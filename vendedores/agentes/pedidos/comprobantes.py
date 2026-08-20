"""Leer el detalle de una factura. Lo que el JSON de la API no da.

── Por qué hace falta esto ────────────────────────────────────────────────────

Ningún endpoint devuelve QUÉ productos tiene un pedido. `/api/sales/orders/documents`
da el monto total y nada más:

    orderId  orderDate  orderStatus  amount  currency  clientName  sellerName

Por eso «¿qué compra más este cliente?» estaba dado por imposible.

Sí está en otro lado: `/api/electronic-documents/search` devuelve `xmlUrl`, y una
factura electrónica peruana es un XML UBL que POR LEY trae el detalle de líneas.
Ahí hay SKU, cantidad, importe y descripción.

── Por qué se parsea con regex y no con un XML parser ─────────────────────────

Porque UBL viene con namespaces (`cac:`, `cbc:`) y los campos que interesan son
cinco. Con `ElementTree` habría que registrar los namespaces y navegar el árbol
para sacar lo mismo, y el día que Catusita cambie de proveedor de facturación el
árbol cambia igual.

Son documentos generados por máquina y siempre con la misma forma. Si algún día
deja de matchear, el resultado es cero líneas — no un dato equivocado.
"""
import re

# Una línea del comprobante. En una factura es `InvoiceLine`; en una nota de
# crédito, `CreditNoteLine`. Misma forma adentro.
_LINEA = re.compile(
    r"<cac:(?:Invoice|CreditNote)Line>.*?</cac:(?:Invoice|CreditNote)Line>", re.S)
_ITEM = re.compile(r"<cac:Item>.*?</cac:Item>", re.S)

# El CDATA es opcional: algunos emisores lo usan y otros no.
_DESC = re.compile(
    r"<cbc:Description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</cbc:Description>", re.S)
_SKU = re.compile(
    r"<cac:SellersItemIdentification>\s*<cbc:ID>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</cbc:ID>", re.S)
_CANT = re.compile(
    r"<cbc:(?:Invoiced|Credited)Quantity[^>]*>(.*?)</cbc:(?:Invoiced|Credited)Quantity>")

# ── Qué tipo de nota de crédito es (catálogo 09 de SUNAT) ─────────────────────
#
# Importa porque no todas descuentan lo mismo. Un descuento global baja la plata
# pero el cliente se quedó con la mercadería; una devolución baja las dos cosas.
#
# Restar unidades en un descuento haría que un cliente que negoció una rebaja
# figure como si hubiera devuelto producto.
_MOTIVO = re.compile(r"<cbc:ResponseCode[^>]*>(.*?)</cbc:ResponseCode>")

DEVUELVE_MERCADERIA = {
    "01",  # anulación de la operación
    "06",  # devolución total
    "07",  # devolución por ítem
}
SOLO_PLATA = {
    "04",  # descuento global
    "05",  # descuento por ítem
    "08",  # bonificación
    "09",  # disminución en el valor
}


def motivo(xml: str) -> str:
    """El código del catálogo 09. Cadena vacía si no está."""
    m = _MOTIVO.search(xml or "")
    return m.group(1).strip() if m else ""

# La moneda va en un atributo, no en el texto. Sacarla es un error caro: el
# modelo la asume y escribió "S/ 443" sobre un monto que estaba en dólares.
# Para un asesor cotizando, eso es un factor de 3.7.
_MONTO = re.compile(
    r'<cbc:LineExtensionAmount[^>]*currencyID="([A-Z]{3})"[^>]*>(.*?)'
    r"</cbc:LineExtensionAmount>")


def _num(m) -> float:
    try:
        return float(m.group(1))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def lineas(xml: str) -> list[dict]:
    """Las líneas de una factura: [{sku, descripcion, unidades, monto}].

    Lista vacía si el XML no se pudo leer o no tiene líneas. Nunca lanza: una
    factura ilegible no puede tumbar el cálculo de las otras once.
    """
    if not xml:
        return []

    salida = []
    for cruda in _LINEA.findall(xml):
        item = _ITEM.search(cruda)
        if not item:
            continue
        bloque = item.group(0)
        desc = _DESC.search(bloque)
        sku = _SKU.search(bloque)
        monto = _MONTO.search(cruda)
        salida.append({
            "sku": (sku.group(1).strip() if sku else ""),
            "descripcion": (desc.group(1).strip() if desc else ""),
            "unidades": _num(_CANT.search(cruda)),
            "monto": float(monto.group(2)) if monto else 0.0,
            "moneda": monto.group(1) if monto else "",
        })
    return salida
