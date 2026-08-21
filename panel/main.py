"""API del panel de Catu.

    uvicorn panel.main:app

Sirve los datos que consume el front (`catu-panel`, React/Vite). Conserva las
rutas del panel que vivía dentro del monolito, para que repuntar el front sea
cambiar una URL y nada más.

── Las cuatro propiedades que lo definen ──────────────────────────────────────

  · No depende de nada fuera de `panel/`. Su Dockerfile copia una sola carpeta.

  · No usa Redis. El historial se lee de Postgres, que es donde está persistido.
    Redis guarda diez mensajes con TTL de dos horas: un panel que leyera de ahí
    mostraría un recorte y se contradiría con el resto de sus vistas.

  · El nombre del asesor sale de la tabla `vendedores`, no de un diccionario en
    el código.

  · Es de SOLO LECTURA. No hay una sola sentencia que escriba, y `panel/db.py`
    no expone `init_db()`: el panel no puede correr migraciones ni crear tablas.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import timezone

from fastapi import Body, FastAPI, Header, Query
from fastapi.middleware.cors import CORSMiddleware

from panel import auth, consultas, db


@asynccontextmanager
async def ciclo(app: FastAPI):
    # Un panel sin contraseña es un panel abierto con los chats de los asesores
    # adentro. Se avisa fuerte al arrancar en vez de descubrirlo después.
    if faltan := auth.configurado():
        logging.error(f"[panel] SIN PROTECCIÓN: faltan {faltan}. El login rechaza todo.")
    else:
        logging.info("[panel] login configurado")
    yield
    await db.close()


app = FastAPI(title="Catu · Panel", lifespan=ciclo)

# El front vive en otro dominio. Con Bearer y sin cookies, '*' no expone nada:
# el token viaja en un header que el navegador no manda solo.
_origenes = os.getenv("PANEL_CORS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origenes == "*" else
                  [o.strip() for o in _origenes.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _utc(dt):
    """El timestamp naive de Postgres, marcado como UTC.

    Sin esto el front lo interpreta como hora local y muestra todo corrido cinco
    horas. Es la mitad de la incidencia 8; la otra mitad está en `consultas`.
    """
    if dt is None:
        return None
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).isoformat()


@app.get("/health")
async def health():
    """Sirve de health check y de diagnóstico: dice si puede leer la base."""
    estado = {"status": "ok", "servicio": "panel"}
    try:
        pool = await db.get()
        async with pool.acquire() as c:
            estado["chats"] = await c.fetchval(
                f"SELECT count(DISTINCT numero) FROM {consultas.CHATS}")
            estado["vendedores"] = await c.fetchval(
                f"SELECT count(*) FROM {consultas.ROSTER} WHERE activo")
    except Exception as e:
        estado["status"] = "sin_base"
        estado["error"] = str(e)[:120]
    return estado


@app.post("/api/panel/login")
async def login(body: dict = Body(...)):
    if not auth.contrasena_ok((body or {}).get("password", "")):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="contraseña incorrecta")
    return auth.emitir()


@app.get("/api/panel/chats")
async def chats(authorization: str = Header("")):
    auth.exigir(authorization)
    filas = await consultas.chats()
    nombres = await consultas.nombres_por_numero()
    for f in filas:
        f["vendedor"] = nombres.get(f["numero"], "")
        f["last_ts"] = _utc(f.get("last_ts"))
    return {"chats": filas, "total": len(filas)}


@app.get("/api/panel/chats/{numero}")
async def chat(numero: str, authorization: str = Header("")):
    auth.exigir(authorization)
    msgs = await consultas.mensajes(numero)
    for m in msgs:
        m["created_at"] = _utc(m.get("created_at"))
    nombres = await consultas.nombres_por_numero()
    return {"numero": numero, "vendedor": nombres.get(numero, ""), "mensajes": msgs}


@app.get("/api/panel/vendedores")
async def vendedores(authorization: str = Header("")):
    auth.exigir(authorization)
    return {"vendedores": await consultas.vendedores()}


# ── Estadísticas ─────────────────────────────────────────────────────────────
#
# Las siete comparten los mismos tres filtros. Se registran en un bucle para que
# agregar una sea una línea y no un bloque copiado — que es como la anterior
# terminó con siete `try/except` idénticos.

_STATS = {
    "resumen": (consultas.resumen, None),
    "evolucion": (consultas.evolucion, "data"),
    "por-dia": (consultas.por_dia, "data"),
    "por-hora": (consultas.por_hora, "data"),
    "tools": (consultas.tools, "data"),
    "ranking": (consultas.ranking, "data"),
}


def _registrar(ruta: str, consulta, envoltura: str | None):
    async def _vista(authorization: str = Header(""),
                     vendedor_id: str = Query(None),
                     desde: str = Query(None),
                     hasta: str = Query(None)):
        auth.exigir(authorization)
        datos = await consulta(vendedor_id, desde, hasta)
        return {envoltura: datos} if envoltura else datos

    app.get(f"/api/panel/stats/{ruta}")(_vista)


for _ruta, (_consulta, _env) in _STATS.items():
    _registrar(_ruta, _consulta, _env)


@app.get("/api/panel/stats/sin-uso")
async def sin_uso(authorization: str = Header("")):
    auth.exigir(authorization)
    return {"data": await consultas.sin_uso()}
