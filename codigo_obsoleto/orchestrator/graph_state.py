"""Estado del grafo LangGraph para el agente Catusita."""
from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages


def _media_reducer(current: list | None, update: list | None) -> list:
    """Acumula items de media (imágenes, PDFs) a enviar por WhatsApp."""
    return (current or []) + (update or [])


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    perfil: dict
    canal: str                      # "vendedor" | "cliente"
    media_pendiente: Annotated[list, _media_reducer]
    # Fase 4: entidades pre-resueltas (cliente, RUC, pedido, placa) antes del LLM
    contexto_resuelto: dict
    # Fase 5: {ok: bool, motivo: str} del nodo validador
    validacion: dict
    intentos_validacion: int
