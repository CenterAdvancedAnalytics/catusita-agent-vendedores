-- ─────────────────────────────────────────────────────────────────────────────
-- 010 — Se cae lo que quedó del stack anterior, y se rescatan 28 chats
-- ─────────────────────────────────────────────────────────────────────────────
--
-- La base tenía 16 tablas y seis no las lee ni las escribe nadie. Cinco vienen
-- de la 001, que implementó el diseño original de CLAUDE.md —`users`,
-- `conversations`, `messages`, `claims`, `knowledge_base`— y quedaron vacías
-- cuando el sistema pasó a la arquitectura vertical. La sexta, `chat_messages`,
-- sí tiene datos y por eso el orden de esta migración importa.
--
-- ── Los 28 chats que hay que rescatar ANTES de borrar ────────────────────────
--
-- `chat_messages` era la tabla única de conversaciones, con una columna `canal`.
-- La 005 la reemplazó por una por multiagente y se copiaron 585 filas. Pero
-- producción siguió escribiendo en la vieja hasta el deploy de hoy 20:29 (el
-- merge del PR), así que quedaron 28 mensajes SOLO ahí:
--
--     chat_messages              613 filas   última: 08-19 19:40
--     chat_messages_vendedores   585 filas   última: 08-18 23:40
--
-- Son conversaciones reales de asesores de hoy — placas, discos de freno, fotos
-- de producto, un "no me sirves". Es material para calibrar procesos y para el
-- panel. Borrar la tabla sin copiarlas las pierde.
--
-- Después del deploy de las 20:29 producción escribe en la tabla nueva, así que
-- esta copia se hace UNA vez y no se repite.
--
-- ── El registro huérfano de la 007 ───────────────────────────────────────────
--
-- La tabla `migraciones` tiene diez filas para nueve archivos. Sobra
-- `007_padron_visto.sql`: se aplicó, después se decidió que el padrón vivía solo
-- en Redis y el archivo se reemplazó por `007_padron_solo_redis.sql`. El
-- registro quedó apuntando a un archivo que no existe.
--
-- No rompe nada hoy porque nadie corre migraciones al arrancar, pero es
-- exactamente el tipo de basura que confunde cuando alguien audita qué se
-- aplicó y qué no.
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── 1. Rescatar los chats que solo están en la tabla vieja ───────────────────
--
-- Se compara por (contenido, created_at) porque los `id` son UUID generados en
-- cada tabla: el mismo mensaje copiado tiene id distinto de los dos lados.
INSERT INTO chat_messages_vendedores
    (numero, rol, contenido, vendedor_id, vendedor_nombre,
     session_id, tipo, tools, latencia_ms, created_at)
SELECT m.numero, m.rol, m.contenido, m.vendedor_id, m.vendedor_nombre,
       m.session_id, m.tipo, m.tools, m.latencia_ms, m.created_at
  FROM chat_messages m
 WHERE NOT EXISTS (
     SELECT 1 FROM chat_messages_vendedores v
      WHERE v.contenido = m.contenido
        AND v.created_at = m.created_at
 );

-- ── 2. Ahora sí, la tabla vieja ──────────────────────────────────────────────
DROP TABLE IF EXISTS chat_messages;

-- ── 3. Lo que quedó de la 001 ────────────────────────────────────────────────
--
-- El orden lo mandan las FK: `messages` y `claims` apuntan a `conversations`,
-- y `conversations` apunta a `users`. Igual va CASCADE por si quedó algún
-- índice o constraint colgando.
DROP TABLE IF EXISTS messages       CASCADE;
DROP TABLE IF EXISTS claims         CASCADE;
DROP TABLE IF EXISTS conversations  CASCADE;
DROP TABLE IF EXISTS users          CASCADE;

-- `knowledge_base` es la tabla de RAG del diseño original, con embeddings de
-- 1536 y un índice ivfflat. La reemplazaron las `conocimiento_*` de la 004, que
-- usan HNSW. Nunca tuvo una fila.
DROP TABLE IF EXISTS knowledge_base CASCADE;

-- ── 4. El registro huérfano ──────────────────────────────────────────────────
DELETE FROM migraciones WHERE nombre = '007_padron_visto.sql';

-- ── 5. Dejar constancia de esta ──────────────────────────────────────────────
INSERT INTO migraciones (nombre) VALUES ('010_limpieza_tablas_muertas.sql')
    ON CONFLICT DO NOTHING;

COMMIT;

-- Después de esto la base queda en 10 tablas:
--
--     chat_messages_vendedores   chat_messages_clientes   chat_messages_supervisores
--     conocimiento_vendedores    conocimiento_clientes    conocimiento_supervisores
--     vendedores                 clientes                 supervisores
--     migraciones
--
-- Tres por multiagente más el registro. Nada suelto.
