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
import os
import urllib.request

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CATUSITA_API_URL", "http://api.catusita.com:8092")

TIMEOUT = 20.0

_http: httpx.AsyncClient | None = None


def _cliente() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)
    return _http


async def get(path: str, params: dict | None = None,
              timeout: float | None = None) -> dict | list:
    """GET a la API. Devuelve el `data` desenvuelto, o `{"error": ...}`.

    Los parámetros vacíos se sacan antes de mandar: la API es sensible a eso
    —`CodeSeller` sin `CodeClient` devuelve 500— y mandar `None` como string
    vacío es la forma más fácil de provocarlo sin darse cuenta.
    """
    limpios = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    try:
        r = await _cliente().get(path, params=limpios or None,
                                 timeout=timeout or TIMEOUT)
        r.raise_for_status()
        cuerpo = r.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return {"error": "NO_ENCONTRADO", "mensaje": f"No existe: {path}"}
        return {"error": f"Error del servidor: {e.response.status_code}"}
    except httpx.TimeoutException:
        return {"error": "La consulta tardó demasiado."}
    except httpx.RequestError:
        return {"error": "No se pudo conectar al servidor."}
    except ValueError:
        return {"error": "La API devolvió algo que no es JSON."}

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
