"""Estado que viaja por el grafo. Lo comparten los tres multiagentes, así que es chico.

No lleva `canal` ni `tipo`: para cuando el grafo arranca, el router ya decidió a
qué multiagente entra y el mensaje corre en el grafo de ese multiagente. Un flag de
canal adentro del estado sería justamente la frontera débil que se eliminó.
"""
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


def _media_reducer(current: list | None, update: list | None) -> list:
    """Acumula media (imágenes, PDFs) que las áreas encolan para WhatsApp."""
    return (current or []) + (update or [])


class EstadoAgente(TypedDict):
    messages: Annotated[list, add_messages]

    # Qué hilo es. Lo necesita `contexto` para saber qué historial cargar, y es
    # lo mismo que `Turno.conversacion` y que la clave del acumulador.
    # No es el canal: no dice a qué multiagente pertenece —eso ya lo decidió el
    # router— solo identifica la conversación dentro de él.
    conversacion: str

    # Lo que el orquestador le pide al área en la que acaba de delegar:
    # {"consulta": "...", "tool_call_id": "..."}. Lo escribe la tool de
    # delegación y lo consume el nodo del área, que responde con un ToolMessage
    # a ese mismo tool_call_id.
    encargo: dict

    perfil: dict          # lo resolvió el router
    historial: list       # lo trae el nodo contexto

    # Los procedimientos de Catusita que aplican a este turno. Los recupera
    # `contexto` del RAG, ANTES de que el orquestador corra, y son el «cómo se
    # hace esto acá»: cada uno trae sus `pasos` y su `entrega`.
    #
    # Lista vacía significa que no había ninguno por encima del umbral. No es
    # un error ni bloquea nada: el orquestador atiende con sus áreas y su
    # criterio. La mayoría de los turnos —saludos, «ok», «gracias»— no necesitan
    # ningún procedimiento.
    procesos: list

    # Entidades sacadas del mensaje SIN IA: RUC, pedido, placa, SKU.
    # Lo llena `contexto`. Sirve para que el orquestador no le pida al usuario un
    # dato que ya estaba escrito en su mensaje — `validar` lo usa para eso.
    # Vacío mientras no se migre el extractor (orchestrator/nodes/pre_resolver.py).
    resuelto: dict

    media_pendiente: Annotated[list, _media_reducer]

    validacion: dict          # {ok: bool, motivo: str}
    intentos_validacion: int
