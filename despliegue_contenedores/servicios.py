"""Qué contenedores existen y qué necesita cada uno.

Este archivo es la fuente de verdad del despliegue. Antes esa información vivía
solo en la UI de Railway: abrías el repo y no había forma de saber que esto
corre en cuatro contenedores, ni qué Dockerfile usa cada uno, ni qué variables
necesita. Estaba todo en un panel web, que es exactamente donde nadie mira
cuando algo falla a las 11 de la noche.

── Un repo, una rama, cuatro imágenes ─────────────────────────────────────────

Los cuatro servicios se despliegan del MISMO commit. Lo único que cambia entre
ellos es qué Dockerfile construyen:

    catusita-recepcion      recepcion/Dockerfile      uvicorn, con puerto
    catusita-vendedores     vendedores/Dockerfile     worker, sin puerto
    catusita-clientes       clientes/Dockerfile       worker, sin puerto
    catusita-supervisores   supervisores/Dockerfile   worker, sin puerto

Que sea el mismo commit no es casualidad: `contrato.py` está duplicado en los
cuatro y su VERSION tiene que coincidir. Recepción encola un Turno y el worker
lo lee; si estuvieran en ramas distintas, una podría avanzar a v4 y la otra
quedarse en v3, y el `VersionIncompatible` dejaría de ser una red de seguridad
para pasar a ser el estado normal.

── Por qué el Root Directory va vacío ─────────────────────────────────────────

Es tentador poner `vendedores/` como Root Directory. No funciona: el Dockerfile
copia `requirements.txt` y `vendedores/`, y las dos rutas arrancan en la raíz
del repo. El contexto de build tiene que ser la raíz; lo que cambia es el
Dockerfile Path.

── Qué variables lleva cada uno, y por qué NO son las mismas ──────────────────

Las diferencias son la arquitectura, no un olvido:

    recepción     lleva WAHA_*        es el único que le habla a WhatsApp
                  NO lleva OPENAI     no toca el RAG ni Postgres

    workers       llevan OPENAI       el RAG de procesos corre en cada turno
                  NO llevan WAHA_*    no saben que WhatsApp existe: dejan el
                                      Resultado en una cola y siguen

Si un worker terminara con WAHA_API_KEY, sería la señal de que alguien le hizo
mandar un mensaje directo — y ahí se rompe la única frontera de proceso que
tiene el sistema.
"""

REPO = "gabrielcz6/catusita-agent-vendedores"
RAMA = "arquitectura-vertical"

# El servicio del que se copian las variables compartidas (DATABASE_URL, las
# API keys). Es el monolito viejo, que sigue en producción y las tiene todas.
FUENTE = "catusita-agent"

# Conexiones e identidad. Las necesita todo lo que corre código nuestro.
COMUNES = ["DATABASE_URL", "REDIS_URL", "ANTHROPIC_API_KEY", "SAP_BASE_URL", "SAP_API_KEY"]

# Embeddings del RAG de procesos. Solo los workers: recepción no toca Postgres.
RAG = ["OPENAI_API_KEY"]

# WhatsApp. Solo recepción.
CANAL = ["WAHA_BASE_URL", "WAHA_API_KEY", "WAHA_SESSION", "WAHA_WEBHOOK_TOKEN"]


SERVICIOS = {
    "catusita-recepcion": {
        "dockerfile": "recepcion/Dockerfile",
        "variables": COMUNES + CANAL,
        "watch": "recepcion/**",
        "rol": "recibe de WAHA, rutea por el padrón, acumula y responde",
        "publico": True,     # único con dominio: es el que recibe el webhook
    },
    "catusita-vendedores": {
        "dockerfile": "vendedores/Dockerfile",
        "variables": COMUNES + RAG,
        "watch": "vendedores/**",
        "rol": "worker: lee la cola `vendedores` y corre su grafo",
        "publico": False,
    },
    "catusita-clientes": {
        "dockerfile": "clientes/Dockerfile",
        "variables": COMUNES + RAG,
        "watch": "clientes/**",
        "rol": "worker: lee la cola `clientes` y corre su grafo",
        "publico": False,
        # 2 áreas vivas: `productos` (precio de lista, sin almacén ni neto) y
        # `conocimiento`. `vehiculos`, `postventa` y `recomendaciones` siguen
        # con sus tools en NotImplementedError — el orquestador no las ve
        # porque el registro solo delega en áreas con NODO compilado.
        "listo": True,
    },
    "catusita-supervisores": {
        "dockerfile": "supervisores/Dockerfile",
        "variables": COMUNES + RAG,
        "watch": "supervisores/**",
        "rol": "worker: lee la cola `supervisores` y corre su grafo",
        "publico": False,
        # 1 área: `conocimiento`. Alcanza para contestar cómo se hace algo en
        # Catusita, que es todo lo que este multiagente hace hoy. Lo de fondo
        # —si un supervisor ve la cartera de SUS asesores— sigue sin decidirse.
        "listo": True,
    },
}


# Servicios que ya no usa nadie y siguen encendidos. Están acá para que la lista
# no se pierda: los dos quedaron de etapas anteriores.
#
#   evolution-api        el canal de WhatsApp anterior a WAHA. Borrarlo necesita
#                        permisos de Carlos Gamero.
#   mock-sap-catusita    el mock de datos. Hoy SAP_BASE_URL apunta a
#                        tools-agente-catusita, que es el servicio real.
MUERTOS = ["evolution-api", "mock-sap-catusita"]


def desplegables() -> dict:
    """Los que tienen su código listo. Los demás se saltean con una razón."""
    return {n: s for n, s in SERVICIOS.items() if s.get("listo", True)}
