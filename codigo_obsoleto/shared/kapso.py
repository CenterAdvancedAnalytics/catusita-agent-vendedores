"""
Cliente para Kapso (WhatsApp Cloud API oficial vía Meta).

Cliente de WhatsApp vía Kapso (Cloud API oficial de Meta)
(send_message / send_document) para que el resto del código no cambie.

Docs: https://docs.kapso.ai
Endpoint base: https://api.kapso.ai/meta/whatsapp/v24.0/{phone_number_id}/messages
"""
import os
import hmac
import base64
import hashlib

import httpx
from dotenv import load_dotenv

load_dotenv()

KAPSO_BASE_URL = os.getenv(
    "KAPSO_BASE_URL", "https://api.kapso.ai/meta/whatsapp/v24.0"
).rstrip("/")
KAPSO_API_KEY = os.getenv("KAPSO_API_KEY", "")
KAPSO_PHONE_NUMBER_ID = os.getenv("KAPSO_PHONE_NUMBER_ID", "")
KAPSO_WEBHOOK_SECRET = os.getenv("KAPSO_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# Verificación de firma del webhook (HMAC-SHA256 del body crudo)
# ---------------------------------------------------------------------------
def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Verifica que `X-Webhook-Signature` coincida con HMAC-SHA256(secret, body).

    Si no está configurado el secret, deja pasar (modo dev) pero loguea aviso.
    """
    if not KAPSO_WEBHOOK_SECRET:
        print("[KAPSO] AVISO: KAPSO_WEBHOOK_SECRET no configurado; firma no se valida")
        return True

    if not signature_header:
        return False

    expected = hmac.new(
        KAPSO_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header.strip())


def _normalizar_numero(numero: str) -> str:
    """
    Quita prefijos (+), sufijos (@s.whatsapp.net) y agrega 51 si falta.
    """
    n = (numero or "").lstrip("+").split("@")[0]
    if n and not n.startswith("51"):
        n = "51" + n
    return n


# ---------------------------------------------------------------------------
# Cliente para enviar mensajes
# ---------------------------------------------------------------------------
class KapsoClient:
    def __init__(self):
        self._headers = {
            "X-API-Key": KAPSO_API_KEY,
            "Content-Type": "application/json",
        }

    def _send_url(self, phone_number_id: str | None = None) -> str:
        pnid = phone_number_id or KAPSO_PHONE_NUMBER_ID
        return f"{KAPSO_BASE_URL}/{pnid}/messages"

    def _media_url(self, phone_number_id: str | None = None) -> str:
        pnid = phone_number_id or KAPSO_PHONE_NUMBER_ID
        return f"{KAPSO_BASE_URL}/{pnid}/media"

    async def send_message(self, numero: str, instance: str, texto: str) -> dict:
        """
        Envía un mensaje de texto.

        Args:
            numero: destinatario (con o sin 51 al inicio, con o sin @s.whatsapp.net).
            instance: ignorado (Kapso enruta por phone_number_id).
            texto: contenido a enviar.
        """
        numero_clean = _normalizar_numero(numero)
        body = {
            "messaging_product": "whatsapp",
            "to": numero_clean,
            "type": "text",
            "text": {"body": texto},
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self._send_url(), headers=self._headers, json=body)
            if resp.status_code >= 400:
                # Loguear cuerpo del error antes de raise_for_status
                print(f"[KAPSO] error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def send_document(
        self,
        numero: str,
        instance: str,
        url: str,
        filename: str,
        caption: str = "",
    ) -> dict:
        """Envía un documento (PDF, XML, etc) por URL pública."""
        numero_clean = _normalizar_numero(numero)
        doc_payload: dict = {"link": url, "filename": filename}
        if caption:
            doc_payload["caption"] = caption
        body = {
            "messaging_product": "whatsapp",
            "to": numero_clean,
            "type": "document",
            "document": doc_payload,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(self._send_url(), headers=self._headers, json=body)
            if resp.status_code >= 400:
                print(f"[KAPSO] error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def _upload_media(
        self,
        contenido: bytes,
        phone_number_id: str | None = None,
        filename: str = "imagen.png",
        mime: str = "image/png",
    ) -> str:
        """Sube un archivo a Kapso/Meta y devuelve el media_id.

        El upload es multipart/form-data (NO json), así que NO se reusa
        self._headers (que fija Content-Type: application/json).
        """
        files = {"file": (filename, contenido, mime)}
        data = {"messaging_product": "whatsapp"}
        headers = {"X-API-Key": KAPSO_API_KEY}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                self._media_url(phone_number_id),
                headers=headers,
                files=files,
                data=data,
            )
            if resp.status_code >= 400:
                print(f"[KAPSO] error upload media {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()["id"]

    async def send_image_base64(
        self,
        numero: str,
        instance: str,
        imagen_base64: str,
        caption: str = "",
        filename: str = "imagen.png",
    ) -> dict:
        """Envía una imagen a partir de su contenido en base64.

        Sube el PNG como media (obtiene media_id) y luego manda un mensaje
        tipo `image` referenciando ese id. `instance` se usa como
        phone_number_id (compat con la interfaz existente).
        """
        numero_clean = _normalizar_numero(numero)
        raw = base64.b64decode(imagen_base64)
        media_id = await self._upload_media(raw, phone_number_id=instance, filename=filename)

        image_payload: dict = {"id": media_id}
        if caption:
            image_payload["caption"] = caption
        body = {
            "messaging_product": "whatsapp",
            "to": numero_clean,
            "type": "image",
            "image": image_payload,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self._send_url(instance), headers=self._headers, json=body
            )
            if resp.status_code >= 400:
                print(f"[KAPSO] error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def send_document_base64(
        self,
        numero: str,
        instance: str,
        documento_base64: str,
        filename: str = "documento.pdf",
        caption: str = "",
        mime: str = "application/pdf",
    ) -> dict:
        """Envía un documento (PDF) a partir de su contenido en base64.

        Sube el archivo como media (obtiene media_id) y luego manda un mensaje
        tipo `document` referenciando ese id."""
        numero_clean = _normalizar_numero(numero)
        raw = base64.b64decode(documento_base64)
        media_id = await self._upload_media(
            raw, phone_number_id=instance, filename=filename, mime=mime
        )
        doc_payload: dict = {"id": media_id, "filename": filename}
        if caption:
            doc_payload["caption"] = caption
        body = {
            "messaging_product": "whatsapp",
            "to": numero_clean,
            "type": "document",
            "document": doc_payload,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                self._send_url(instance), headers=self._headers, json=body
            )
            if resp.status_code >= 400:
                print(f"[KAPSO] error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()


kapso = KapsoClient()
