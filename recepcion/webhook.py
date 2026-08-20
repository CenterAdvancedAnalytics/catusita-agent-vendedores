"""Entrada del sistema. Recibe de WAHA, decide a quién y encola.

    WAHA ──POST /webhook/waha──► valida ──► ¿ya lo vi? ──► acumula
                                                              │
                                          (VENTANA s de silencio)
                                                              │
                                     router ──► cola del multiagente

Lo que este archivo NO hace: correr el agente. Contesta 200 y encola. Todo lo
demás pasa en otro proceso.

── Los tres agujeros que cierra ───────────────────────────────────────────────

El webhook anterior (`webhooks/whatsapp.py`) tenía tres, y los tres eran del
tipo que no da error:

  1. NO VALIDABA. `if expected and waha_key != expected` — con la variable
     vacía, el `and` corta y no valida nada. `WAHA_WEBHOOK_TOKEN` no estaba
     puesta en Railway, así que cualquiera con la URL podía disparar corridas
     del agente contra la API key de Anthropic. Acá la falta de token es un
     error de arranque, no un permiso abierto.

  2. NO ERA IDEMPOTENTE. La ruta de WAHA no tenía el `ya_procesado` que sí
     tenía la de Kapso. WAHA reintenta si tarda —y el agente tarda segundos—
     así que un mensaje se procesaba dos veces y el asesor recibía dos
     respuestas.

  3. TENÍA EL CANAL CLAVADO. `agente_tipo = "vendedor"`: todo el que escribía
     entraba como asesor, con acceso a precio neto y cartera.

── Por qué contesta antes de procesar ─────────────────────────────────────────

WAHA espera un 200 rápido. Si se le contesta después de correr el agente
—segundos— lo da por caído, reintenta, y cada reintento dispara otra corrida.
El acumulador se encarga de que los fragmentos se junten en un solo turno.
"""
import json
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request

from recepcion import acumulador, router as ruteo, waha, yahuar

router_webhook = APIRouter()

TOKEN = os.getenv("WAHA_WEBHOOK_TOKEN", "")

# Eventos que traen un mensaje entrante. El resto (acks, estado de sesión,
# presencia) se ignoran sin ruido: son la mayoría del tráfico.
EVENTOS_MENSAJE = {"message", "message.any"}


def verificar_configuracion() -> None:
    """Se llama al arrancar. Sin token, no se levanta.

    Es la diferencia entre «recomendado» y obligatorio. La versión anterior lo
    tenía como opcional y por eso estuvo meses sin ninguna validación.
    """
    if not TOKEN:
        raise RuntimeError(
            "Falta WAHA_WEBHOOK_TOKEN. Sin eso el webhook acepta cualquier POST "
            "de internet y cada uno dispara una corrida del agente."
        )


@router_webhook.post("/waha")
async def recibir(request: Request, x_api_key: str = Header("")) -> dict:
    if x_api_key != TOKEN:
        logging.warning("[webhook] token inválido")
        raise HTTPException(status_code=401, detail="token inválido")

    try:
        evento = json.loads(await request.body())
    except Exception:
        raise HTTPException(status_code=400, detail="json inválido")

    if evento.get("event") not in EVENTOS_MENSAJE:
        return {"status": "ignorado", "motivo": "no es un mensaje"}

    payload = evento.get("payload") or {}

    if payload.get("fromMe"):
        return {"status": "ignorado", "motivo": "lo mandamos nosotros"}

    # Idempotencia ANTES de acumular. WAHA reintenta y sin esto el mismo
    # fragmento entra dos veces al turno.
    if await acumulador.ya_visto(payload.get("id", "")):
        return {"status": "ignorado", "motivo": "duplicado"}

    remitente = payload.get("from", "")
    session = evento.get("session", "")

    # Un LID no es un teléfono: hay que preguntarle a WAHA cuál es. Si falla, el
    # número queda sin resolver y el router lo manda a `clientes`.
    #
    # Se consulta contra la sesión por la que entró, que es la que tiene el
    # mapeo: un LID solo existe dentro de la sesión que lo vio.
    # `payload["from"]` queda intacto y es lo que se usa para responder: si el
    # contacto se presentó como LID, hay que contestarle al LID.
    crudo = remitente
    if "@lid" in remitente:
        if tel := await waha.resolve_lid_to_phone(remitente, session):
            remitente = tel

    # ── Yahuar sale del flujo ANTES del padrón ───────────────────────────────
    #
    # Es el relay de placas: un servicio contestando, no una persona escribiendo.
    # Su número no está en ningún padrón, así que si llegara acá el router lo
    # mandaría a `clientes` y el agente de clientes le contestaría a Yahuar
    # mientras el vendedor que pidió la placa se queda esperando.
    #
    # Se le pasan las dos formas del remitente: si la traducción de LID funcionó
    # coincide por teléfono, y si no, por LID.
    if await yahuar.es_yahuar(crudo, remitente):
        await yahuar.encolar(payload, crudo)
        logging.info(f"[yahuar] respuesta del relay ({crudo}) -> yahuar:entrantes")
        return {"status": "aceptado", "destino": "yahuar"}

    # Y si es Yahuar con un LID que todavía no conocíamos: se aprende una vez y
    # a partir de ahí entra por la rama de arriba. Ver la nota en `yahuar.py`
    # sobre por qué no alcanza con «hay una placa en vuelo».
    if await yahuar.aprender_si_corresponde(payload, crudo):
        await yahuar.encolar(payload, crudo)
        return {"status": "aceptado", "destino": "yahuar", "lid": "aprendido"}

    destino = await ruteo.resolver(remitente, session)
    logging.info(
        f"[router] {destino.numero or remitente} · sesión={session!r} "
        f"-> {destino.multiagente} ({destino.motivo})"
    )

    espera = await acumulador.acumular(destino.multiagente, destino.numero, payload)

    return {"status": "aceptado", "multiagente": destino.multiagente, "espera_s": espera}
