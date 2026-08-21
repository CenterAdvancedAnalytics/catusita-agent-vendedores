"""Tools de `clientes` (vendedores): quién es este cliente y es mío.

EXPERIMENTO: sin capa `backend`. Cada tool es su endpoint de Catusita y nada
más — se manda lo que la API devuelve, crudo, con sus nombres de campo.

Lo único que queda entre el modelo y la API es el control de acceso, que no es
transformación de datos: `consultar_perfil_cliente` verifica cartera ANTES de
pegarle al endpoint, y `consultar_cartera` saca el `vendedor_id` del state para
que el modelo no pueda pedir la cartera de otro.
"""
import json
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from vendedores.plataforma_vendedores import acceso, catusita_api


def _responder(resultado, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(
        content=json.dumps(resultado, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id)]})


@tool
async def consultar_cartera(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Los clientes asignados a este asesor.

    Devuelve la cartera COMPLETA tal como la da Catusita: una fila por cliente
    con `rucClient`, `nameClient`, `address`, `locality`, `codeClient` y `email`.

    Usar SIEMPRE que pregunten por «mis clientes», «mi cartera», «cuántos
    clientes tengo». Nunca contestar eso de memoria."""
    vendedor_id = (state.get("perfil") or {}).get("vendedor_id")
    if not vendedor_id:
        return _responder({"error": "SIN_VENDEDOR"}, tool_call_id)
    return _responder(
        await catusita_api.get("/api/client/CustomerbySeller",
                               {"SellerId": vendedor_id}),
        tool_call_id)


@tool
async def consultar_perfil_cliente(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Perfil de UN cliente, tal como lo da Catusita.

    `cliente` puede ser el RUC o el nombre — se resuelve dentro de la cartera
    del asesor. Si el cliente no es suyo, la consulta no se ejecuta."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(
        await catusita_api.get("/api/client/CustomerbyFilter", {"RUCClient": ruc}),
        tool_call_id)


TOOLS = [consultar_cartera, consultar_perfil_cliente]
