"""Cliente HTTP contra la API de Catusita — la de verdad, sin wrapper.

    http://api.catusita.com:8092/swagger/index.html   (Agent.WebAPI)

Hasta ahora cada área tenía su propio `httpx.AsyncClient` apuntando a
`tools-agente-catusita`, un servicio intermedio que traducía esta misma API a
10 endpoints con `X-API-Key`. Ese salto se va: las áreas hablan directo.

── Todo vive envuelto ─────────────────────────────────────────────────────────

Cada respuesta viene como `{data, isValid, messages}`. `get()` devuelve `data`
ya desenvuelto, porque a las áreas el sobre no les sirve para nada — salvo
cuando `isValid` es false, que es un rechazo con un 200 encima y por eso se
traduce a `{"error": ...}` acá y no en cada área.

── «No encontrado» son cero resultados, no un 404 ─────────────────────────────

    /api/stock/filter?ItemCode=NOEXISTE   ->   200  {"data": [], "isValid": true}

Esto importa más de lo que parece: quien llame no puede preguntar por
`resultado.get("error")` para saber si el SKU existe. Tiene que mirar si la
lista vino vacía. Los backends de cada área lo traducen a su propio error.

── Autenticación ──────────────────────────────────────────────────────────────

Hoy no pide. El swagger declara Bearer y hay un `POST /api/accounts/login`, pero
los GET responden 200 sin token — probado endpoint por endpoint. No se
implementa el login mientras no haga falta: código de autenticación que nadie
ejercita es código que va a estar roto justo el día que se necesite.

Si algún día empiezan a dar 401, se agrega acá y en un solo lugar.

── Sobre el http:// ───────────────────────────────────────────────────────────

La API no tiene TLS y los datos de clientes viajan en claro hasta
api.catusita.com. Queda anotado porque es la clase de cosa que nadie revisa
después; si algún día hay https, solo cambia CATUSITA_API_URL.
"""
import asyncio
import logging
import os
import time
import urllib.request
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv

from vendedores.plataforma_vendedores import redis as redis_mod

load_dotenv()

BASE_URL = os.getenv("CATUSITA_API_URL", "http://api.catusita.com:8092")

TIMEOUT = 20.0

# ── Endpoints que NO toleran dos llamadas a la vez ─────────────────────────────
#
# El controlador de `sales/orders` se pisa a sí mismo. Medido, 24 consultas:
#
#     en serie      0 errores       2.03s
#     de a 2       83% error 500    0.30s
#     de a 6       96% error 500    0.10s
#
# Y es POR ENDPOINT, no global: dos llamadas simultáneas a endpoints distintos
# responden 200 las dos. `stock`, `price`, `article` y `client` aguantan
# paralelo sin problema; estos dos no.
#
# Importa mucho más de lo que parece, porque nadie escribe estas llamadas en
# paralelo a propósito: LangGraph ejecuta las tool calls de un mismo turno
# concurrentemente. Un asesor que pregunta por dos clientes a la vez disparaba
# dos `consultar_pedidos` y uno volvía con error 500 — sin que nada en el código
# del área insinuara que eso podía pasar.
#
# ── Por qué el lock es de Redis y no un asyncio.Lock ─────────────────────────
#
# Un `asyncio.Lock()` vive dentro de UN proceso. Alcanzaba mientras había un
# contenedor por multiagente: lo que había que evitar era que un turno se
# pisara a sí mismo (LangGraph corre las tool calls en paralelo).
#
# Con réplicas deja de alcanzar. Tres contenedores de `vendedores` son tres
# locks independientes que no se ven entre sí, y los tres le pegan a
# `sales/orders` al mismo tiempo — o sea, exactamente el 83% de error 500 que el
# lock existía para evitar.
#
# Con una clave en Redis el candado es uno solo para todos los procesos, esté
# donde esté cada uno.
SERIALIZADOS = ("/api/sales/orders",)

# El candado. Uno por prefijo serializado, no uno global: `sales/orders` no
# tiene por qué esperar a nada más.
CLAVE_LOCK = "api:lock:sales-orders"

# Cuánto vive el candado si quien lo tomó se muere. Un turno normal contra este
# endpoint tarda menos de un segundo; 15 da margen de sobra y garantiza que un
# contenedor que se cae no deja la cola trabada más que eso.
LOCK_TTL = 15

# Cuánto se espera a que se libere antes de seguir igual. Sin tope, una réplica
# colgada bloquearía a todas las demás; pasado esto se arriesga el 500, que el
# reintento suele salvar.
LOCK_ESPERA_MAX = 10.0
LOCK_SONDEO = 0.05

# Pausa antes del único reintento ante un 500. Corta a propósito: el turno de
# WhatsApp está esperando del otro lado.
ESPERA_REINTENTO = 0.4

_http: httpx.AsyncClient | None = None
_lock_series = asyncio.Lock()


def _cliente() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)
    return _http


def _hay_que_serializar(path: str) -> bool:
    return path.startswith(SERIALIZADOS)


