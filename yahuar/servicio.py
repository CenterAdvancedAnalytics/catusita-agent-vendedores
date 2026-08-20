"""El servicio de placas. Dueño ÚNICO de la conversación con Yahuar.

    BRPOP yahuar:solicitudes ──► manda la placa ──► junta lo que conteste
                                                          │
    SETEX yahuar:resultado:{placa} ◄── procesa ◄───────────┘

── Por qué es un servicio aparte y no una tool más ────────────────────────────

Porque Yahuar es UN RECURSO FÍSICO ÚNICO: una sola conversación de WhatsApp. Sus
respuestas vuelven todas por el mismo hilo, sin nada que las correlacione. Con
dos consultas en vuelo no hay forma de saber cuál respuesta es de quién.

No es que convenga tener un solo dueño — es que no puede haber más de uno. El
stack viejo lo resolvía con una clave global (`yahuar:pendiente`): una consulta a
la vez y punto. Con tres contenedores preguntando, esa clave se pisa.

Acá la exclusión es la estructura del programa: el loop no saca la siguiente
solicitud hasta terminar la anterior.

── Lo que cuesta ──────────────────────────────────────────────────────────────

Serializar significa que tres placas pedidas a la vez son ~3 minutos para la
última. No hay forma de acelerarlo: el cuello es Yahuar contestando un WhatsApp.
Por eso la tool avisa «TARDA 30-60 SEGUNDOS» y el área lo repite.
"""
import asyncio
import base64
import json
import logging
import os
import time

import httpx

from yahuar import redis as redis_mod, relay, vision, waha

COLA_SOLICITUDES = "yahuar:solicitudes"
COLA_ENTRANTES = "yahuar:entrantes"
CLAVE_ESPERANDO = "yahuar:esperando"
PREFIJO_RESULTADO = "yahuar:resultado:"

# Cuánto vive un resultado. La tool espera 90 s; con 180 sobra para que lo
# levante incluso si el worker se demoró.
TTL_RESULTADO = 180

# Cuánto espera el BRPOP de solicitudes. Corto para que un SIGTERM no tarde.
BLOQUEO = 5

# Cada cuánto se mira si llegó algo de Yahuar mientras se espera.
SONDEO = 0.5


async def _sin_leer(r) -> None:
    """Tira lo que haya quedado de una consulta anterior.

    Sin esto, la respuesta tardía de una placa que ya expiró se mezcla con la
    siguiente y el vendedor recibe los datos de otro vehículo — el error más
    caro que puede cometer este servicio.
    """
    await r.delete(COLA_ENTRANTES)


async def _recolectar(r, placa: str) -> list[dict]:
    """Junta los mensajes de Yahuar hasta que se calla.

    ── El debounce ────────────────────────────────────────────────────────────

    Yahuar no contesta un mensaje: manda tres o cuatro —saludo, datos, foto—.
    Procesar el primero que llega deja afuera la foto, que es donde están los
    datos. Así que se juntan y se espera `relay.DEBOUNCE` de silencio.

    ── Las aclaraciones no cuentan ────────────────────────────────────────────

    Su saludo («soy Yahuar, ¿qué información buscas?») no es una respuesta: se
    le contesta «Placa vehicular» y se sigue esperando, sin acumularlo y sin
    reiniciar el reloj total. Si se acumulara, el vendedor recibiría el saludo
    de un bot como si fuera el dato del vehículo (incidencia 21).
    """
    juntados: list[dict] = []
    arranque = time.time()
    ultimo = 0.0

    while True:
        ahora = time.time()

        # ¿Se calló el tiempo suficiente y ya tenemos algo?
        if juntados and ultimo and (ahora - ultimo) >= relay.DEBOUNCE:
            return juntados

        # ¿Nunca contestó?
        if (ahora - arranque) >= relay.ESPERA_MAX:
            if juntados:
                return juntados
            logging.warning(f"[yahuar] {placa}: no contestó en {relay.ESPERA_MAX}s")
            return []

        crudo = await r.rpop(COLA_ENTRANTES)
        if not crudo:
            await asyncio.sleep(SONDEO)
            continue

        try:
            sobre = json.loads(crudo)
        except Exception:
            continue
        payload = sobre.get("payload") or {}
        texto = payload.get("body") or ""

        if relay.es_aclaracion(texto):
            logging.info(f"[yahuar] {placa}: pide aclaración, se le responde")
            await waha.enviar(relay.RESPUESTA_A_ACLARACION)
            continue

        juntados.append(payload)
        ultimo = time.time()
        logging.info(f"[yahuar] {placa}: mensaje {len(juntados)} · {texto[:50]!r}")


