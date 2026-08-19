"""Tools de `conocimiento` (supervisores). El orquestador NO ve la búsqueda por dentro."""
import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.types import Command

from supervisores.agentes.conocimiento import servicio


def _responder(resultado: dict, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(
        content=json.dumps(resultado, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id,
    )]})


@tool
async def buscar_conocimiento(
    consulta: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Busca en los procesos de Catusita cómo se resuelve algo. Úsala cuando lo
    que te sirvió el contexto no alcance, o cuando la consulta no corresponda a
    ninguna otra área. Pasa la consulta del usuario TAL CUAL, sin reformularla."""
    procesos = await servicio.buscar(consulta)

    if not procesos:
        # «No sé» y punto. No se inventa un procedimiento ni se bloquea el
        # turno: el orquestador tiene sus áreas y su criterio, y con eso
        # atiende. Que no haya proceso escrito no es que no se pueda resolver.
        return _responder({
            "encontrado": False,
            "mensaje": "No hay ningún proceso escrito para esto.",
        }, tool_call_id)

    return _responder({
        "encontrado": True,
        "procesos": [
            {"proceso": p["proceso"], "procedimiento": p["procedimiento"],
             "similitud": round(p["similitud"], 3)}
            for p in procesos
        ],
    }, tool_call_id)


TOOLS = [buscar_conocimiento]
