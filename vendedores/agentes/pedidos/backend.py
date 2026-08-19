"""Acceso de `pedidos` (vendedores) a la API de Catusita.

    GET /api/sales/orders/documents?ClientTaxId=&NumberOfOrders=   qué pidió
    GET /api/sales/orders/dispatch-status?OrderId=|InvoiceNumber=  dónde está

Los dos buscan por cosas distintas y no es un detalle: el primero va por CLIENTE
y el segundo va por PEDIDO o FACTURA. No hay forma de pedir «el despacho de los
pedidos de este RUC» en una sola llamada — hay que sacar los números primero.

── `SalesRepresentativeId` NO sirve para listar por vendedor ──────────────────

El parámetro existe en el swagger y parece prometer «todos los pedidos del
asesor». No lo hace:

    ?SalesRepresentativeId=22   ->  400  "Debe ingresar el código del cliente
                                          o el RUC del cliente."

Es un filtro adicional sobre un cliente ya elegido, no una forma de listar. Con
eso queda cerrado el camino barato para «¿a qué clientes no les vendí este año?»:
sale a una llamada por cliente de la cartera, y eso se decide arriba, no acá.

── El número de documento viene con prefijo ───────────────────────────────────

    sunatDocumentNumber:  "FA/ F001-0073326"

El asesor escribe `F001-0073326`. Si se compara crudo no coincide nunca, y el
síntoma es «no encuentro esa factura» sobre una factura que está ahí. Por eso
`_numero_sunat` corta el prefijo y `facturacion.ubicar` compara contra eso.
"""
from vendedores.plataforma_vendedores import catusita_api

# Cuántos pedidos traer cuando nadie dice cuántos. La API pide el número; sin
# tope devolvería el histórico entero de un cliente de veinte años.
PEDIDOS_POR_DEFECTO = 20


def _numero_sunat(valor: str) -> str:
    """'FA/ F001-0073326' -> 'F001-0073326'. Ver la nota del encabezado."""
    if not valor:
        return ""
    return valor.split("/")[-1].strip()


# Códigos SUNAT. El asesor lee «Factura», no «01».
TIPOS_DOC = {"01": "Factura", "03": "Boleta", "07": "Nota de crédito",
             "08": "Nota de débito", "09": "Guía de remisión"}


def _documento(d: dict) -> dict:
    tipo_codigo = d.get("documentType") or ""
    return {
        "tipo": TIPOS_DOC.get(tipo_codigo, tipo_codigo or "Documento"),
        "numero": _numero_sunat(d.get("sunatDocumentNumber") or ""),
        "numero_interno": d.get("documentNumber"),
        "tipo_codigo": tipo_codigo,
        "empresa": d.get("companyName") or "",
        "empresa_codigo": d.get("companyCode") or "",
        "fecha": d.get("documentDate") or "",
        "estado_despacho": d.get("dispatchStatus") or "",
        "monto": d.get("amount"),
        "moneda": d.get("currency") or "",
        "notas_credito": [
            {
                "numero": _numero_sunat(nc.get("sunatDocumentNumber") or ""),
                "tipo_codigo": nc.get("documentType") or "07",
                "empresa_codigo": nc.get("companyCode") or "",
                "fecha": nc.get("documentDate") or "",
                "monto": nc.get("amount"),
                "moneda": nc.get("currency") or "",
            }
            for nc in (d.get("creditNotes") or [])
        ],
    }


async def pedidos(cliente_ruc: str, estado: str | None = None,
                  cantidad: int = PEDIDOS_POR_DEFECTO) -> dict:
    datos = await catusita_api.get(
        "/api/sales/orders/documents",
        {"ClientTaxId": cliente_ruc, "NumberOfOrders": cantidad},
    )
    if isinstance(datos, dict) and datos.get("error"):
        return datos
    if not isinstance(datos, dict):
        return {"error": "RESPUESTA_INESPERADA",
                "mensaje": "La API no devolvió los pedidos en el formato esperado."}

    ordenes = datos.get("orders") or []
    lista = []
    for o in ordenes:
        # El estado no es un parámetro de la API: se filtra acá.
        if estado and (o.get("orderStatus") or "").upper() != estado.upper():
            continue
        lista.append({
            "pedido_id": o.get("orderId"),
            "fecha": o.get("orderDate") or "",
            "estado": o.get("orderStatus") or "",
            "monto": o.get("amount"),
            "moneda": o.get("currency") or "",
            "cliente": o.get("clientName") or "",
            "vendedor": o.get("sellerName") or "",
            "documentos": [_documento(d) for d in (o.get("salesDocuments") or [])],
        })

    return {
        "ruc": cliente_ruc,
        "cliente": (ordenes[0].get("clientName") if ordenes else "") or "",
        "total_pedidos": len(lista),
        "pedidos": lista,
    }


async def despacho(pedido_id: str | None = None,
                   factura: str | None = None) -> dict:
    datos = await catusita_api.get(
        "/api/sales/orders/dispatch-status",
        {"OrderId": pedido_id, "InvoiceNumber": factura},
    )
    if isinstance(datos, dict) and datos.get("error"):
        return datos
    if not isinstance(datos, dict):
        return {"error": "RESPUESTA_INESPERADA",
                "mensaje": "La API no devolvió el despacho en el formato esperado."}

    resumen = datos.get("summary") or {}
    return {
        "estado_general": resumen.get("generalStatus") or "",
        "guia": resumen.get("shippingGuideNumber") or "",
        "fecha_despacho_estimada": resumen.get("estimatedDispatchDate") or "",
        "mensaje": resumen.get("userMessage") or "",
        "documentos": [
            {
                "pedido_id": d.get("orderId"),
                "factura": _numero_sunat(d.get("invoiceNumber") or ""),
                "fecha_factura": d.get("invoiceDate") or "",
                "estado": d.get("invoiceStatus") or "",
                "guia_reparto": d.get("deliveryGuide") or "",
                "guia_transportista": d.get("carrierGuide") or "",
                "fecha_despacho": d.get("dispatchDate") or "",
                "fecha_entrega": d.get("deliveryDate") or "",
            }
            for d in (datos.get("documents") or [])
        ],
    }
