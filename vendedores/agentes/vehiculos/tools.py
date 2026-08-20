"""Tool de `vehiculos` (vendedores): qué auto es una placa.

SUNARP SE ELIMINÓ. No servía: 20-60 s por consulta, se colgaba seguido, y para
sacar marca/modelo/año había que pasarle la foto de la tarjeta a un modelo con
visión porque no devolvía esos campos sueltos. Todo ese camino se fue.

Queda Yahuar, y NO es una API: es un relay por WhatsApp. Se le manda la placa a
un número externo y se espera su respuesta.

── Por qué esta área no lo hace ella misma ────────────────────────────────────

Porque el relay es UN RECURSO FÍSICO ÚNICO. Es una sola conversación de WhatsApp
con un número externo: si tres contenedores le escriben a la vez, las respuestas
vuelven por el mismo hilo y no hay forma confiable de saber cuál es de quién.

No es que convenga tener un solo dueño — es que no puede haber más de uno.

Por eso Yahuar sale a su propio servicio, fuera de los tres multiagentes:

    vendedores/vehiculos ──┐
                           ├──► cola "yahuar:solicitudes" ──► servicio yahuar
    clientes/vehiculos ────┘                                       │
                                                         (relay, 30-60 s,
                                                          UNA a la vez)
                                                                   │
       la tool espera en  "yahuar:resultado:{placa}"  ◄─────────────┘

Desde acá es una llamada normal: encolar y esperar con timeout. Bloquea a ESTA
conversación, no al worker — asyncio sigue atendiendo las demás mientras tanto.

Y el estado del relay (qué LID es Yahuar, qué placa está pendiente, el
acumulador de sus respuestas) deja de estar copiado en los tres multiagentes,
como está hoy en `shared/yahuar.py` por herencia del monolito.
"""
import asyncio
import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.types import Command

from vendedores.plataforma_vendedores import redis as redis_mod

# Cuánto se espera la respuesta del relay antes de darse por vencido. El relay
# tarda 30-60 s; más allá de 90 no está lento, está caído.
ESPERA_MAX = 90

# Cada cuánto se mira si ya está el resultado. Un segundo no agrega latencia
# perceptible sobre una espera de 30-60 s y no castiga a Redis.
SONDEO = 1.0

COLA_SOLICITUDES = "yahuar:solicitudes"
CLAVE_RESULTADO = "yahuar:resultado:"


def _responder(resultado: dict, tool_call_id: str, extra: dict | None = None) -> Command:
    update: dict = {"messages": [ToolMessage(
        content=json.dumps(resultado, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id)]}
    if extra:
        update.update(extra)
    return Command(update=update)


@tool
async def consultar_placa(
    placa: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Qué vehículo es una placa peruana: marca, modelo, año, VIN y motor.

    TARDA 30-60 SEGUNDOS. Llamala UNA sola vez por turno — llamarla de nuevo no
    la acelera, encola otra consulta detrás de la primera.

    Los datos vuelven en `datos_vehiculo_texto`: presentalos por escrito. Si
    `tiene_imagen` es true, la foto de la tarjeta ya se mandó sola al chat.

    ── Cómo espera ────────────────────────────────────────────────────────────

    Encola y hace polling sobre la clave del resultado. Bloquea a ESTA
    conversación, no al worker: asyncio sigue atendiendo las demás mientras
    tanto.

    No usa BRPOP porque el resultado NO es una cola: es una clave con TTL. Si
    esta tool se cayera entre el encolar y el leer, un BRPOP se comería el
    resultado y nadie lo vería nunca; con una clave, el servicio ya lo dejó
    escrito y el reintento lo encuentra.
    """
    normalizada = _normalizar(placa)
    if not normalizada:
        return _responder({
            "error": "PLACA_INVALIDA",
            "mensaje": "Esa placa no se entiende. Pedile al asesor que la repita.",
        }, tool_call_id)

    r = await redis_mod.get()
    clave = CLAVE_RESULTADO + normalizada

    # Puede haber quedado el resultado de una consulta anterior de la misma
    # placa. Se limpia: contestar con un dato de hace media hora sin decirlo es
    # peor que volver a consultar.
    await r.delete(clave)

    await r.lpush(COLA_SOLICITUDES, json.dumps({"placa": normalizada},
                                               ensure_ascii=False))

    espera = 0.0
    while espera < ESPERA_MAX:
        await asyncio.sleep(SONDEO)
        espera += SONDEO
        if crudo := await r.get(clave):
            await r.delete(clave)
            resultado = json.loads(crudo)
            return _entregar(resultado, tool_call_id)

    return _responder({
        "error": "TIEMPO_AGOTADO",
        "mensaje": (
            f"El servicio de placas no respondió por {normalizada} en "
            f"{int(ESPERA_MAX)} segundos. Decíselo al asesor y ofrecele "
            "seguir con la marca y el modelo si los tiene a mano."
        ),
    }, tool_call_id)


def _normalizar(placa: str) -> str:
    """'f9n-562' -> 'F9N562'. Tiene que dar igual que en el servicio."""
    return "".join(c for c in (placa or "").upper() if c.isalnum())


def _entregar(resultado: dict, tool_call_id: str) -> Command:
    """El resultado del servicio, y la foto por el camino normal de los adjuntos.

    La imagen NO la manda el servicio de Yahuar: viaja por Redis hasta acá y
    sale como `media_pendiente`, igual que la foto de un producto. Así el
    servicio solo puede escribirle a UN número —el de Yahuar— y una tarjeta
    vehicular con el nombre del propietario no puede terminar en el chat
    equivocado por un bug de destinatario.
    """
    if resultado.get("error"):
        return _responder(resultado, tool_call_id)

    media = []
    if img := resultado.get("imagen_base64"):
        placa = resultado.get("placa", "")
        ext = (resultado.get("imagen_mime") or "image/jpeg").split("/")[-1]
        media = [{
            "imagen_base64": img,
            "caption": f"Tarjeta vehicular — {placa}",
            "filename": f"placa_{placa}.{ext.replace('jpeg', 'jpg')}",
        }]

    # El base64 no vuelve al modelo: son cientos de miles de caracteres que
    # gastaría en contexto sin poder hacer nada con ellos.
    return _responder(
        {k: v for k, v in resultado.items() if k != "imagen_base64"},
        tool_call_id,
        {"media_pendiente": media} if media else None,
    )


TOOLS = [consultar_placa]
