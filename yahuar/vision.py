"""Volcar a texto la foto de la tarjeta vehicular.

Yahuar manda los datos del vehículo como IMAGEN — una foto de la tarjeta de
identificación—, no como texto. Sin este paso lo único que se le puede dar al
vendedor es «acá está la foto», y entonces el agente no puede usar la marca ni
el modelo para buscarle repuestos, que es para lo que preguntó la placa.
"""
import logging
import os

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from yahuar import relay

load_dotenv()

# El modelo más chico alcanza: es transcribir campos de un documento, no razonar.
MODELO = os.getenv("YAHUAR_MODELO_VISION", "claude-haiku-4-5-20251001")
MAX_TOKENS = 1024

# Lo que la API acepta como imagen. Un mimetype fuera de esta lista no se manda:
# hacerlo devuelve un 400 y se pierde el turno entero por una foto.
FORMATOS = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_cliente: AsyncAnthropic | None = None


def _api() -> AsyncAnthropic:
    global _cliente
    if _cliente is None:
        _cliente = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    return _cliente


async def leer_tarjeta(imagen_b64: str, mime: str = "image/jpeg") -> str:
    """Devuelve los datos de la tarjeta como texto. Cadena vacía si no se pudo.

    No lanza: si la visión falla, el servicio igual tiene que devolver lo que
    Yahuar haya mandado por texto y la foto. Media respuesta es mejor que un
    error.
    """
    limpio = (mime or "").split(";")[0].strip()
    if limpio not in FORMATOS:
        logging.warning(f"[yahuar/vision] formato no soportado: {limpio!r}")
        return ""

    try:
        r = await _api().messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": limpio, "data": imagen_b64}},
                    {"type": "text", "text": relay.INSTRUCCION_TARJETA},
                ],
            }],
        )
    except Exception as e:
        logging.error(f"[yahuar/vision] falló: {e}")
        return ""

    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
