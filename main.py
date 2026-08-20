import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from codigo_obsoleto.webhooks.whatsapp import router_wh as whatsapp_router
from dashboard.panel import router_panel


async def _sync_vendedores():
    """Carga el roster de vendedores (para 'sin uso' y el filtro) desde el registro de auth."""
    from codigo_obsoleto.shared import auth
    from db import models
    vistos = set()
    for numero, p in auth._MOCK_ASESORES.items():
        vid = p.get("vendedor_id")
        if not vid or vid in vistos:
            continue
        vistos.add(vid)
        try:
            await models.upsert_vendedor(vid, p.get("asesor_id"), p.get("nombre"), numero)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Solo inicializar DB si DATABASE_URL está configurado
    if os.getenv("DATABASE_URL"):
        from db.connection import close_db

        # ── Acá NO se corren migraciones ──────────────────────────────────────
        #
        # Antes se corrían al arrancar, y era un error de dos formas:
        #
        #   el init_db() original reejecutaba TODAS en cada arranque. Las tablas
        #   que se dropeaban a mano volvían solas en el siguiente deploy, sin
        #   avisar — `users`, `conversations`, `messages` y `claims` habrían
        #   reaparecido.
        #
        #   el corredor que lo reemplazó llevaba registro, pero apuntaba a una
        #   ruta que no existe: corría cero migraciones en silencio.
        #
        # Los `.sql` de `db/migrations/` se aplican a mano, desde desarrollo,
        # cuando se decide cambiar el esquema. La 009 dropeó tres tablas: eso se
        # corre mirando el resultado, no como efecto de reiniciar un contenedor.
        await _sync_vendedores()
        yield
        await close_db()
    else:
        yield


app = FastAPI(title="Catusita Agent API", lifespan=lifespan)

# CORS para el front Vite (otro origen). Con Bearer (sin cookies) '*' es seguro.
# Configurable con PANEL_CORS (lista separada por comas) si se quiere restringir.
_cors = os.getenv("PANEL_CORS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors == "*" else [o.strip() for o in _cors.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "catusita-agent"}


app.include_router(whatsapp_router, prefix="/webhook")
app.include_router(router_panel)
