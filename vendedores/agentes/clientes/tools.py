"""Tools de `clientes` (vendedores): quién es este cliente y es mío.

Los endpoints están en `api.py`, uno por función. Acá va una tool por PREGUNTA,
y cada una hace lo específico que esa pregunta necesita:

    contar_cartera       ¿cuántos son?        -> el número, sin las filas
    listar_cartera       ¿quiénes son?        -> hasta 25, filtrados, con total
    consultar_perfil     ¿quién es este?      -> una fila, verificando cartera

La API no filtra por distrito ni cuenta: devuelve las 174 filas y listo. Lo que
no puede hacer ella lo hace Python acá — no el modelo, que con 174 filas
delante contestó "321 clientes" cuando eran 174.

El control de acceso va ANTES del endpoint, nunca después: consultar primero y
filtrar la respuesta significa que el dato ajeno ya salió.
"""
import json
from typing import Annotated, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool, InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from vendedores.agentes.clientes import api
from vendedores.plataforma_vendedores import acceso

# Cuántos clientes entran en un mensaje de WhatsApp sin que el asesor deje de
# leer. Para el resto están los filtros, que trabajan sobre lo ya traído.
#
# Era 25, y el modelo listaba 10 igual — dos veces seguidas, medido. Pero
# narraba "los primeros 25" y "y 149 más" (174 - 25), porque esos números salían
# del payload. O sea: decía 25, mostraba 10, y el faltante estaba mal.
#
# El modelo tenía razón sobre cuántos entran en un WhatsApp. Ahora el payload
# dice lo mismo que va a hacer, y los tres números cierran solos sin pedírselo
# al prompt.
MAX_LISTA = 10


def _responder(resultado, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(
        content=json.dumps(resultado, ensure_ascii=False, default=str),
        tool_call_id=tool_call_id)]})


def _vendedor(state: dict) -> str | None:
    return (state.get("perfil") or {}).get("vendedor_id")


def _distrito_corto(valor: str) -> str:
    """'SANTIAGO DE SURCO/LIMA/LIMA' -> 'SANTIAGO DE SURCO'."""
    return (valor or "").split("/")[0].strip()


@tool
async def contar_cartera(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Cuántos clientes tiene el asesor, y en qué distritos.

    Para «¿cuántos clientes tengo?» — devuelve el número y el reparto por
    distrito, SIN la lista. Si además quieren saber quiénes son, usá
    `listar_cartera`."""
    vendedor_id = _vendedor(state)
    if not vendedor_id:
        return _responder({"error": "SIN_VENDEDOR"}, tool_call_id)

    filas = await api.customer_by_seller(vendedor_id)
    if not isinstance(filas, list):
        return _responder(filas, tool_call_id)

    conteo: dict[str, int] = {}
    for f in filas:
        d = _distrito_corto(f.get("locality", ""))
        if d:
            conteo[d] = conteo.get(d, 0) + 1

    return _responder({
        "total": len(filas),
        "por_distrito": dict(sorted(conteo.items(), key=lambda x: -x[1])[:12]),
    }, tool_call_id)


@tool
async def listar_cartera(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    distrito: Optional[str] = None,
    nombre: Optional[str] = None,
) -> Command:
    """Los clientes del asesor, hasta 25, cada uno con su RUC.

    `distrito`: filtra por zona («Lima», «Surco», «Huancayo»)
    `nombre`:   filtra por razón social o RUC

    Devuelve `total_cartera` (todos los del asesor), `coinciden` (cuántos pasan
    el filtro) y `clientes` (hasta 25 de esos). Los dos números vienen contados:
    `coinciden` es sobre TODOS los que coinciden, no sobre los 25 que van."""
    vendedor_id = _vendedor(state)
    if not vendedor_id:
        return _responder({"error": "SIN_VENDEDOR"}, tool_call_id)

    filas = await api.customer_by_seller(vendedor_id)
    if not isinstance(filas, list):
        return _responder(filas, tool_call_id)

    coincidentes = filas
    if distrito:
        d = distrito.lower()
        coincidentes = [c for c in coincidentes
                        if d in (c.get("locality") or "").lower()]
    if nombre:
        n = nombre.lower()
        coincidentes = [c for c in coincidentes
                        if n in (c.get("nameClient") or "").lower()
                        or n in (c.get("rucClient") or "")]

    resultado = {
        "total_cartera": len(filas),
        "coinciden": len(coincidentes),
        "mostrados": min(len(coincidentes), MAX_LISTA),
        "clientes": coincidentes[:MAX_LISTA],
    }
    if distrito and not coincidentes:
        resultado["sin_coincidencias_en"] = distrito
    return _responder(resultado, tool_call_id)


@tool
async def consultar_perfil_cliente(
    cliente: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Perfil de UN cliente: razón social, RUC, dirección, distrito y email.

    `cliente` puede ser el RUC o el nombre — se resuelve dentro de la cartera
    del asesor. Si el cliente no es suyo, la consulta no se ejecuta."""
    ruc, error = await acceso.verificar(cliente, state.get("perfil") or {})
    if error:
        return _responder(error, tool_call_id)
    return _responder(await api.customer_by_filter(ruc_client=ruc), tool_call_id)


TOOLS = [contar_cartera, listar_cartera, consultar_perfil_cliente]
