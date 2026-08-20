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
import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.types import Command

# Cuánto se espera la respuesta del relay antes de darse por vencido. El relay
# tarda 30-60 s; más allá de 90 no está lento, está caído.
ESPERA_MAX = 90


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

    TODO: encolar en `yahuar:solicitudes` y esperar en `yahuar:resultado:{placa}`.
    Depende de que exista el servicio de Yahuar.
    """
    # ── Por qué devuelve un error y no `raise NotImplementedError` ────────────
    #
    # Porque la excepción reventaba el turno entero. Medido: «¿Qué auto es la
    # placa F9N562?» terminaba en NotImplementedError, el worker lo convertía en
    # un Resultado con ok=False, y el asesor recibía «no pude procesar tu
    # mensaje» — sin ninguna pista de por qué.
    #
    # Y no era un caso raro: la consulta de placa está prometida en
    # docs/guia_uso_vendedor.md y hoy funciona en el stack viejo. Apenas se
    # corte el webhook hacia recepción, el 100% de las placas fallaría así.
    #
    # Un área que todavía no puede hacer su trabajo tiene que DECIRLO. El
    # orquestador con esto arma una respuesta honesta; con una excepción no
    # tiene nada.
    return _responder({
        "error": "NO_DISPONIBLE",
        "mensaje": (
            "La consulta de placas todavía no está conectada en esta versión: "
            "depende del servicio de Yahuar, que aún no está desplegado. "
            "Decíselo al asesor tal cual y no intentes deducir el vehículo por "
            "otro lado. Si te da la marca y el modelo a mano, con eso sí podés "
            "buscarle repuestos en el área productos."
        ),
    }, tool_call_id)


TOOLS = [consultar_placa]
