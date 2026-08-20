"""
Cliente para WAHA (WhatsApp HTTP API) — versión Core, imagen devlikeapro/waha.

Mantiene la misma interfaz que shared/kapso.py (send_message / send_document /
send_image_base64) para que el webhook pueda cambiar de proveedor con una sola
variable de entorno (WHATSAPP_PROVIDER=waha|kapso).

Docs: https://waha.devlike.pro/docs/
Endpoint base: http://<host>:3000/api
"""
import os
import base64

import httpx
from dotenv import load_dotenv

load_dotenv()

WAHA_BASE_URL = os.getenv("WAHA_BASE_URL", "http://localhost:3000").rstrip("/")
WAHA_API_KEY  = os.getenv("WAHA_API_KEY", "waha-secret-key-2026")
WAHA_SESSION  = os.getenv("WAHA_SESSION", "default")


def _headers() -> dict:
    return {"X-Api-Key": WAHA_API_KEY, "Content-Type": "application/json"}


def _chat_id(numero: str) -> str:
    """
    Convierte un número a chatId válido para WAHA.
    Si ya tiene @ (ej. '51940351180@c.us' o '111621938671767@lid') lo devuelve tal cual.
    Si es solo dígitos, agrega @c.us (y el prefijo 51 si falta).
    """
    if "@" in numero:
        return numero
    n = numero.lstrip("+")
    if not n.startswith("51"):
        n = "51" + n
    return f"{n}@c.us"


async def resolve_lid_to_phone(lid: str) -> str | None:
    """Traduce un LID de Meta ('<lid>@lid') al número de teléfono ('51...').

    WhatsApp/WAHA entrega para algunos contactos el LID interno de Meta en vez del
    teléfono. WAHA expone el mapeo en:
        GET /api/{session}/lids/{lid}  ->  {"lid": "...@lid", "pn": "51...@c.us"}
    Devuelve solo los dígitos del teléfono, o None si no se pudo resolver.
    """
    lid_id = lid if lid.endswith("@lid") else f"{lid}@lid"
    url = f"{WAHA_BASE_URL}/api/{WAHA_SESSION}/lids/{lid_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 200:
                pn = (resp.json() or {}).get("pn", "") or ""
                tel = pn.split("@")[0].lstrip("+")
                return tel or None
            print(f"[WAHA] lids/{lid_id} -> {resp.status_code}", flush=True)
    except Exception as e:
        print(f"[WAHA] error resolviendo LID {lid_id}: {e}", flush=True)
    return None


class WAHAClient:
    async def send_message(self, numero: str, instance: str, texto: str) -> dict:
        """
        Envía un mensaje de texto.
        `instance` se ignora (compat con la interfaz de KapsoClient).
        """
        body = {
            "session": WAHA_SESSION,
            "chatId": _chat_id(numero),
            "text": texto,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{WAHA_BASE_URL}/api/sendText",
                headers=_headers(),
                json=body,
            )
            if resp.status_code >= 400:
                print(f"[WAHA] error {resp.status_code}: {resp.text}")
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
        """Envía un archivo por URL pública."""
        body = {
            "session": WAHA_SESSION,
            "chatId": _chat_id(numero),
            "file": {"url": url, "filename": filename},
            "caption": caption,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{WAHA_BASE_URL}/api/sendFile",
                headers=_headers(),
                json=body,
            )
            if resp.status_code >= 400:
                print(f"[WAHA] error {resp.status_code}: {resp.text}")
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
        """Envía un documento (PDF) en base64 como archivo adjunto.

        WAHA Core usa /api/sendFile con el contenido crudo en `file.data`."""
        raw_b64 = documento_base64
        if raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        body = {
            "session": WAHA_SESSION,
            "chatId": _chat_id(numero),
            "file": {"mimetype": mime, "filename": filename, "data": raw_b64},
            "caption": caption,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{WAHA_BASE_URL}/api/sendFile",
                headers=_headers(),
                json=body,
            )
            if resp.status_code >= 400:
                print(f"[WAHA] error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def send_image_base64(
        self,
        numero: str,
        instance: str,
        imagen_base64: str,
        caption: str = "",
        filename: str = "imagen.png",
    ) -> dict:
        """
        Envía una imagen en base64.
        WAHA Core acepta data URI en el campo `file.data`.
        """
        # Detectar tipo real desde el filename si no se especificó
        if filename.lower().endswith(".jpg") or filename.lower().endswith(".jpeg"):
            mime = "image/jpeg"
        elif filename.lower().endswith(".png"):
            mime = "image/png"
        else:
            mime = "image/jpeg"
        # WAHA Core usa atob(data) internamente → necesita base64 crudo, sin prefijo data URI
        raw_b64 = imagen_base64
        if raw_b64.startswith("data:"):
            raw_b64 = raw_b64.split(",", 1)[1]
        body = {
            "session": WAHA_SESSION,
            "chatId": _chat_id(numero),
            "file": {"mimetype": mime, "filename": filename, "data": raw_b64},
            "caption": caption,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{WAHA_BASE_URL}/api/sendImage",
                headers=_headers(),
                json=body,
            )
            if resp.status_code >= 400:
                print(f"[WAHA] error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()


waha = WAHAClient()
