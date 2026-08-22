"""Tools de `pedidos` (vendedores): qué pidió un cliente y dónde está."""
import json
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from vendedores.agentes.pedidos import backend
from vendedores.plataforma_vendedores import acceso


def _responder(resultado: dict, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(
        content=json.dumps(resultado, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id)]})


@tool
async def consultar_pedidos(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    estado: Optional[str] = None,
) -> Command:
    """Pedidos de un cliente: estado, N° de factura SUNAT, estado de despacho y
    notas de crédito.

    `cliente` puede ser el RUC o el nombre — se resuelve dentro de la cartera
    del asesor. La búsqueda es POR CLIENTE, no por número de pedido."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(await backend.pedidos(ruc, estado), tool_call_id)


@tool
async def consultar_compras(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedidos: Optional[int] = None,
) -> Command:
    """QUÉ PRODUCTOS compra un cliente: los SKU que más lleva.

    Para «¿qué le vendo más?», «¿qué suele llevar?», «¿en qué gasta más?».
    `cliente` es el RUC o el nombre.

    NO devuelve la marca. Si preguntan por marca, es `consultar_marcas`.

    La descripción de la factura a veces nombra una marca, y a veces esa marca
    NO es la del catálogo — medido: 3 de 18 productos de un cliente decían
    «NARVA» y el catálogo los tiene como ENERTECH. No la uses.

    ── Cómo leer lo que devuelve ──────────────────────────────────────────────

    `por_monto` y `por_unidades` son el MISMO conjunto ordenado distinto, y no
    dan lo mismo: un foco puede ser 6º en plata y 1º en cantidad.

      · el asesor quiere saber qué reponerle  ->  por_unidades
      · quiere saber dónde está la plata      ->  por_monto

    Si no se entiende de la pregunta, mostrá `por_monto` y ofrecé el otro en una
    línea. No lo hagas escribir de nuevo por algo que ya se entendía.

    Los totales YA vienen sumados: no los recalcules ni los estimes. Podés
    filtrar y reordenar lo que te llega, pero los números salen de acá.

    Deci siempre sobre cuántos pedidos se calculó: «compra Valvoline» no
    significa nada sin «en sus últimos 10 pedidos».

    Es lo que YA compró, no una predicción. Que el asesor decida qué le ofrece."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(
        await backend.compras(ruc, cantidad=pedidos or backend.PEDIDOS_POR_DEFECTO),
        tool_call_id)


@tool
async def consultar_marcas(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedidos: Optional[int] = None,
) -> Command:
    """Qué MARCAS compra un cliente, ordenadas por plata.

    Para «¿qué marca compra más?», «¿de qué marca le vendemos?», «¿es cliente
    de Valvoline o de Sakura?».

    `cliente` es el RUC o el nombre. Devuelve `por_marca` con el monto, las
    unidades y cuántos SKU distintos lleva de cada una — ya sumado sobre TODOS
    sus productos, no sobre una muestra.

    Los totales vienen calculados: copialos, no los recalcules.

    Si preguntan por PRODUCTOS y no por marcas, usá `consultar_compras`."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(
        await backend.marcas(ruc, cantidad=pedidos or backend.PEDIDOS_POR_DEFECTO),
        tool_call_id)


@tool
async def consultar_despacho(
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedido_id: Optional[str] = None,
    factura: Optional[str] = None,
) -> Command:
    """Si un pedido ya se entregó: guía de remisión, fecha de despacho y de
    entrega. Para «¿ya llegó?», «¿se entregó?», «¿en qué va el despacho?».

    NO funciona por RUC. Necesita el N° de pedido o el de factura: si solo
    tenés el cliente, usá consultar_pedidos primero para sacar sus números."""
    if not pedido_id and not factura:
        return _responder({
            "error": "FALTA_DATO",
            "mensaje": ("Se necesita el número de pedido o el de factura. "
                        "Con el RUC no alcanza: usá consultar_pedidos primero."),
        }, tool_call_id)
    return _responder(await backend.despacho(pedido_id, factura), tool_call_id)


TOOLS = [consultar_pedidos, consultar_compras, consultar_marcas, consultar_despacho]
