"""Subgrafo de `conocimiento` (clientes).

    (entra del orquestador) -> agente ⇄ tools -> (vuelve al orquestador)

Salidas permitidas (las fuerza clientes/grafo.py):
  - SOLO vuelve al orquestador
  - NO va a otra área
  - NO va a END: toda respuesta pasa por validar

── Por qué el modelo más chico de todos ───────────────────────────────────────

Porque acá no se razona: se busca y se presenta. El único juicio del área es si
lo que volvió aplica o no; si no aplica, dice «no sé» y listo. Decidir qué hacer
—seguir el procedimiento, o resolverlo sin uno— es del orquestador, que además
ve el resto del turno.
"""
import os

from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition

from clientes.agentes.conocimiento.prompt import SYSTEM
from clientes.agentes.conocimiento.tools import TOOLS
from clientes.plataforma_clientes.estado import EstadoAgente

MODELO = "claude-haiku-4-5-20251001"

LIMITE_PASOS = int(os.getenv("LIMITE_PASOS_AREA", "6"))

_llm = ChatAnthropic(model=MODELO, temperature=0).bind_tools(TOOLS)


async def _nodo_agente(state: EstadoAgente) -> dict:
    respuesta = await _llm.ainvoke(
        [{"role": "system", "content": SYSTEM}] + state["messages"]
    )
    return {"messages": [respuesta]}


def construir() -> StateGraph:
    g = StateGraph(EstadoAgente)
    g.add_node("agente", _nodo_agente)
    g.add_node("tools", ToolNode(TOOLS))

    g.set_entry_point("agente")
    g.add_conditional_edges("agente", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agente")
    return g


NODO = construir().compile()
