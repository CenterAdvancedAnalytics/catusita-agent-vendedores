"""Pool de Postgres del panel. Lo único que el panel necesita de infraestructura.

── Por qué no reusa db/connection.py ──────────────────────────────────────────

Porque ese trae `init_db()`, que corre TODAS las migraciones al arrancar. Eso ya
mordió: las tablas que se dropeaban a mano volvían solas en el siguiente deploy.

El panel es de solo lectura sobre las tablas del agente. No tiene por qué poder
crear ni borrar nada, y la forma de garantizarlo es que la función no exista acá.
"""
import os
import re

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_pool: asyncpg.Pool | None = None


def _sin_sslmode(dsn: str) -> str:
    """asyncpg no entiende `sslmode` en la URL; el TLS se pide por parámetro."""
    return re.sub(r"[?&]sslmode=\w+", "", dsn or "")


async def get() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = _sin_sslmode(os.getenv("DATABASE_URL", ""))
        if not dsn:
            raise RuntimeError("Falta DATABASE_URL.")
        # Pocas conexiones: el panel lo miran dos o tres personas, no 38.
        _pool = await asyncpg.create_pool(dsn, ssl="require", min_size=1, max_size=5)
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