async def _bajar_media(payload: dict) -> tuple[str, str]:
    """(base64, mime) de la foto del payload. ('', '') si no hay o no se pudo.

    WAHA la manda de dos formas según cómo esté configurado: los bytes ya
    incrustados (`media.data`, con WHATSAPP_HOOK_MEDIA_INLINE) o una URL. Hay
    que soportar las dos: cambiar esa variable no debería romper las placas.
    """
    media = payload.get("media") or {}
    mime = (media.get("mimetype") or "image/jpeg").split(";")[0].strip()

    if datos := media.get("data"):
        return datos, mime

    url = media.get("url")
    if not url:
        return "", ""
    if url.startswith("/"):
        url = f"{os.getenv('WAHA_BASE_URL', '').rstrip('/')}{url}"

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url, headers={"X-Api-Key": os.getenv("WAHA_API_KEY", "")})
            r.raise_for_status()
            return base64.b64encode(r.content).decode(), (
                mime or r.headers.get("content-type", "image/jpeg").split(";")[0])
    except Exception as e:
        logging.error(f"[yahuar] no se pudo bajar la foto: {e}")
        return "", ""


async def _procesar(mensajes: list[dict], placa: str) -> dict:
    """De los mensajes crudos de Yahuar al resultado que espera la tool."""
    if not mensajes:
        return {"error": "SIN_RESPUESTA",
                "mensaje": (f"El servicio de placas no respondió por {placa}. "
                            "Puede estar caído; probá de nuevo en un rato.")}

    textos: list[str] = []
    imagen_b64 = mime = ""

    for m in mensajes:
        texto = m.get("body") or ""
        if texto:
            if relay.es_error(texto):
                return {"error": "NO_ENCONTRADO",
                        "mensaje": (f"No se pudieron obtener datos de la placa "
                                    f"{placa}. Verificá que esté bien escrita.")}
            textos.append(texto)
        if not imagen_b64 and m.get("hasMedia"):
            imagen_b64, mime = await _bajar_media(m)

    datos = ""
    if imagen_b64:
        datos = await vision.leer_tarjeta(imagen_b64, mime)

    # Si la visión falló, lo que Yahuar haya mandado por texto es mejor que nada.
    if not datos:
        datos = "\n\n".join(textos)

    if not datos and not imagen_b64:
        return {"error": "SIN_DATOS",
                "mensaje": f"Yahuar contestó por la placa {placa} pero sin datos usables."}

    return {
        "placa": placa,
        "datos_vehiculo_texto": datos,
        "tiene_imagen": bool(imagen_b64),
        "imagen_base64": imagen_b64,
        "imagen_mime": mime or "image/jpeg",
    }


async def _atender(r, solicitud: dict) -> None:
    placa = relay.normalizar_placa(solicitud.get("placa", ""))
    if not placa:
        return

    t0 = time.time()
    # La marca que le permite a recepción reconocer un LID nuevo: solo mientras
    # hay una placa en vuelo puede aparecer un desconocido que sea Yahuar.
    await r.setex(CLAVE_ESPERANDO, int(relay.ESPERA_MAX) + 30, placa)
    await _sin_leer(r)

    if not await waha.enviar(relay.pedido(placa)):
        resultado = {"error": "NO_SE_PUDO_CONSULTAR",
                     "mensaje": "No se pudo contactar al servicio de placas."}
    else:
        resultado = await _procesar(await _recolectar(r, placa), placa)

    await r.setex(PREFIJO_RESULTADO + placa, TTL_RESULTADO,
                  json.dumps(resultado, ensure_ascii=False))
    await r.delete(CLAVE_ESPERANDO)
    logging.info(f"[yahuar] {placa} resuelta en {time.time()-t0:.1f}s "
                 f"· error={resultado.get('error', '-')}")


async def correr() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info(f"servicio yahuar · relay={relay.NUMERO} · "
                 f"debounce={relay.DEBOUNCE}s · espera_max={relay.ESPERA_MAX}s")

    r = await redis_mod.get()
    while True:
        try:
            item = await r.brpop([COLA_SOLICITUDES], timeout=BLOQUEO)
        except Exception as e:
            logging.error(f"[cola] Redis no respondió: {e}")
            await asyncio.sleep(2)
            continue
        if item is None:
            continue

        try:
            solicitud = json.loads(item[1])
        except Exception as e:
            logging.error(f"solicitud ilegible: {e}")
            continue

        # Una placa que falla no puede tumbar el servicio: dejaría la cola
        # detenida y todas las siguientes esperando el timeout de su tool.
        try:
            await _atender(r, solicitud)
        except Exception as e:
            logging.exception(f"placa {solicitud.get('placa')!r} falló: {e}")
            placa = relay.normalizar_placa(solicitud.get("placa", ""))
            if placa:
                await r.setex(PREFIJO_RESULTADO + placa, TTL_RESULTADO,
                              json.dumps({"error": "FALLO_INTERNO",
                                          "mensaje": "El servicio de placas falló."},
                                         ensure_ascii=False))
                await r.delete(CLAVE_ESPERANDO)


def main() -> None:
    asyncio.run(correr())


if __name__ == "__main__":
    main()
