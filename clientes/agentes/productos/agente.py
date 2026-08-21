"""Subgrafo de `productos` (clientes).

    (entra del orquestador) -> agente ⇄ tools -> (vuelve al orquestador)

Un loop chico: el modelo del área decide qué tools llamar, las llama, mira el
resultado y decide si le alcanza. Cuando deja de pedir tools, termina y el
control vuelve solo — la arista de salida la pone `clientes/grafo.py`.

Salidas permitidas (las fuerza el grafo padre):
  - SOLO vuelve al orquestador
  - NO va a otra área
  - NO va a END: toda respuesta pasa por `validar`

── Qué recibe y qué devuelve ──────────────────────────────────────────────────

El estado del turno con `messages` REEMPLAZADO por la consulta que escribió el
orquestador. Arranca limpio: no ve el historial ni lo que dijeron otras áreas.
El resto del estado —`perfil`, `conversacion`— sí viaja.

De vuelta salen dos cosas:

    un ToolMessage    con el texto final de este subgrafo, contra el
                      tool_call_id de la delegación
    media_pendiente   las fotos, que tiene reducer de suma y las manda
                      recepción al final del turno

O sea que el orquestador NO ve el JSON crudo de las tools: ve lo que este
modelo escribió. Si un dato no entra en ese texto, no existe río abajo.

Lo hace `_envolver_area` en clientes/grafo.py.

── Por qué un modelo chico ────────────────────────────────────────────────────

Este nodo no razona sobre el negocio: elige entre cuatro tools y devuelve lo
que salió. El razonamiento —cruzar áreas, decidir qué se le explica al cliente—
es del orquestador, que sí corre con un modelo grande.
"""
import os

from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition

from clientes.agentes.productos.prompt import SYSTEM
from clientes.agentes.productos.tools import TOOLS
from clientes.plataforma_clientes.estado import EstadoAgente

MODELO = "claude-haiku-4-5-20251001"

# Tope de vueltas del loop. Cuatro tools no necesitan más: si a la sexta no
# resolvió, está reintentando algo que no va a salir.
LIMITE_PASOS = int(os.getenv("LIMITE_PASOS_AREA", "6"))

_llm = ChatAnthropic(model=MODELO, temperature=0).bind_tools(TOOLS)


async def _nodo_agente(state: EstadoAgente) -> dict:
    """Un paso del área: mirar lo que hay y decidir qué tool llamar."""
    respuesta = await _llm.ainvoke(
        [{"role": "system", "content": SYSTEM}] + state["messages"]
    )
    return {"messages": [respuesta]}


def construir() -> StateGraph:
    g = StateGraph(EstadoAgente)
    g.add_node("agente", _nodo_agente)
    g.add_node("tools", ToolNode(TOOLS))

    g.set_entry_point("agente")
    # END acá es el final del SUBGRAFO, no del turno: al salir, el grafo padre
    # devuelve el control al orquestador.
    g.add_conditional_edges("agente", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agente")
    return g


NODO = construir().compile()
