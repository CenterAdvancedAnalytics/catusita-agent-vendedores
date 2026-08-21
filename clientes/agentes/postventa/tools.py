"""Tools internas de `postventa` (clientes). El orquestador NO las ve.

SIN IMPLEMENTAR. `registrar_reclamo` levanta NotImplementedError y el área no
compila NODO, así que `registro.tools_de_delegacion()` la omite: el orquestador
de clientes no la ve y nadie puede saltar acá. Queda declarada para que el día
que se implemente el contrato ya esté escrito.
"""
from typing import Annotated

from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command


@tool
async def registrar_reclamo(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Registra un reclamo y devuelve su número de caso."""
    raise NotImplementedError


TOOLS = [registrar_reclamo]
