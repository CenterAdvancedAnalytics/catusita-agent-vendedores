"""Subgrafo de `pedidos` (vendedores).

    (entra del orquestador) -> agente ⇄ tools -> (vuelve al orquestador)

Salidas permitidas (las fuerza vendedores/grafo.py):
  - SOLO vuelve al orquestador
  - NO va a otra área
  - NO va a END: toda respuesta pasa por `validar`

Recibe el estado del turno con `messages` REEMPLAZADO por la consulta que
escribió el orquestador: arranca limpio, sin historial ni lo que dijeron otras
áreas. De vuelta sale un ToolMessage con su texto final, más `media_pendiente`,
que tiene reducer de suma. Lo hace `_envolver_area` en el grafo del multiagente.
"""
import os

from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition

from vendedores.agentes.pedidos.prompt import SYSTEM
from vendedores.agentes.pedidos.tools import TOOLS
from vendedores.plataforma_vendedores.estado import EstadoAgente

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
    # END acá es el final del SUBGRAFO, no del turno.
    g.add_conditional_edges("agente", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agente")
    return g


NODO = construir().compile()
