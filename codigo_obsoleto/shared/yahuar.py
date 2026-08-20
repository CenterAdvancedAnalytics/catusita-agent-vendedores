"""
Relay de consulta de placas vía Yahuar WhatsApp (51977504279).

Flujo:
1. consultar_placa(placa, from_field) → envía la placa a Yahuar + guarda en Redis
   quién está esperando la respuesta.
2. El webhook detecta que llegó un mensaje de Yahuar, llama a get_pendiente()
   y reenvía el texto + foto al usuario original.
"""
import json
import os
import time

from orchestrator.context import _get_redis

YAHUAR_NUMBER    = os.getenv("YAHUAR_NUMBER", "51977504279")
YAHUAR_CHAT_ID   = f"{YAHUAR_NUMBER}@c.us"
PENDING_KEY      = "yahuar:pendiente"
PENDING_TTL      = 180   # 3 min
YAHUAR_LID_KEY   = "yahuar:lid"     # LID aprendido automáticamente
RELAY_DEST_KEY   = "yahuar:relay"   # destino activo mientras llegan follow-ups
RELAY_DEST_TTL   = 60               # ventana de relay
ACUM_KEY         = "yahuar:acum"    # lista de mensajes acumulados de Yahuar
ACUM_TS_KEY      = "yahuar:acum_ts" # timestamp del último mensaje recibido
ACUM_TTL         = 60               # TTL del acumulador
DEBOUNCE_SECS    = 7                # segundos sin actividad antes de procesar
IMG_DONE_KEY     = "yahuar:imgdone" # flag: acumulador ya procesado


async def consultar_placa(placa: str, from_field: str) -> None:
    """Guarda en Redis quién preguntó y envía la placa a Yahuar."""
    from shared import waha as waha_mod   # import tardío para evitar circular
    r = await _get_redis()
    data = json.dumps(
        {"from_field": from_field, "placa": placa.upper(), "ts": time.time()},
        ensure_ascii=False,
    )
    await r.setex(PENDING_KEY, PENDING_TTL, data)
    # Mandamos la placa con contexto para que Yahuar no pida aclaración
    await waha_mod.waha.send_message(YAHUAR_CHAT_ID, "", f"Placa vehicular: {placa.upper()}")


async def peek_pendiente() -> dict | None:
    """Lee la consulta pendiente SIN eliminarla."""
    r = await _get_redis()
    raw = await r.get(PENDING_KEY)
    return json.loads(raw) if raw else None


async def get_yahuar_lid() -> str | None:
    """Devuelve el LID aprendido de Yahuar, o None si aún no se conoce."""
    r = await _get_redis()
    val = await r.get(YAHUAR_LID_KEY)
    return val.decode() if isinstance(val, bytes) else val


async def save_yahuar_lid(numero: str) -> None:
    """Guarda el LID de Yahuar en Redis sin expiración (aprendizaje permanente)."""
    r = await _get_redis()
    await r.set(YAHUAR_LID_KEY, numero)
    print(f"[YAHUAR] LID aprendido y guardado: {numero!r}", flush=True)


async def open_relay(from_field: str, placa: str = "") -> None:
    """Abre ventana de relay: guarda destino + placa durante 60s para follow-ups."""
    r    = await _get_redis()
    data = json.dumps({"from_field": from_field, "placa": placa})
    await r.setex(RELAY_DEST_KEY, RELAY_DEST_TTL, data)


async def get_relay_dest() -> tuple[str, str] | None:
    """Devuelve (from_field, placa) del relay activo, o None si expiró."""
    r   = await _get_redis()
    val = await r.get(RELAY_DEST_KEY)
    if not val:
        return None
    raw = val.decode() if isinstance(val, bytes) else val
    try:
        d = json.loads(raw)
        return d["from_field"], d.get("placa", "")
    except Exception:
        return raw, ""   # compatibilidad con formato antiguo


async def acumular_mensaje(payload: dict) -> float:
    """
    Agrega el payload de Yahuar al acumulador Redis y actualiza el timestamp.
    Devuelve el timestamp guardado (para el debounce en el caller).
    """
    r   = await _get_redis()
    ts  = time.time()
    raw = json.dumps(payload, ensure_ascii=False)
    await r.rpush(ACUM_KEY, raw)
    await r.expire(ACUM_KEY, ACUM_TTL)
    await r.set(ACUM_TS_KEY, str(ts), ex=ACUM_TTL)
    return ts


async def get_ultimo_ts() -> float:
    r   = await _get_redis()
    val = await r.get(ACUM_TS_KEY)
    return float(val) if val else 0.0


async def get_y_limpiar_acumulador() -> list[dict]:
    """Obtiene todos los mensajes acumulados y limpia las claves."""
    r    = await _get_redis()
    raws = await r.lrange(ACUM_KEY, 0, -1)
    await r.delete(ACUM_KEY, ACUM_TS_KEY)
    await r.setex(IMG_DONE_KEY, 30, "1")   # evita que otro task reprocese
    return [json.loads(x) for x in raws]


async def acumulador_ya_procesado() -> bool:
    r = await _get_redis()
    return bool(await r.get(IMG_DONE_KEY))


async def get_pendiente() -> dict | None:
    """
    Obtiene y elimina la consulta pendiente de Redis.
    Retorna {from_field, placa, ts} o None si no hay ninguna.
    """
    r = await _get_redis()
    raw = await r.get(PENDING_KEY)
    if raw:
        await r.delete(PENDING_KEY)
        return json.loads(raw)
    return None


# ─── Modo bloqueante (subagente) ──────────────────────────────────────────────
# Cuando el tool consulta la placa de forma BLOQUEANTE, marca un flag por placa.
# El webhook, al procesar la respuesta de Yahuar, si ve el flag GUARDA el resultado
# en Redis (en vez de empujarlo al vendedor) y el subagente lo recoge y lo devuelve
# como resultado del tool. Así el agente arma UNA sola respuesta limpia.

BLOCK_KEY_PREFIX  = "yahuar:block:"
RESULT_KEY_PREFIX = "yahuar:result:"
BLOCK_TTL   = 120
RESULT_TTL  = 120


def _pk(placa: str) -> str:
    return (placa or "").upper().replace(" ", "").replace("-", "")


async def set_bloqueante(placa: str) -> None:
    r = await _get_redis()
    await r.setex(BLOCK_KEY_PREFIX + _pk(placa), BLOCK_TTL, "1")


async def es_bloqueante(placa: str) -> bool:
    r = await _get_redis()
    return bool(await r.get(BLOCK_KEY_PREFIX + _pk(placa)))


async def clear_bloqueante(placa: str) -> None:
    r = await _get_redis()
    await r.delete(BLOCK_KEY_PREFIX + _pk(placa))


async def guardar_resultado(placa: str, payload: dict) -> None:
    r = await _get_redis()
    await r.setex(RESULT_KEY_PREFIX + _pk(placa), RESULT_TTL,
                  json.dumps(payload, ensure_ascii=False))


async def get_resultado(placa: str) -> dict | None:
    r = await _get_redis()
    raw = await r.get(RESULT_KEY_PREFIX + _pk(placa))
    return json.loads(raw) if raw else None


async def clear_resultado(placa: str) -> None:
    r = await _get_redis()
    await r.delete(RESULT_KEY_PREFIX + _pk(placa))
