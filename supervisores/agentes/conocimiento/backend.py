"""Acceso de `conocimiento` (supervisores) a los procesos.

No habla con las BACKEND APIS: habla con Postgres + pgvector, tabla
`conocimiento_supervisores` (migraciones 004 y 008, aplicadas).

Ya NO lleva filtro por multiagente. La tabla ES la de supervisores: la frontera
dejó de ser un WHERE que alguien puede olvidar y pasó a ser el nombre de la
tabla. Un proceso de otro multiagente no puede salir de acá ni con el query mal
escrito.

── Por qué el vector va como texto ────────────────────────────────────────────

asyncpg no conoce el tipo `vector`, así que una lista de floats se manda como
`'[0.1,0.2,...]'` y se castea con `::vector` en el SQL. La alternativa es
registrar un codec en cada conexión del pool; para dos queries no vale la pena.

── Qué se embebe y qué se compara ─────────────────────────────────────────────

El `<=>` de pgvector es distancia coseno: 0 = idéntico, 2 = opuesto. Similitud
es `1 - distancia`, y así se devuelve, porque el umbral se piensa en similitud
(«0.35 de parecido») y no en distancia.

El índice es HNSW, no ivfflat: ivfflat calibra sus listas cuando se crea, y
creado sobre una tabla vacía —que es exactamente el caso acá— queda mal
calibrado para siempre.
"""
from supervisores.plataforma_supervisores import db
from supervisores.plataforma_supervisores.embeddings import MODELO as MODELO_EMBEDDING, embeber  # noqa: F401

TABLA_PROCESOS = "conocimiento_supervisores"

# `embeber` se reexporta desde plataforma: el cliente de OpenAI es uno por
# proceso, no uno por área. El aislamiento entre multiagentes lo dan las tablas
# de arriba, no el cliente.
#
# MODELO_EMBEDDING viaja hasta la columna `modelo_emb` de cada fila. Es lo que
# permite saber qué quedó viejo si algún día se cambia de modelo.



def _vec(vector: list[float]) -> str:
    """[0.1, 0.2] -> '[0.1,0.2]', que es lo que entiende `::vector`."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


async def similares(vector: list[float], k: int = 5) -> list[dict]:
    """Top-k de `conocimiento_supervisores`. [{proceso, descripcion, procedimiento, similitud}]

    Se compara contra `descripcion`, que es lo único embebido: cómo lo PIDE el
    usuario, con sus palabras. No contra `proceso`, que es el mismo caso dicho
    en idioma de la empresa — nadie escribe «devolución por daño en transporte»
    cuando le llegó una caja rota.
    """
    if not vector:
        return []

    pool = await db.get()
    async with pool.acquire() as c:
        filas = await c.fetch(
            f"""
            SELECT id, proceso, descripcion, procedimiento, area,
                   1 - (embedding <=> $1::vector) AS similitud
              FROM {TABLA_PROCESOS}
             WHERE activo AND embedding IS NOT NULL
             ORDER BY embedding <=> $1::vector
             LIMIT $2
            """,
            _vec(vector), k,
        )
    return [dict(f) for f in filas]


async def cargar(proceso: str, descripcion: str, procedimiento: str,
                 area: str | None = None, creado_por: str = "manual") -> str:
    """Escribe un proceso. Es el ÚNICO camino para meter uno.

    ── Por qué existe esta función y no un INSERT a mano ──────────────────────

    Porque embebe `descripcion` y nada más. Un INSERT suelto puede embeber el
    `proceso` por error, y eso no falla: la fila queda cargada, la búsqueda
    devuelve resultados, y simplemente recupera mal para siempre. El síntoma
    aparece meses después: el agente dice «no sé» sobre algo que está escrito.

    Es la única parte del diseño que se rompe sin dar error. Por eso se cierra
    con código en vez de con una nota.
    """
    vector = await embeber(descripcion)
    if not vector:
        raise ValueError("`descripcion` vacía: es lo único con lo que se busca.")

    pool = await db.get()
    async with pool.acquire() as c:
        return await c.fetchval(
            f"""INSERT INTO {TABLA_PROCESOS}
                (proceso, descripcion, procedimiento, area, modelo_emb, embedding, creado_por)
                VALUES ($1, $2, $3, $4, $5, $6::vector, $7)
                RETURNING id""",
            proceso, descripcion, procedimiento, area,
            MODELO_EMBEDDING, _vec(vector), creado_por,
        )