async def get(path: str, params: dict | None = None,
              timeout: float | None = None) -> dict | list:
    """GET a la API. Devuelve el `data` desenvuelto, o `{"error": ...}`.

    Los parámetros vacíos se sacan antes de mandar: la API es sensible a eso
    —`CodeSeller` sin `CodeClient` devuelve 500— y mandar `None` como string
    vacío es la forma más fácil de provocarlo sin darse cuenta.
    """
    limpios = {k: v for k, v in (params or {}).items() if v not in (None, "")}

    if _hay_que_serializar(path):
        # El lock local sigue: evita que las tareas de ESTE proceso peleen por
        # el de Redis, que sería una ronda de red por cada una.
        async with _lock_series:
            async with _candado():
                return await _pedir(path, limpios, timeout)
    return await _pedir(path, limpios, timeout)


@asynccontextmanager
async def _candado():
    """Mutex entre procesos sobre `sales/orders`. Nunca bloquea para siempre.

    Es un `SET NX EX`: el primero que lo pone gana, y el TTL garantiza que un
    contenedor que se muere con el candado tomado no traba al resto más allá de
    `LOCK_TTL`.

    ── Por qué se sigue igual si no se pudo tomar ─────────────────────────────

    Porque el candado es una OPTIMIZACIÓN, no una regla de negocio: existe para
    no provocar un 500 evitable. Si después de `LOCK_ESPERA_MAX` no se soltó,
    algo raro pasa —una réplica colgada, Redis lento— y esperar más solo agrega
    latencia a un turno que ya tiene a alguien esperando del otro lado.

    Se sigue, y si sale 500 el reintento de `_pedir` lo suele salvar. Peor sería
    devolverle «no pude» al asesor por un candado.
    """
    tomado = False
    r = None
    try:
        r = await redis_mod.get()
        limite = time.monotonic() + LOCK_ESPERA_MAX
        while time.monotonic() < limite:
            if await r.set(CLAVE_LOCK, "1", nx=True, ex=LOCK_TTL):
                tomado = True
                break
            await asyncio.sleep(LOCK_SONDEO)
        else:
            logging.warning("[catusita_api] candado de sales/orders no se soltó; sigo igual")
    except Exception as e:
        # Sin Redis no hay candado, pero sí hay consulta que hacer. El lock
        # local del proceso sigue en pie, que es mejor que nada.
        logging.error(f"[catusita_api] no se pudo tomar el candado: {e}")

    try:
        yield
    finally:
        if tomado and r is not None:
            try:
                await r.delete(CLAVE_LOCK)
            except Exception:
                pass   # el TTL lo limpia igual


async def _pedir(path: str, params: dict, timeout: float | None) -> dict | list:
    """Una llamada, con UN reintento si el servidor devuelve 500.

    El 500 de esta API es casi siempre por concurrencia, y el lock de arriba solo
    cubre a un proceso: los tres workers le pegan por separado. Un reintento con
    una pausa corta alcanza para eso. Dos ya sería insistirle a un servidor que
    está en problemas.
    """
    for intento in (1, 2):
        try:
            r = await _cliente().get(path, params=params or None,
                                     timeout=timeout or TIMEOUT)
            r.raise_for_status()
            return _desenvolver(r.json())
        except httpx.HTTPStatusError as e:
            codigo = e.response.status_code
            if codigo == 404:
                return {"error": "NO_ENCONTRADO", "mensaje": f"No existe: {path}"}
            if codigo >= 500 and intento == 1:
                await asyncio.sleep(ESPERA_REINTENTO)
                continue
            return {"error": f"Error del servidor: {codigo}"}
        except httpx.TimeoutException:
            return {"error": "La consulta tardó demasiado."}
        except httpx.RequestError:
            return {"error": "No se pudo conectar al servidor."}
        except ValueError:
            return {"error": "La API devolvió algo que no es JSON."}

    return {"error": "Error del servidor: 500"}


def _desenvolver(cuerpo) -> dict | list:
    """Saca el `data` del sobre `{data, isValid, messages}`."""
    if not isinstance(cuerpo, dict) or "data" not in cuerpo:
        return cuerpo
    if cuerpo.get("isValid") is False:
        msgs = "; ".join(m.get("message", "") for m in (cuerpo.get("messages") or []))
        return {"error": msgs or "La API rechazó la consulta."}
    return cuerpo.get("data")


def _bajar_bloqueante(url: str, timeout: float) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


async def descargar(url: str, timeout: float = 30.0) -> bytes | None:
    """Baja un archivo suelto (PDF, imagen). `None` si no se pudo.

    ── Por qué urllib y no httpx, que es lo que usa todo el resto ─────────────

    Los PDFs y las imágenes viven en otro servidor (`:8086`) y ese servidor
    manda una cabecera con un espacio en el nombre:

        Pagina web: http://api.catusita.com

    Un nombre de header no puede tener espacios, así que los clientes que
    parsean en serio la rechazan y no entregan NADA:

        httpx     RemoteProtocolError: illegal header line
        aiohttp   400, Invalid header token
        requests  se cuelga hasta el timeout

    `urllib` de la stdlib es más permisivo y baja el archivo sin chistar —
    probado: devuelve el PNG entero, 25978 bytes. Es la única razón por la que
    esta función no usa el mismo cliente que las demás.

    Es bloqueante, así que va a un hilo: si corriera en el event loop, bajar la
    foto de un producto congelaría a todos los turnos que están en vuelo.

    Si algún día arreglan esa cabecera, esto vuelve a ser un `await _cliente().get(url)`.
    """
    return await asyncio.to_thread(_bajar_bloqueante, url, timeout)


async def close() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None
