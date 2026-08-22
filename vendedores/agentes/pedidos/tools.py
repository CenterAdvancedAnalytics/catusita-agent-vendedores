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
async def pedidos_del_cliente(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    estado: Optional[str] = None,
) -> Command:
    """Los PEDIDOS de un cliente, uno por uno: fecha, monto, estado, N° de
    factura SUNAT, estado de despacho y notas de crédito.

    Para «¿qué pedidos tiene?», «¿cuánto me compró?», «¿cuál fue el último?».

    Devuelve los pedidos como documentos, NO qué productos traían adentro. Para
    eso es `productos_mas_comprados`.

    `cliente` puede ser el RUC o el nombre — se resuelve dentro de la cartera
    del asesor. La búsqueda es POR CLIENTE, no por número de pedido."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(await backend.pedidos(ruc, estado), tool_call_id)


@tool
async def productos_mas_comprados(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedidos: Optional[int] = None,
) -> Command:
    """QUÉ PRODUCTOS compra un cliente: los SKU que más lleva, rankeados.

    Para «¿qué le vendo más?», «¿qué suele llevar?», «¿en qué gasta más?».
    `cliente` es el RUC o el nombre.

    NO devuelve la marca. Si preguntan por marca, es `marcas_mas_compradas`.

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

    ── Los dos son un TOP 10, no la lista entera ──────────────────────────────

    Cuántos productos hay en total está en `skus_distintos`, y cuánto suman
    TODOS está en `total_monto`. Los dos números salen de acá ya calculados.

    No sumes la tabla para sacar el total: te va a dar de menos, porque le
    faltan las filas que no se muestran. Un cliente con 42 SKU muestra 10, y
    esos 10 pueden ser tres cuartos de su volumen o la mitad.

    Si `skus_distintos` es mayor que las filas que mostrás, decilo. El asesor
    tiene que saber que está viendo un recorte.

    (Cuando el cliente tiene facturas en más de una moneda no viene
    `total_monto` a propósito: sumarlas sería inventar un número. En ese caso
    llega `ojo_monedas` explicando qué hacer.)

    Decí siempre sobre cuántos pedidos se calculó (`pedidos_revisados`):
    «compra Valvoline» no significa nada sin «en sus últimos 10 pedidos».

    Es lo que YA compró, no una predicción. Que el asesor decida qué le ofrece."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(
        await backend.compras(ruc, cantidad=pedidos or backend.PEDIDOS_POR_DEFECTO),
        tool_call_id)


@tool
async def marcas_mas_compradas(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedidos: Optional[int] = None,
) -> Command:
    """Qué MARCAS compra un cliente, ordenadas por plata.

    Para «¿qué marca compra más?», «¿de qué marca le vendemos?», «¿es cliente
    de Valvoline o de Sakura?».

    `cliente` es el RUC o el nombre. Devuelve `por_marca` con el monto, las
    unidades, cuántos SKU distintos y el `porcentaje` que pesa cada una — ya
    sumado sobre TODOS sus productos, no sobre una muestra.

    Los totales y los porcentajes vienen calculados: copialos. No estimes ni
    dividas. Todo número que no esté en el payload es un número inventado.

    Van TODAS las marcas, incluida `(sin marca en el catálogo)`. Son pocas y son
    la respuesta. Si recortás una, el total de la tabla deja de ser el total y
    el asesor suma mal sin saberlo.

    Decí siempre sobre cuántos pedidos se calculó (`pedidos_revisados`).
    «Compra AISIN» no significa nada sin «en sus últimos 10 pedidos».

    Si preguntan por PRODUCTOS y no por marcas, usá `productos_mas_comprados`."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(
        await backend.marcas(ruc, cantidad=pedidos or backend.PEDIDOS_POR_DEFECTO),
        tool_call_id)


@tool
async def estado_de_despacho(
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedido_id: Optional[str] = None,
    factura: Optional[str] = None,
) -> Command:
    """Si un pedido ya se entregó: guía de remisión, fecha de despacho y de
    entrega. Para «¿ya llegó?», «¿se entregó?», «¿en qué va el despacho?».

    NO funciona por RUC. Necesita el N° de pedido o el de factura: si solo
    tenés el cliente, usá `pedidos_del_cliente` primero para sacar sus
    números."""
    if not pedido_id and not factura:
        return _responder({
            "error": "FALTA_DATO",
            "mensaje": ("Se necesita el número de pedido o el de factura. "
                        "Con el RUC no alcanza: usá `pedidos_del_cliente` "
                        "primero."),
        }, tool_call_id)
    return _responder(await backend.despacho(pedido_id, factura), tool_call_id)


TOOLS = [pedidos_del_cliente, productos_mas_comprados,
         marcas_mas_compradas, estado_de_despacho]
