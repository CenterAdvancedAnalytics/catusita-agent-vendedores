"""Tools nativas de LangChain para el agente Catusita.

Cada @tool:
  - Solo expone al modelo los parámetros de negocio (sku_code, cliente_ruc, etc.).
  - Recibe `perfil` y `tool_call_id` via InjectedState / InjectedToolCallId (invisible al modelo).
  - Aplica control de acceso por cartera (access.py) para tools RUC-scoped.
  - Retorna Command(update={...}) para actualizar el estado del grafo.

Listas exportadas:
  - TOOLS_VENDEDOR_LC  (12 tools, acceso completo)
  - TOOLS_CLIENTE_LC   (8 tools, solo información pública)
"""
import os
import json
import logging
from typing import Annotated, Optional

from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from agents import (
    stock, prices, orders, documents,
    catalog_rag, cartera, imagenes,
)
from orchestrator import access
from shared import llm


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _to_command(resultado: dict, tool_call_id: str, extra: dict | None = None) -> Command:
    """Empaqueta el resultado como ToolMessage y lo aplica al estado via Command."""
    content = json.dumps(resultado, ensure_ascii=False, default=str)
    update: dict = {"messages": [ToolMessage(content=content, tool_call_id=tool_call_id)]}
    if extra:
        update.update(extra)
    return Command(update=update)


_USE_AUTH_MOCK = os.getenv("USE_AUTH_MOCK", "true").lower() == "true"



async def _sku_fallback(sku_code: str, resultado: dict) -> dict:
    """Si el SKU no se encontró, busca coincidencias en el catálogo y las sugiere."""
    if not (resultado.get("error") or resultado.get("detail") == "Producto no encontrado"):
        return resultado
    try:
        from shared.sap_client import sap
        search = await sap.get_catalogo(q=sku_code)
        productos = search.get("productos", []) if isinstance(search, dict) else []
    except Exception:
        return resultado
    if not productos:
        return resultado
    return {
        "error": "PRODUCTO_NO_ENCONTRADO_SUGERENCIAS",
        "mensaje": (
            f"No se encontró ningún producto con el SKU exacto '{sku_code}'. "
            "Sin embargo, encontramos estas coincidencias en el catálogo. "
            "Pregúntale al usuario si se refiere a alguna de estas opciones y muéstrale los SKUs:"
        ),
        "sugerencias": [
            {
                "sku": p["sku"],
                "nombre": p["nombre"],
                "categoria": p.get("categoria"),
                "marca": p.get("marca"),
            }
            for p in productos[:5]
        ],
    }


# ─── Stock / precios ──────────────────────────────────────────────────────────

