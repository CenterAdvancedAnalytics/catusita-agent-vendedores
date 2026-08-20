from shared.sap_client import sap
from agents import orders


def _norm_num(s: str) -> str:
    return (s or "").replace(" ", "").replace("/", "").upper()


async def _ubicar_documento(cliente_ruc: str, numero_documento: str) -> dict:
    """Ubica una factura o NC en los pedidos del cliente para obtener sus códigos.

    Los endpoints de documentos exigen N° + tipo (01/07) + empresa (04/01); el agente
    solo conoce el N° y el cliente. Devuelve el documento normalizado (con
    tipo_codigo/empresa_codigo) o un dict {"error": ...} si no se ubica.
    """
    ped = await orders.consultar_pedidos(cliente_ruc)
    if not ped or ped.get("error"):
        return {"error": "SIN_PEDIDOS",
                "mensaje": "No pude obtener los pedidos del cliente para ubicar el documento."}

    objetivo = _norm_num(numero_documento)
    for p in ped.get("pedidos", []) or []:
        for d in p.get("documentos", []) or []:
            if _norm_num(d.get("numero")) == objetivo:
                return d
            for nc in d.get("notas_credito", []) or []:
                if _norm_num(nc.get("numero")) == objetivo:
                    return nc

    return {"error": "DOC_NO_ENCONTRADO",
            "mensaje": (f"No encontré el documento {numero_documento} en los pedidos "
                        "de ese cliente. Verifica el número o el cliente.")}


async def enviar_documento(cliente_ruc: str, numero_documento: str) -> dict:
    """Devuelve el PDF (base64) de una factura o nota de crédito de un cliente."""
    doc = await _ubicar_documento(cliente_ruc, numero_documento)
    if doc.get("error"):
        return doc
    return await sap.get_documento(
        numero=doc.get("numero"),
        tipo=doc.get("tipo_codigo") or "01",
        empresa=doc.get("empresa_codigo") or "04",
    )


async def consultar_pago_documento(cliente_ruc: str, numero_documento: str) -> dict:
    """Estado de pago de una factura/NC de un cliente: si está pagada, si fue con
    letras (canje) o nota de crédito, saldo pendiente y detalle de pagos."""
    doc = await _ubicar_documento(cliente_ruc, numero_documento)
    if doc.get("error"):
        return doc
    return await sap.get_documento_pagos(
        numero=doc.get("numero"),
        tipo=doc.get("tipo_codigo") or "01",
        empresa=doc.get("empresa_codigo") or "04",
    )
