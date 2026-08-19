"""RAG de procesos. Un paso hoy, varios mañana.

    async def buscar(consulta):
        vector = await backend.embeber(consulta)      # la consulta del usuario, sin tocar
        procesos = await backend.similares(vector, k=K)
        return [p for p in procesos if p["similitud"] >= UMBRAL]

`multiagente` no es parámetro: la tabla del backend es la de este multiagente.

── Qué pasa cuando no encuentra ───────────────────────────────────────────────

Devuelve una lista vacía y nada más. No registra, no avisa, no abre nada.

El que pregunta —`contexto` o la tool— traduce ese vacío a un «no sé», y el
orquestador sigue trabajando con lo que tiene: sus áreas y su criterio. El RAG de
procesos es una guía, no un permiso: que no haya procedimiento escrito no
significa que no se pueda atender la consulta.

Detectar qué procesos faltan se hace aparte, analizando los chats. No es trabajo
de este camino, que corre adentro del turno y tiene que ser rápido.

── Los pasos que vendrán ──────────────────────────────────────────────────────

Se insertan acá, en este orden y midiendo cada uno:

    1. rerank        reordenar los k candidatos con un modelo de reranking
    2. híbrida       sumar búsqueda por texto para códigos y nombres propios
    3. reformular    reescribir la consulta antes de embeber
    4. loop          evaluar lo recuperado y reintentar con otro término

Se agregan de a uno. Con RAG, un cambio que suena obviamente bueno empeora la
recuperación tan seguido como la mejora, y si entran dos juntos no se sabe cuál
fue.

El paso 2 tiene un caso concreto medido: 'nota de crédito' contra 'NC' da 0.433
de similitud, más bajo que 'nota de crédito' contra 'más crédito' (0.550), que
no tienen nada que ver. Las abreviaturas y los códigos son el punto ciego del
embedding, y ahí es donde la búsqueda por texto gana.
"""
from supervisores.agentes.conocimiento import backend

K = 5

# Debajo de esto se considera que NO hay proceso. `contexto` usa este mismo, no
# una copia: si los dos caminos se separan, el mismo mensaje encuentra proceso
# por uno y no por el otro.
#
# ── Por qué 0.50 y no menos ───────────────────────────────────────────────────
#
# Devolver un proceso que NO aplica es peor que no devolver ninguno. El
# orquestador trata lo que le llega como el procedimiento oficial —«seguilo, no
# improvises lo que ya está escrito»— así que un proceso de devoluciones
# recuperado para una consulta de horarios lo hace ejecutar pasos equivocados
# con toda seguridad.
#
# Sin proceso, en cambio, improvisa: usa sus áreas y su criterio. Eso puede
# salir peor o mejor, pero no arrastra la autoridad de un procedimiento escrito.
#
# Entre los dos errores, el barato es no recuperar.
#
# ── Lo que se midió (con procesos de prueba, no reales) ───────────────────────
#
#     0.697   'quiere comprar 5000 y solo tiene 2000 de linea'  -> límite de crédito
#     0.463   'me llego el filtro todo abollado'                -> devolución por daño
#     0.225   'a que hora abre el local'                        -> nada, y está bien
#
# Con 0.50, el segundo caso NO se recupera. Es el costo de subir el umbral y
# está asumido: ese 0.463 salía de una `descripcion` que yo escribí, no de una
# real. Con text-embedding-3-small las similitudes corren bajo, y la forma de
# subirlas es redactar mejor la `descripcion`, no aflojar el corte.
#
# ── De qué depende el margen ──────────────────────────────────────────────────
#
# De cómo esté escrita la `descripcion`, que es lo único que se embebe. El
# usuario escribe «me llegó abollado», no «tramitar devolución por daño en
# transporte». Redactarla en idioma de manual bajó la similitud de 0.463 a 0.390
# en la misma consulta — medido, no estimado.
#
# Recalibrar cuando haya procesos reales cargados, midiendo contra consultas
# reales de los chats.
UMBRAL = 0.50


async def buscar(consulta: str) -> list[dict]:
    """Procesos que aplican a la consulta. Lista vacía si ninguno es confiable.

    ── «No encontrar nada» hay que DEFINIRLO ──────────────────────────────────

    Una búsqueda por coseno siempre devuelve un top-k: aunque la tabla esté
    llena de procesos de despacho y pregunten por vacaciones, algo vuelve. Lo
    que no hay es un resultado *bueno*. Por eso existe UMBRAL, y por debajo se
    trata como vacío.

    Sin ese corte, el orquestador recibiría el proceso de devoluciones para una
    consulta de horarios y lo seguiría — que es peor que no tener ninguno.
    """
    vector = await backend.embeber(consulta)
    if not vector:
        return []

    procesos = await backend.similares(vector, k=K)

    # Todos los que pasan el umbral, no solo el primero: una consulta puede
    # tocar dos procedimientos («llegó roto Y fuera de fecha»).
    return [p for p in procesos if p["similitud"] >= UMBRAL]
