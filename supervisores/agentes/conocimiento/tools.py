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
    ninguna otra área. Pasa la consulta del usuario TAL CUAL, sin reformularla.

    Lo que vuelve son CANDIDATOS, no respuestas. Cada uno trae `cubre`, que dice
    en qué situación aplica: comparalo con lo que se preguntó y descartá los que
    no correspondan."""
    procesos = await servicio.buscar(consulta)

    if not procesos:
        # «No sé», pero diciendo qué hacer con ese «no sé».
        #
        # Un `encontrado: False` a secas se lee como un freno, y el orquestador
        # tiende a trasladárselo al usuario: «no tengo información sobre eso».
        # Que no exista un procedimiento escrito no es que no se pueda resolver
        # — es que no hay una forma oficial y hay que usar criterio.
        return _responder({
            "candidatos": [],
            "mensaje": (
                "No hay ningún proceso escrito para esto. No es un impedimento: "
                "resolvelo con tus áreas y tu criterio. Lo único que no cambia "
                "es que no inventes datos ni autorices nada."
            ),
        }, tool_call_id)

    # ── Por qué viaja `cubre` (la `descripcion` del proceso) ──────────────────
    #
    # Es el campo que dice PARA QUÉ SITUACIÓN existe el proceso, y es lo único
    # con lo que se puede decidir si aplica. Sin él, el modelo recibe un
    # procedimiento suelto y no tiene contra qué contrastarlo: lo pasa siempre.
    #
    # Y pasarlo siempre es exactamente el problema. La búsqueda es por coseno y
    # devuelve lo más parecido, no lo correcto — medido: «Deseo saber mi cartera
    # de clientes» recupera el proceso de «cartera de OTRO asesor» con 0.543, y
    # ese proceso dice que no se entrega. Sin nadie que lo mire, el asesor
    # termina recibiendo una negativa sobre su propia cartera.
    #
    # El umbral es un filtro grueso. El fino es leer `cubre` y descartar.
    return _responder({
        "candidatos": [
            {"proceso": p["proceso"],
             "cubre": p["descripcion"],
             "procedimiento": p["procedimiento"],
             "similitud": round(p["similitud"], 3)}
            for p in procesos
        ],
        "mensaje": (
            "Son candidatos por parecido de texto, no respuestas verificadas. "
            "Para cada uno, mirá `cubre` y preguntate si describe la MISMA "
            "situación que se consultó. Si ninguno aplica, decilo — es una "
            "respuesta válida y mejor que seguir un procedimiento equivocado."
        ),
    }, tool_call_id)


TOOLS = [buscar_conocimiento]
