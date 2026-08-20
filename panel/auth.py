"""Login del panel: contraseña -> token firmado.

HMAC-SHA256 con la stdlib, sin JWT ni ninguna librería. Para un panel que miran
tres personas, una dependencia más es más superficie que valor.

── Qué protege y qué no ───────────────────────────────────────────────────────

Protege el acceso a las conversaciones de los asesores, que son datos de
clientes: razones sociales, RUCs, montos. No es un sistema de usuarios — hay UNA
contraseña compartida y el token no dice quién sos, solo hasta cuándo vale.

Si algún día hace falta saber quién miró qué, esto se cambia entero. Mientras
tanto, que sea chico y obvio.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from dotenv import load_dotenv
from fastapi import Header, HTTPException

load_dotenv()

PASSWORD = os.getenv("PANEL_PASSWORD", "")
SECRET = os.getenv("PANEL_SECRET", "")
TTL = int(os.getenv("PANEL_TOKEN_TTL", str(12 * 3600)))


def configurado() -> list[str]:
    """Qué falta para que el login sea seguro. Se revisa al arrancar.

    Sin esto, un deploy sin variables arranca con la contraseña vacía y el panel
    queda abierto — el mismo agujero que tenía el webhook viejo, donde un `and`
    con la variable vacía hacía que no validara nada.
    """
    faltan = []
    if not PASSWORD:
        faltan.append("PANEL_PASSWORD")
    if not SECRET:
        faltan.append("PANEL_SECRET")
    return faltan


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _firma(cuerpo: str) -> str:
    return _b64(hmac.new(SECRET.encode(), cuerpo.encode(), hashlib.sha256).digest())


def emitir() -> dict:
    """Un token nuevo. Solo se llama después de validar la contraseña."""
    vence = int(time.time()) + TTL
    cuerpo = _b64(json.dumps({"exp": vence}, separators=(",", ":")).encode())
    return {"token": f"{cuerpo}.{_firma(cuerpo)}", "exp": vence}


def valido(token: str) -> bool:
    try:
        cuerpo, firma = token.split(".", 1)
        # compare_digest y no ==: comparar strings corta en el primer byte
        # distinto, y ese tiempo filtra la firma byte por byte.
        if not hmac.compare_digest(firma, _firma(cuerpo)):
            return False
        return json.loads(_unb64(cuerpo)).get("exp", 0) > time.time()
    except Exception:
        return False


def exigir(authorization: str = Header("")) -> None:
    """Dependencia de FastAPI. Lanza 401 si el Bearer no sirve."""
    if not SECRET or not valido(authorization.replace("Bearer", "").strip()):
        raise HTTPException(status_code=401, detail="no autorizado")


def contrasena_ok(intento: str) -> bool:
    return bool(PASSWORD) and hmac.compare_digest(intento or "", PASSWORD)
