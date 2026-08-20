import json
import os
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
TTL_SECONDS = 7200  # 2 horas
MAX_MESSAGES = 20   # 10 turnos (user + assistant)

_redis = None


async def _get_redis():
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def _key(numero: str) -> str:
    return f"conversation:{numero}"


async def get_history(numero: str) -> list:
    try:
        r = await _get_redis()
        raw = await r.get(_key(numero))
        return json.loads(raw) if raw else []
    except Exception:
        return []


async def save_message(numero: str, rol: str, contenido: str):
    try:
        r = await _get_redis()
        historial = await get_history(numero)
        historial.append({"role": rol, "content": contenido})
        if len(historial) > MAX_MESSAGES:
            historial = historial[-MAX_MESSAGES:]
        await r.setex(_key(numero), TTL_SECONDS, json.dumps(historial, ensure_ascii=False))
    except Exception:
        pass


async def clear_history(numero: str):
    try:
        r = await _get_redis()
        await r.delete(_key(numero))
    except Exception:
        pass


async def list_conversations() -> list:
    """Lista todas las conversaciones activas en Redis (para el panel de chats).

    Devuelve [{numero, mensajes: [{role, content}], ttl}] recorriendo las keys
    conversation:*. No incluye claves internas (idemp:, yahuar:).
    """
    chats = []
    try:
        r = await _get_redis()
        async for key in r.scan_iter(match="conversation:*", count=200):
            numero = key.split("conversation:", 1)[1]
            raw = await r.get(key)
            if not raw:
                continue
            try:
                mensajes = json.loads(raw)
            except Exception:
                mensajes = []
            ttl = await r.ttl(key)
            chats.append({"numero": numero, "mensajes": mensajes, "ttl": ttl})
    except Exception:
        pass
    return chats


async def ya_procesado(idempotency_key: str, ttl: int = 600) -> bool:
    """Idempotencia: True si esta key ya se procesó (y entonces hay que ignorarla).

    Usa SET NX en Redis: la primera vez setea la marca y devuelve False
    (procesar); cualquier reintento de Kapso con la misma key encuentra la
    marca ya puesta y devuelve True (ignorar). Evita que un webhook reenviado
    dispare múltiples ejecuciones del agente (spam de respuestas).
    """
    if not idempotency_key:
        return False
    try:
        r = await _get_redis()
        seteado = await r.set(f"idemp:{idempotency_key}", "1", ex=ttl, nx=True)
        return not seteado   # si no se pudo setear, ya existía → ya procesado
    except Exception:
        return False  # ante fallo de Redis, no bloquear (mejor procesar que perder)
