# CLAUDE.md — Agente IA Catusita

Sistema multiagente de WhatsApp para Grupo Catusita (repuestos automotrices, Perú).
Canal **WAHA** (self-hosted), **FastAPI + LangGraph**, **Claude**, **Postgres + pgvector**, **Redis**.

Rama de trabajo y de despliegue: `arquitectura-vertical`.

---

## La regla que ordena todo: una carpeta = un contenedor

Cada servicio se despliega copiando **UNA sola carpeta**. No hay paquete compartido
que importar. Si la imagen construye, el servicio corre.

| servicio | Dockerfile | arranca con |
|---|---|---|
| `catusita-recepcion` | `recepcion/Dockerfile` | `uvicorn recepcion.main:app` — el único con puerto |
| `catusita-vendedores` | `vendedores/Dockerfile` | `python -m vendedores.plataforma_vendedores.worker` |
| `catusita-clientes` | `clientes/Dockerfile` | `python -m clientes.plataforma_clientes.worker` |
| `catusita-supervisores` | `supervisores/Dockerfile` | `python -m supervisores.plataforma_supervisores.worker` |
| panel | `panel/Dockerfile` | `uvicorn panel.main:app` — solo lectura |
| yahuar | `yahuar/Dockerfile` | `python -m yahuar.servicio` — consulta de placas |

**No agregues imports entre carpetas hermanas.** `vendedores/` no importa nada de
`clientes/` ni de `recepcion/`. El día que lo haga, su Dockerfile deja de construir.

---

## Cómo circula un mensaje

```
WAHA ──► recepcion ──LPUSH "<multiagente>"──► Redis ──BRPOP──► worker
           ▲                                                     │
           └────── BRPOP "respuestas:<multiagente>" ◄────────────┘
```

Recepción resuelve el destino con el **padrón** (`HGET padron <numero>` en Redis;
cada multiagente publica sus números al arrancar) y encola. Nunca corre el agente:
tiene que contestarle a WAHA en milisegundos o WAHA reintenta por timeout.

Ante cualquier duda —número sin publicar, sesión desconocida, Redis caído— rutea a
`clientes`. Ninguna rama llega a `vendedores` por descarte.

**Esa cola es la única frontera de proceso del sistema.** Los workers no tienen
puerto y no le hablan a WAHA: un worker con `WAHA_API_KEY` significa que alguien
rompió el aislamiento.

---

## Adentro de un multiagente

```
(del router) ─► contexto ─► orquestador ─► validar ─► respuesta
                              │    ▲
                              ▼    │
                           (un área)
```

El **orquestador** es el único que le habla al usuario y el único que coordina. Ve
*áreas* descritas por la pregunta que contestan, no tools sueltas. Delega con
`Command(goto=<area>)`: un salto en memoria, en el mismo proceso — por eso las
áreas **no** tienen cola propia.

Las áreas no se hablan entre sí. Si a un área le falta un dato de otra, vuelve al
orquestador. Se fuerza en el constructor del grafo: de cada área sale UNA arista y
va al orquestador.

**Las áreas se descubren, no se listan.** `registro.py` recorre `agentes/*/` y entra
la que declare `MODELO`, `TOOLS` y `DESCRIPCION`. Un área sin `NODO` compilado se
omite de la delegación con un warning — existe en disco pero no en el grafo.

Cada área es una carpeta: `prompt.py`, `agente.py`, `tools.py`, `backend.py`,
`contratos.py`, `tests/`.

Estado hoy:

```
vendedores    6/6 áreas    clientes conocimiento facturacion pedidos productos vehiculos
clientes      2/5 áreas    conocimiento productos    (postventa, recomendaciones, vehiculos sin NODO)
supervisores  1/1 área     conocimiento
```

---

## El RAG de procesos no es un extra

Los procedimientos viven en las tablas `conocimiento_<multiagente>` con tres campos:
cómo lo **pide** el usuario (`descripcion`, lo único que se embebe), qué **es**
(`proceso`) y qué **hacer** (`procedimiento`). El nodo `contexto` se los sirve al
orquestador en cada turno.

El criterio de qué va dónde: **si se puede corregir sin redeployar, va en el RAG.**
El prompt lleva lo transversal (quién es, cómo habla, qué no revela).

Si no hay procedimiento escrito, el agente atiende igual — no es un freno.

---

## Las migraciones NO se corren solas

Los `.sql` de `db/migrations/` se aplican **a mano**, desde desarrollo, mirando el
resultado. La tabla `migraciones` registra cuáles corrieron.

Esto no es pereza: un corredor automático al arrancar reejecutaba todo en cada
deploy, y las tablas dropeadas a mano volvían solas en el siguiente push sin un
solo error. Además los tres workers comparten base — serían tres procesos haciendo
`ALTER TABLE` sobre lo mismo.

`db/` es solo las migraciones. Cada vertical tiene su propio pool en
`plataforma_<x>/db/`; el panel tiene el suyo en `panel/db.py` y no expone `init_db()`.

---

## Variables de entorno

No son las mismas para todos, **y eso es la arquitectura**:

```
recepcion     REDIS_URL  WAHA_BASE_URL  WAHA_API_KEY  WAHA_SESSION  WAHA_WEBHOOK_TOKEN
              WAHA_SESSION_{VENDEDORES,CLIENTES,SUPERVISORES}  YAHUAR_NUMBER  YAHUAR_LID

workers       ANTHROPIC_API_KEY  OPENAI_API_KEY  DATABASE_URL  REDIS_URL  CATUSITA_API_URL
(los tres)    ANTHROPIC_MODEL  MODELO_ORQUESTADOR  EMBEDDING_MODEL
              LANGGRAPH_RECURSION_LIMIT  LIMITE_PASOS_AREA
              + solo clientes: SAP_BASE_URL  SAP_API_KEY

panel         DATABASE_URL  PANEL_PASSWORD  PANEL_SECRET  PANEL_CORS  PANEL_TOKEN_TTL

yahuar        ANTHROPIC_API_KEY  REDIS_URL  WAHA_*  YAHUAR_MODELO_VISION
              YAHUAR_DEBOUNCE  YAHUAR_ESPERA_MAX
```

`OPENAI_API_KEY` es **solo** para los embeddings del RAG. Todo el razonamiento es
Claude. Cambiar `EMBEDDING_MODEL` obliga a re-embeber `conocimiento_*`.

El `.env` nunca va al repo.

---

## Antes de dar algo por terminado

```bash
python -m pytest -q --no-header
```

Y que los seis importen — es lo que rompe primero cuando se cruza una frontera:

```bash
for m in recepcion.main panel.main yahuar.servicio \
         vendedores.plataforma_vendedores.worker \
         clientes.plataforma_clientes.worker \
         supervisores.plataforma_supervisores.worker; do
  python -c "import importlib; importlib.import_module('$m')" && echo "OK $m"
done
```

Para desplegar, diagnosticar builds o mover el webhook de WAHA: skill
`despliegue-contenedores`.
