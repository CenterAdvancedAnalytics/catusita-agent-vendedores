"""Área `conocimiento` de Supervisores — cómo se hace en Catusita.

Contesta: cómo se hace cualquier cosa en Catusita.
Entidad:  procedimientos — el CÓMO, no el dato.

── Dos puertas a la misma tabla ───────────────────────────────────────────────

`conocimiento` se lee desde dos lados y conviene no confundirlos:

    plataforma/nodos/contexto.py::_procesos
        AUTOMÁTICO, en cada turno, antes de que corra el orquestador. Le deja
        servidos los procesos que aplican. Es el camino principal — el
        orquestador trabaja con esto SIEMPRE, no solo cuando se traba.

    esta área  (tool `buscar_conocimiento`)
        A PEDIDO. El orquestador la llama cuando lo que le sirvieron no alcanza:
        la consulta es rara, o hay que buscar con otros términos.

Misma tabla. Lo que cambia es quién dispara la búsqueda. Esta área existe como área —y no como una función suelta— porque acá
es donde van a crecer el rerank, la búsqueda híbrida y la reformulación, y
cuando crezcan las aprovechan las dos puertas.

Cuando el orquestador la llama, le pasa **la consulta del usuario tal cual**.

Ojo con «tal cual»: NO se reformula la pregunta antes de buscar. Lo que entra a
la búsqueda es lo que escribió el usuario. Si más adelante conviene reformular,
se agrega como un paso más de `servicio.py` y se mide contra lo que hay hoy.

── Qué se guarda acá ──────────────────────────────────────────────────────────

TODO proceso que ejecute el orquestador. No solo los raros — también los de
todos los días. No datos: PROCEDIMIENTOS. Cada fila dice cuándo aplica, cómo se
resuelve y cómo se entrega la respuesta. Por ejemplo:

    «hasta qué monto autoriza un supervisor sin pasar por gerencia»
    «cómo se reasigna la cartera cuando un asesor sale de vacaciones»
    «qué se revisa antes de aprobar una excepción de crédito»

Hoy es la ÚNICA área de este multiagente: los supervisores todavía no tienen áreas
de datos propias. Definirlas está pendiente y tiene una decisión abierta detrás
—si un supervisor puede ver la cartera de sus asesores— que toca el aislamiento
de `acceso.py`. Mientras eso no se resuelva, este multiagente solo consulta
procedimientos.

── Hoy: RAG simple ────────────────────────────────────────────────────────────

    embeber(consulta del usuario) -> top-k filtrado por multiagente -> devolver

Sin loop, sin juez, sin reintentos. Una búsqueda y ya.

── Crecimiento previsto ───────────────────────────────────────────────────────

Es un área propia justamente para que esto crezca sin tocar nada más:

    hoy        RAG simple, top-k por similitud
    después    rerank de los candidatos
               búsqueda híbrida (vector + texto exacto para códigos)
               reformulación de la consulta
               loop: buscar, evaluar, reintentar

Cada uno es un paso dentro de `servicio.py`. El contrato con el orquestador
—entra una consulta, salen documentos— no cambia con ninguno.
"""
from supervisores.agentes.conocimiento.agente import NODO, MODELO
from supervisores.agentes.conocimiento.tools import TOOLS



# Lo que el orquestador ve de esta área. Es la PREGUNTA que contesta, no
# la lista de sus tools: el orquestador delega en el ÁREA y es ella la que
# decide cuáles usar y en qué orden.
DESCRIPCION = (
    "Cómo se hace algo en Catusita. Usala cuando la consulta no caiga en ninguna de las otras áreas."
)

__all__ = ["MODELO", "DESCRIPCION", "NODO", "TOOLS"]
