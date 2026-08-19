"""Tools de `conocimiento` (clientes). El orquestador NO ve la búsqueda por dentro."""
import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.types import Command

from clientes.agentes.conocimiento import servicio


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
        # «No sé», pero diciendo qué hacer con ese «no sé».
        #
        # Un `encontrado: False` a secas se lee como un freno, y el orquestador
        # tiende a trasladárselo al usuario: «no tengo información sobre eso».
        # Que no exista un procedimiento escrito no es que no se pueda resolver
        # — es que no hay una forma oficial y hay que usar criterio.
        return _responder({
            "encontrado": False,
            "mensaje": (
                "No hay ningún proceso escrito para esto. No es un impedimento: "
                "resolvelo con tus áreas y tu criterio. Lo único que no cambia "
                "es que no inventes datos ni autorices nada."
            ),
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
