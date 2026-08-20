"""El SQL del panel. Solo lecturas.

── Los nombres de tabla se escriben ACÁ ───────────────────────────────────────

No se importan de `vendedores.chat` ni de ningún multiagente. El panel declara
qué tablas mira, igual que cada área declara la suya. Si mañana se agrega el
panel de clientes, se agrega el nombre acá y no se toca nada del agente.

── La hora ────────────────────────────────────────────────────────────────────

`created_at` se guarda en UTC. Todo lo que agrupa por día, hora o semana lo
convierte a hora de Lima primero: sin eso, un mensaje de las 8 PM del lunes en
Lima cae en el martes del gráfico, y las horas pico salen corridas cinco.

Es la incidencia 8, y volvería sola si alguien escribe una consulta nueva sin
usar `_LIMA`.
"""
from datetime import date

from panel import db

CHATS = "chat_messages_vendedores"
ROSTER = "vendedores"

# Nada de lo que el panel muestre puede salir de una columna sin pasar por acá.
_LIMA = "(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'America/Lima')"


def _fecha(s):
    """'2026-08-20' -> date. asyncpg quiere el objeto, no el string."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _filtros(vendedor_id, desde, hasta):
    """Los tres filtros que comparten todas las estadísticas.

    `desde` y `hasta` son fechas de calendario en hora de Lima, las dos
    inclusivas: si el usuario elige "20 al 20", tiene que ver ese día entero.
    """
    condiciones, params = [], []
    if vendedor_id:
        params.append(vendedor_id)
        condiciones.append(f"vendedor_id = ${len(params)}")
    if d := _fecha(desde):
        params.append(d)
        condiciones.append(f"{_LIMA}::date >= ${len(params)}")
    if h := _fecha(hasta):
        params.append(h)
        condiciones.append(f"{_LIMA}::date <= ${len(params)}")
    return condiciones, params


def _where(extra, vendedor_id, desde, hasta):
    condiciones, params = _filtros(vendedor_id, desde, hasta)
    condiciones = list(extra) + condiciones
    return ("WHERE " + " AND ".join(condiciones)) if condiciones else "", params


async def _filas(sql, *params):
    pool = await db.get()
    async with pool.acquire() as c:
        return [dict(f) for f in await c.fetch(sql, *params)]


# ─── Roster ───────────────────────────────────────────────────────────────────

async def vendedores() -> list[dict]:
    return await _filas(
        f"SELECT vendedor_id, nombre FROM {ROSTER} WHERE activo ORDER BY nombre")


async def nombres_por_numero() -> dict[str, str]:
    """`{whatsapp: nombre}` para mostrar quién es cada conversación.

    Sale de la tabla `vendedores`, que es el roster real. La versión anterior lo
    sacaba de un diccionario en el código (`auth._MOCK_ASESORES`), que es la
    razón por la que el panel dependía del stack viejo.
    """
    filas = await _filas(
        f"SELECT whatsapp, nombre FROM {ROSTER} WHERE whatsapp IS NOT NULL")
    return {f["whatsapp"]: f["nombre"] or "" for f in filas}


# ─── Conversaciones ───────────────────────────────────────────────────────────

async def chats() -> list[dict]:
    return await _filas(f"""
        SELECT m.numero,
               COUNT(*)          AS n,
               MAX(m.created_at) AS last_ts,
               (SELECT contenido FROM {CHATS} x
                 WHERE x.numero = m.numero
                 ORDER BY x.created_at DESC LIMIT 1) AS last_msg
          FROM {CHATS} m
         GROUP BY m.numero
         ORDER BY last_ts DESC
    """)


async def mensajes(numero: str, limite: int = 500) -> list[dict]:
    return await _filas(f"""
        SELECT rol, contenido, tipo, tools, created_at
          FROM {CHATS}
         WHERE numero = $1
         ORDER BY created_at ASC
         LIMIT $2
    """, numero, limite)


# ─── Estadísticas ─────────────────────────────────────────────────────────────

async def resumen(vendedor_id=None, desde=None, hasta=None) -> dict:
    where, params = _where(["rol = 'user'"], vendedor_id, desde, hasta)
    pool = await db.get()
    async with pool.acquire() as c:
        f = await c.fetchrow(f"""
            SELECT COUNT(*) AS mensajes_totales,
                   COUNT(DISTINCT numero || '|' || ({_LIMA}::date)::text) AS conversaciones
              FROM {CHATS} {where}""", *params)
    return {"mensajes_totales": f["mensajes_totales"],
            "conversaciones": f["conversaciones"]}


async def evolucion(vendedor_id=None, desde=None, hasta=None) -> list[dict]:
    where, params = _where(["rol = 'user'"], vendedor_id, desde, hasta)
    return await _filas(f"""
        SELECT date_trunc('week', {_LIMA})::date AS semana, COUNT(*) AS mensajes
          FROM {CHATS} {where} GROUP BY 1 ORDER BY 1""", *params)


async def por_dia(vendedor_id=None, desde=None, hasta=None) -> list[dict]:
    where, params = _where(["rol = 'user'"], vendedor_id, desde, hasta)
    return await _filas(f"""
        SELECT {_LIMA}::date AS dia, COUNT(*) AS mensajes
          FROM {CHATS} {where} GROUP BY 1 ORDER BY 1""", *params)


async def por_hora(vendedor_id=None, desde=None, hasta=None) -> list[dict]:
    where, params = _where(["rol = 'user'"], vendedor_id, desde, hasta)
    return await _filas(f"""
        SELECT EXTRACT(HOUR FROM {_LIMA})::int AS hora, COUNT(*) AS mensajes
          FROM {CHATS} {where} GROUP BY 1 ORDER BY 1""", *params)


async def tools(vendedor_id=None, desde=None, hasta=None) -> list[dict]:
    """Qué áreas/tools se usaron. `tools` es un array de text por mensaje."""
    where, params = _where(["rol = 'assistant'", "tools IS NOT NULL"],
                           vendedor_id, desde, hasta)
    return await _filas(f"""
        SELECT t AS tool, COUNT(*) AS veces
          FROM {CHATS}, unnest(tools) AS t {where}
         GROUP BY 1 ORDER BY 2 DESC""", *params)


async def ranking(vendedor_id=None, desde=None, hasta=None) -> list[dict]:
    where, params = _where(["rol = 'user'", "vendedor_id IS NOT NULL"],
                           vendedor_id, desde, hasta)
    return await _filas(f"""
        SELECT vendedor_id,
               MAX(vendedor_nombre) AS nombre,
               COUNT(*)             AS mensajes
          FROM {CHATS} {where}
         GROUP BY vendedor_id ORDER BY mensajes DESC""", *params)


async def sin_uso() -> list[dict]:
    """Quién nunca le escribió al agente. Es la métrica de adopción."""
    return await _filas(f"""
        SELECT v.vendedor_id, v.nombre FROM {ROSTER} v
         WHERE v.activo AND NOT EXISTS (
             SELECT 1 FROM {CHATS} m
              WHERE m.vendedor_id = v.vendedor_id AND m.rol = 'user')
         ORDER BY v.nombre""")
