"""Pool de Redis. Uno por proceso.

Duplicar el pool abriría N conexiones contra el mismo Redis sin ganar
aislamiento: el aislamiento lo dan los nombres de las claves y de las colas.

── socket_timeout: el bug que tumbaba el worker ───────────────────────────────

redis-py 8 cambió el default de `socket_timeout` de `None` a `5`. Y el worker
espera trabajo así:

    await r.brpop([cola], timeout=5)     el servidor retiene la conexión 5 s
                                         el cliente abandona la lectura a los 5 s

Los dos plazos son el mismo, así que compiten. El cliente corta justo cuando el
servidor iba a contestar, redis-py lanza `TimeoutError`, y como está adentro del
loop principal el proceso se muere:

    redis.exceptions.TimeoutError: Timeout reading from redis.railway.internal:6379

No se ve en desarrollo. Las pruebas hacen operaciones cortas —SET, LPUSH, EVAL—
que contestan en milisegundos; el único que bloquea es el loop del worker, y ese
solo corre adentro de un contenedor.

La regla: **el timeout del socket tiene que ser MAYOR que el del comando que
bloquea.** Acá el más largo es el BRPOP de 5 s, y 30 deja margen de sobra sin
que una conexión muerta quede colgada para siempre — que es lo que pasaría con
`socket_timeout=None`, el default viejo.

`health_check_interval` es el complemento: Railway corta las conexiones ociosas,
y sin un PING periódico la primera operación después del corte falla.
"""
import os

import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Más alto que cualquier comando bloqueante del sistema. Ver el encabezado.
SOCKET_TIMEOUT = 30

# Cada cuánto se hace PING sobre una conexión ociosa del pool.
LATIDO = 30

_pool: aioredis.Redis | None = None


async def get() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = await aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=SOCKET_TIMEOUT,
            socket_connect_timeout=10,
            health_check_interval=LATIDO,
            retry_on_timeout=True,
        )
    return _pool


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
