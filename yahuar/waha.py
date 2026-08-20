"""Lo único que este servicio necesita de WhatsApp: escribirle a Yahuar.

── Por qué es un archivo de diez líneas y no el `waha.py` de recepción ────────

Porque recepción manda texto, imágenes y PDFs a cualquier destinatario, y este
servicio no tiene que poder hacer nada de eso. Su única salida legítima es un
mensaje de texto al número de Yahuar.

No es una regla escrita en un comentario — es que no existe la función. Este
módulo no sabe mandar una imagen ni acepta un destinatario: `enviar()` toma un
texto y va a `relay.CHAT_ID`, punto.

Importa porque el resultado de una placa incluye la foto de una tarjeta
vehicular con el nombre del propietario. Ese dato vuelve por Redis al worker,
que lo manda por el camino normal (`media_pendiente`). Si este servicio pudiera
escribirle a cualquiera, un bug de destinatario mandaría la tarjeta de un
vehículo al chat equivocado.
"""
import logging
import os

import httpx
from dotenv import load_dotenv

from yahuar import relay

load_dotenv()

BASE_URL = os.getenv("WAHA_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("WAHA_API_KEY", "")
SESION = os.getenv("WAHA_SESSION", "default")

TIMEOUT = 15.0


async def enviar(texto: str) -> bool:
    """Le manda un texto a Yahuar. El destinatario NO es parametrizable."""
    if not texto:
        return False
    if not BASE_URL:
        logging.error("[yahuar/waha] falta WAHA_BASE_URL")
        return False

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(
                f"{BASE_URL}/api/sendText",
                headers={"X-Api-Key": API_KEY, "Content-Type": "application/json"},
                json={"session": SESION, "chatId": relay.CHAT_ID, "text": texto},
            )
            if r.status_code >= 400:
                logging.error(f"[yahuar/waha] sendText -> {r.status_code}: {r.text[:200]}")
                return False
            return True
    except Exception as e:
        logging.error(f"[yahuar/waha] sendText falló: {e}")
        return False
