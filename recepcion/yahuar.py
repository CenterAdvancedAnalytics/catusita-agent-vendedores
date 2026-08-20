"""Reconocer a Yahuar y apartarlo del flujo normal.

Yahuar es el relay de placas: un número de WhatsApp externo al que el servicio
`yahuar/` le manda una placa y que contesta con los datos del vehículo y la foto
de la tarjeta.

── Por qué esto existe ────────────────────────────────────────────────────────

Porque sus respuestas entran por el MISMO webhook que los mensajes de la gente.
Sin este archivo, el router las trata como a cualquier desconocido: el padrón no
tiene su número, así que caen en `POR_DEFECTO = "clientes"` y termina el agente
de clientes contestándole a Yahuar como si fuera alguien preguntando por un
repuesto — mientras el vendedor que pidió la placa no recibe nada.

No es hipotético: pasa el día que el webhook apunte a recepción.

── Cómo se lo reconoce ────────────────────────────────────────────────────────

Por tres identidades, y hacen falta las tres:

    YAHUAR_NUMBER   el teléfono, fijo          51977504279
    YAHUAR_LID      su LID, si se conoce       (variable de entorno, opcional)
    yahuar:lid      el LID aprendido           (Redis, sin expiración)

WhatsApp identifica a los contactos con un LID que NO es el teléfono y que puede
cambiar. `resolve_lid_to_phone` a veces lo traduce y a veces no, así que no
alcanza con comparar contra el número.

El aprendizaje lo hace el servicio `yahuar/`, que es el único que sabe si hay una
placa en vuelo: si llega un mensaje de un número desconocido justo mientras
espera, ese es Yahuar. Acá solo se lee lo aprendido.

── Lo que NO hace ─────────────────────────────────────────────────────────────

No procesa el mensaje. Lo empuja a `yahuar:entrantes` y se olvida. El debounce,
el filtro de sus saludos y la visión sobre la foto son del servicio, que es quien
sabe qué placa está esperando.

Tampoco pasa por el acumulador ni toma el lock de conversación: Yahuar no es un
usuario con una conversación, es un servicio contestando.
"""
import json
import logging
import os

from recepcion import redis as redis_mod

NUMERO = os.getenv("YAHUAR_NUMBER", "51977504279")
LID_ENV = os.getenv("YAHUAR_LID", "")

CLAVE_LID = "yahuar:lid"          # el LID aprendido, sin expiración
CLAVE_ESPERANDO = "yahuar:esperando"   # qué placa está en vuelo (la pone el servicio)
COLA_ENTRANTES = "yahuar:entrantes"

# Si el servicio `yahuar` está caído, sus mensajes se acumularían para siempre.
# Con un tope, la cola vieja se descarta sola en vez de crecer sin fin.
MAX_ENTRANTES = 200


def _solo_digitos(valor: str) -> str:
    """'51977504279@c.us' -> '51977504279'. También sirve para los LID."""
    return "".join(c for c in (valor or "") if c.isdigit())


async def identidades() -> set[str]:
    """Todos los identificadores conocidos de Yahuar, en dígitos."""
    ids = {_solo_digitos(NUMERO)}
    if LID_ENV:
        ids.add(_solo_digitos(LID_ENV))
    try:
        r = await redis_mod.get()
        if aprendido := await r.get(CLAVE_LID):
            ids.add(_solo_digitos(aprendido))
    except Exception as e:
        # Sin Redis no se puede leer el LID aprendido, pero el número fijo sigue
        # sirviendo. Se avisa y se sigue: fallar acá cortaría TODO el webhook.
        logging.error(f"[yahuar] no se pudo leer el LID aprendido: {e}")
    return {i for i in ids if i}


async def es_yahuar(*candidatos: str) -> bool:
    """¿Alguno de estos identificadores es Yahuar?

    Se le pasan varios a propósito: el remitente crudo y el ya traducido de LID
    a teléfono. Si la traducción funcionó, coincide por número; si no, coincide
    por LID. Con uno solo se escapa la mitad de los casos.
    """
    conocidos = await identidades()
    return any(_solo_digitos(c) in conocidos for c in candidatos if c)


async def aprender_si_corresponde(payload: dict, remitente: str) -> bool:
    """¿Este desconocido es Yahuar con un LID nuevo? Si sí, lo guarda.

    ── El huevo y la gallina ──────────────────────────────────────────────────

    `es_yahuar` solo reconoce identidades ya conocidas. El día que WhatsApp le
    cambie el LID a Yahuar, sus respuestas dejan de reconocerse y caen al agente
    de clientes — que es exactamente lo que este módulo existe para evitar.

    Así que hay que poder reconocerlo la PRIMERA vez, sin conocerlo.

    ── Por qué no alcanza con «hay una placa en vuelo» ────────────────────────

    El stack viejo daba por Yahuar a cualquier número desconocido que escribiera
    mientras había una consulta pendiente. Es una ventana de un minuto en la que
    un cliente nuevo escribiendo por primera vez queda marcado como Yahuar —
    para siempre, porque el LID se guarda sin expiración— y su mensaje se lo
    come el relay.

    Acá se exige, además de la ventana, que el mensaje SE PAREZCA a uno de
    Yahuar: que nombre la placa que se está esperando, que sea uno de sus
    saludos, o que traiga una foto. Un cliente que escribe «hola» no cumple
    ninguna.
    """
    r = await redis_mod.get()
    placa = await r.get(CLAVE_ESPERANDO)
    if not placa:
        return False

    texto = (payload.get("body") or "").lower()
    placa_norm = "".join(c for c in placa.upper() if c.isalnum())

    parece = (
        (placa_norm and placa_norm in "".join(c for c in texto.upper() if c.isalnum()))
        or any(f in texto for f in _SALUDOS)
        or bool(payload.get("hasMedia"))
    )
    if not parece:
        return False

    await r.set(CLAVE_LID, _solo_digitos(remitente))
    logging.info(f"[yahuar] LID nuevo aprendido: {remitente!r} (esperaba {placa})")
    return True


# Lo mínimo para reconocer su forma de hablar sin importar el módulo del
# servicio: recepción no debe depender de `yahuar/`, que es otro contenedor.
_SALUDOS = (
    "soy yahuar", "qué información buscas", "que informacion buscas",
    "en qué te puedo ayudar", "en que te puedo ayudar", "puedo ayudarte",
)


async def encolar(payload: dict, remitente: str) -> None:
    """Manda el mensaje al servicio `yahuar` y no espera nada.

    El `remitente` viaja aparte del payload porque el servicio lo necesita para
    aprender el LID, y `payload["from"]` puede venir en cualquiera de las dos
    formas.
    """
    r = await redis_mod.get()
    sobre = json.dumps({"remitente": remitente, "payload": payload},
                       ensure_ascii=False)
    await r.lpush(COLA_ENTRANTES, sobre)
    await r.ltrim(COLA_ENTRANTES, 0, MAX_ENTRANTES - 1)