@tool
async def consultar_stock(
    sku_code: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Consulta el stock disponible de un producto en los almacenes. Usar cuando pregunten por disponibilidad, inventario o si hay stock de un producto."""
    perfil = state["perfil"]
    resultado = await stock.consultar_stock(sku_code)
    resultado = await _sku_fallback(sku_code, resultado)
    return _to_command(resultado, tool_call_id)


@tool
async def consultar_precio(
    sku_code: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Consulta el precio de lista de un producto. El agente SOLO muestra precio de lista, nunca precios netos ni descuentos."""
    perfil = state["perfil"]
    resultado = await prices.consultar_precio(sku_code, tipo="lista")
    resultado = await _sku_fallback(sku_code, resultado)
    return _to_command(resultado, tool_call_id)


# ─── Pedidos / documentos ────────────────────────────────────────────────────

@tool
async def consultar_pedidos(
    cliente_ruc: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    estado: Optional[str] = None,
) -> Command:
    """Consulta los pedidos de un cliente por su RUC. Devuelve estado del pedido, número de factura SUNAT, estado de despacho (entregado/rechazado) y notas de crédito. Úsala cuando el asesor pregunte por los pedidos, facturas o despachos de un cliente de su cartera. La búsqueda es por cliente (RUC), no por número de pedido."""
    perfil = state["perfil"]
    args = {"cliente_ruc": cliente_ruc}
    denegado = await access.verificar_acceso_cartera("consultar_pedidos", args, perfil)
    resultado = denegado or await orders.consultar_pedidos(args["cliente_ruc"], estado)
    return _to_command(resultado, tool_call_id)


@tool
async def consultar_despacho(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    pedido_id: Optional[str] = None,
    factura: Optional[str] = None,
) -> Command:
    """Consulta el estado de ENTREGA/DESPACHO de un pedido: si ya se entregó, la guía de remisión y las fechas de despacho y entrega. Úsala cuando pregunten '¿ya llegó el pedido?', '¿se entregó?', '¿en qué va el despacho?'. Requiere el N° de pedido o el N° de factura (NO funciona por RUC): si el asesor pregunta por el despacho de un cliente, primero usa consultar_pedidos para obtener sus N° de pedido y luego consulta el despacho de cada uno. Trae un campo 'mensaje' ya redactado que puedes reenviar tal cual."""
    perfil = state["perfil"]
    if not pedido_id and not factura:
        return _to_command(
            {"error": "FALTA_DATO", "mensaje": "Necesito el número de pedido o el número de factura para consultar el despacho."},
            tool_call_id,
        )
    resultado = await orders.consultar_despacho(pedido_id=pedido_id, factura=factura)
    return _to_command(resultado, tool_call_id)


# ─── Cartera (solo vendedores) ────────────────────────────────────────────────

@tool
async def consultar_cartera(
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    estado: Optional[str] = None,
    tipo: Optional[str] = None,
) -> Command:
    """DEBES usar esta tool SIEMPRE que el asesor pregunte por sus clientes, su cartera o su lista de cuentas. Devuelve todos los clientes asignados a este asesor con razón social, tipo, estado, límite de crédito, saldo pendiente y último pedido. Dispárala ante frases como 'mis clientes', 'mi cartera', 'qué clientes tengo'. NO inventes ni resumas la cartera de memoria."""
    perfil = state["perfil"]
    vendedor_id = perfil.get("vendedor_id", "V001")
    resultado = await cartera.consultar_cartera(vendedor_id, estado, tipo)
    return _to_command(resultado, tool_call_id)


@tool
async def consultar_perfil_cliente(
    ruc: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Obtiene el perfil completo de un cliente: razón social, dirección, teléfono, tipo, vendedor asignado y estado."""
    perfil = state["perfil"]
    args = {"ruc": ruc}
    denegado = await access.verificar_acceso_cartera("consultar_perfil_cliente", args, perfil)
    resultado = denegado or await cartera.consultar_perfil_cliente(args["ruc"])
    return _to_command(resultado, tool_call_id)


# ─── Catálogo / vehículo ──────────────────────────────────────────────────────

@tool
async def buscar_catalogo(
    query: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    placa: Optional[str] = None,
    vin: Optional[str] = None,
) -> Command:
    """Busca productos en el catálogo por nombre, categoría o placa/VIN del vehículo. Usar para encontrar repuestos, ver equivalencias o buscar productos compatibles."""
    perfil = state["perfil"]
    resultado = await catalog_rag.buscar_catalogo(query, placa, vin)
    return _to_command(resultado, tool_call_id)


@tool
async def enviar_imagen_producto(
    sku_code: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Envía por WhatsApp la(s) FOTO(s) de un producto. Úsala SOLO cuando el vendedor pida la imagen, foto o ficha de un producto puntual (por su código SKU). La foto se manda automáticamente al chat como imagen; en tu respuesta de texto solo confírmale que se la enviaste."""
    perfil = state["perfil"]
    resultado = await imagenes.obtener_imagenes(sku_code)
    if not resultado or resultado.get("error"):
        return _to_command(
            {"error": "SIN_IMAGEN", "mensaje": f"No encontré una foto del producto {sku_code} en el sistema."},
            tool_call_id,
        )
    imgs = resultado.get("imagenes", [])
    nombre = resultado.get("nombre", "")
    media = []
    for i, im in enumerate(imgs):
        media.append({
            "imagen_base64": im["base64"],
            "caption": f"{nombre} — {sku_code}" if i == 0 else "",
            "filename": im.get("filename", f"{sku_code}.png"),
        })
    return _to_command(
        {"sku": sku_code, "nombre": nombre, "enviadas": len(media),
         "mensaje": f"Te envié {len(media)} foto(s) del producto {sku_code} al chat."},
        tool_call_id,
        {"media_pendiente": media},
    )


@tool
async def enviar_documento(
    cliente_ruc: str,
    numero_documento: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Envía por WhatsApp el PDF de una FACTURA o NOTA DE CRÉDITO de un cliente. Úsala cuando el vendedor pida 'mándame la factura', 'descárgame la factura del pedido X', 'la nota de crédito de tal cliente'. Necesita el RUC del cliente y el número del documento (ej. F001-0102835); si no tienes el número, primero usa consultar_pedidos para obtenerlo de los pedidos del cliente. El PDF se manda automáticamente al chat como archivo; en tu texto solo confírmale que se lo enviaste. Solo maneja facturas y notas de crédito (no guías de remisión)."""
    perfil = state["perfil"]
    args = {"cliente_ruc": cliente_ruc}
    denegado = await access.verificar_acceso_cartera("enviar_documento", args, perfil)
    if denegado:
        return _to_command(denegado, tool_call_id)
    resultado = await documents.enviar_documento(args["cliente_ruc"], numero_documento)
    if not resultado or resultado.get("error"):
        return _to_command(
            resultado or {"error": "SIN_DOCUMENTO",
                          "mensaje": f"No pude obtener el documento {numero_documento}."},
            tool_call_id,
        )
    numero = resultado.get("numero", numero_documento)
    tipo = resultado.get("tipo", "documento")
    media = [{
        "documento_base64": resultado["pdf_base64"],
        "caption": f"{tipo} {numero}",
        "filename": resultado.get("filename", f"{numero}.pdf"),
        "mime": resultado.get("mime", "application/pdf"),
    }]
    return _to_command(
        {"numero": numero, "tipo": tipo, "cliente": resultado.get("cliente", ""),
         "mensaje": f"Te envié el PDF de {tipo} {numero} al chat."},
        tool_call_id,
        {"media_pendiente": media},
    )


@tool
async def consultar_pago_documento(
    cliente_ruc: str,
    numero_documento: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Consulta el ESTADO DE PAGO de una factura o nota de crédito de un cliente: si ya está pagada, si se pagó con LETRAS (canje) o con NOTA DE CRÉDITO, el saldo pendiente y el detalle de los pagos. Úsala cuando pregunten '¿esta factura está pagada?', '¿se pagó con letras?', '¿le aplicaron nota de crédito?', '¿cuánto debe de esa factura?'. Necesita el RUC del cliente y el número del documento (ej. F001-0037749); si no tienes el número, primero usa consultar_pedidos. NO descarga el PDF (para eso está enviar_documento)."""
    perfil = state["perfil"]
    args = {"cliente_ruc": cliente_ruc}
    denegado = await access.verificar_acceso_cartera("consultar_pago_documento", args, perfil)
    if denegado:
        return _to_command(denegado, tool_call_id)
    resultado = await documents.consultar_pago_documento(args["cliente_ruc"], numero_documento)
    if not resultado or resultado.get("error"):
        resultado = resultado or {"error": "SIN_DATO",
                                  "mensaje": f"No pude obtener el estado de pago de {numero_documento}."}
    return _to_command(resultado, tool_call_id)


# ─── Placa vía Yahuar (WhatsApp relay) ───────────────────────────────────────

_YAHUAR_BLOQUEANTE = os.getenv("YAHUAR_BLOQUEANTE", "true").lower() == "true"


@tool
async def consultar_placa_yahuar(
    placa: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Consulta los datos de un vehículo peruano por su placa vía el servicio Yahuar. Úsala cuando pregunten qué auto es una placa, a quién pertenece, o quieran los datos del vehículo. La consulta BLOQUEA hasta obtener la respuesta (~30-60s): cuando devuelva, tendrás los datos del vehículo en 'datos_vehiculo_texto' — preséntaselos al usuario por escrito. La foto de la tarjeta se envía sola al chat (si 'tiene_imagen' es true, menciónaselo). Si devuelve 'error', comunícalo. NO la llames de nuevo en el mismo turno."""
    from shared import yahuar as yahuar_mod
    perfil     = state["perfil"]
    from_field = perfil.get("from_field") or perfil.get("numero", "")
    placa_clean = placa.strip().upper()

    # ── Modo async viejo (kill-switch YAHUAR_BLOQUEANTE=false) ─────────────────
    if not _YAHUAR_BLOQUEANTE:
        existente = await yahuar_mod.peek_pendiente()
        if existente:
            return _to_command({
                "placa": existente.get("placa", placa_clean),
                "mensaje": "Ya hay una consulta de placa en proceso. La respuesta llegará en momentos al chat. NO vuelvas a llamar este tool.",
            }, tool_call_id)
        try:
            await yahuar_mod.consultar_placa(placa_clean, from_field)
            resultado = {"placa": placa_clean,
                         "mensaje": f"Consulta enviada a Yahuar para la placa {placa_clean}. Llega en ~30s al chat. NO llames este tool de nuevo."}
        except Exception as e:
            logging.error(f"Error consultando Yahuar: {e}")
            resultado = {"error": "YAHUAR_ERROR", "mensaje": "No pude consultar la placa en este momento. Inténtalo de nuevo."}
        return _to_command(resultado, tool_call_id)

    # ── Modo BLOQUEANTE (subagente): espera la respuesta y la devuelve limpia ──
    from agents import yahuar_subagente
    resultado = await yahuar_subagente.consultar_placa_bloqueante(placa_clean, from_field)

    extra: dict = {}
    if resultado.get("imagen_base64"):
        b64 = resultado.pop("imagen_base64")
        pc = resultado.get("placa", placa_clean)
        ext = (resultado.get("imagen_mime") or "image/jpeg").split("/")[-1].replace("jpeg", "jpg")
        extra["media_pendiente"] = [{
            "imagen_base64": b64,
            "caption": f"Tarjeta vehicular — {pc}",
            "filename": f"placa_{pc}.{ext}",
        }]
    resultado.pop("imagen_mime", None)

    return _to_command(resultado, tool_call_id, extra)


# ─── Solo clientes ────────────────────────────────────────────────────────────


# ─── Toolsets por canal ───────────────────────────────────────────────────────

# NOTA: al migrar del Mock SAP a la API real de Catusita (tools-agente-catusita),
# se APAGARON las tools sin dato de origen real: buscar_pedido_por_id,
# consultar_credito, consultar_cobranzas, consultar_historial, obtener_documentos
# e identificar_vehiculo. Su código (y los wrappers en agents/) se eliminó; si la
# API real llega a exponer esos datos hay que reescribirlas.
# Ver docs/plan_migracion_api_real.md.
TOOLS_VENDEDOR_LC = [
    consultar_stock,
    consultar_precio,
    consultar_pedidos,
    consultar_despacho,
    consultar_cartera,
    consultar_perfil_cliente,
    buscar_catalogo,
    enviar_imagen_producto,
    enviar_documento,
    consultar_pago_documento,
    consultar_placa_yahuar,
]

TOOLS_CLIENTE_LC = [
    consultar_stock,
    consultar_precio,
    buscar_catalogo,
    enviar_imagen_producto,
]
