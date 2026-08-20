"""Subagente de placa vía Yahuar — versión BLOQUEANTE (A2A).

En vez del relay async (el tool decía "en 30s" y la respuesta llegaba por otro
webhook, filtrando mal los mensajes intermedios de Yahuar), este subagente:

1. Marca la consulta como bloqueante y la envía a Yahuar.
2. Le avisa al vendedor que está consultando (feedback inmediato).
3. BLOQUEA hasta que el webhook deposite el resultado final en Redis (o timeout).
4. Devuelve los datos limpios del vehículo + la foto, para que el AGENTE arme una
   sola respuesta. Los saludos/aclaraciones de Yahuar nunca llegan al vendedor.

Corre dentro del background task del mensaje del vendedor, así que bloquear no
cuelga el HTTP; solo demora la respuesta (que es justo lo que se busca).

Kill-switch: YAHUAR_BLOQUEANTE=false hace que el tool use el relay async viejo.
"""
import asyncio
import logging
import time

from shared import yahuar

_TIMEOUT = int(__import__("os").getenv("YAHUAR_TIMEOUT_SECS", "80"))
_POLL = 2.0


async def consultar_placa_bloqueante(placa: str, from_field: str,
                                     timeout: int = _TIMEOUT) -> dict:
    """Envía la placa a Yahuar y espera (bloqueando) el resultado final.

    Devuelve {placa, datos_vehiculo_texto, imagen_base64?, tiene_imagen} o
    {error, placa, mensaje} si no hubo datos o venció el tiempo.
    """
    placa = (placa or "").strip().upper()

    # Estado limpio + marcar bloqueante ANTES de enviar (para que el webhook lo vea)
    await yahuar.clear_resultado(placa)
    await yahuar.set_bloqueante(placa)

    # Aviso inmediato al vendedor (best-effort; no rompe si falla)
    try:
        from shared import waha as waha_mod
        await waha_mod.waha.send_message(
            from_field, "", f"🔎 Consultando la placa *{placa}*, dame unos segundos…")
    except Exception as e:
        logging.warning(f"[yahuar_subagente] aviso inicial falló: {e}")

    try:
        await yahuar.consultar_placa(placa, from_field)
    except Exception as e:
        await yahuar.clear_bloqueante(placa)
        logging.error(f"[yahuar_subagente] error enviando a Yahuar: {e}")
        return {"error": "YAHUAR_ENVIO", "placa": placa,
                "mensaje": "No pude enviar la consulta de la placa. Intenta de nuevo."}

    # Bloquear hasta que el webhook deposite el resultado (o timeout)
    t0 = time.time()
    res = None
    while time.time() - t0 < timeout:
        res = await yahuar.get_resultado(placa)
        if res:
            break
        await asyncio.sleep(_POLL)

    await yahuar.clear_bloqueante(placa)
    await yahuar.clear_resultado(placa)

    if not res:
        return {"error": "TIMEOUT", "placa": placa,
                "mensaje": (f"La consulta de la placa {placa} no respondió a tiempo. "
                            "Intenta de nuevo en un momento.")}
    if res.get("error"):
        return {"error": res["error"], "placa": placa,
                "mensaje": res.get("mensaje",
                                   f"No se obtuvieron datos de la placa {placa}.")}

    return {
        "placa": placa,
        "datos_vehiculo_texto": res.get("datos") or "",
        "imagen_base64": res.get("imagen_base64"),
        "imagen_mime": res.get("imagen_mime", "image/jpeg"),
        "tiene_imagen": bool(res.get("imagen_base64")),
    }
