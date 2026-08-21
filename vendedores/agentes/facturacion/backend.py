"""Acceso de `facturacion` (vendedores) a la API de Catusita.

    GET /api/electronic-documents/search           el PDF (por URL)
    GET /api/document-payment-application/search   cómo se pagó
    GET /api/sales/orders/documents                para ubicar el documento

── Por qué esta área llama a los pedidos ──────────────────────────────────────

Los dos primeros endpoints exigen `DocumentNumber + DocumentType + CompanyCode`.
El asesor solo sabe el número («mandame la F001-0102835»). Los otros dos códigos
únicamente aparecen dentro de los pedidos del cliente.

No es hablarle al ÁREA `pedidos` — es usar un endpoint para resolver sus propios
identificadores. Lo que el diseño prohíbe es que un área le PIDA algo a otra;
acá no hay nadie del otro lado.

── El PDF ya no viene en base64 ───────────────────────────────────────────────

El wrapper devolvía `pdf_base64` listo. La API real devuelve una URL a otro
puerto:

    pdfUrl: http://api.catusita.com:8086/ICFacturaPDF/20100080002-01-F001-0073326.PDF

WhatsApp necesita los bytes, así que se descargan acá y se devuelve `pdf_base64`
igual que antes. La tool no se entera del cambio, que es el punto.

`hasPdf` se mira antes de bajar: un documento sin PDF emitido tiene la URL
armada igual, y bajarla da un 404 que se leería como «falló la descarga» en vez
de «ese documento no tiene PDF».
"""
import base64

from vendedores.plataforma_vendedores import catusita_api

TIMEOUT_DESCARGA = 30.0

# Cuántos pedidos revisar para ubicar un documento. Un asesor pide facturas
# recientes; buscar en el histórico completo de un cliente grande es caro y no
# cambia el resultado en la práctica.
PEDIDOS_A_REVISAR = 30


def _norm(s: str) -> str:
    """F001-0102835, f001 0102835, F001/0102835 -> todos al mismo string.

    El asesor lo escribe de memoria y cada uno usa un separador distinto.
    """
    return (s or "").replace(" ", "").replace("/", "").replace("-", "").upper()


async def ubicar(cliente_ruc: str, numero: str) -> dict:
    """Encuentra el documento entre los pedidos del cliente y devuelve sus
    códigos (`tipo_codigo`, `empresa_codigo`). `{"error": ...}` si no está.

    Busca también dentro de las notas de crédito de cada documento: una NC tiene
    su propio número y el asesor la pide igual que una factura.
    """
    datos = await catusita_api.get(
        "/api/sales/orders/documents",
        {"ClientTaxId": cliente_ruc, "NumberOfOrders": PEDIDOS_A_REVISAR},
    )
    if isinstance(datos, dict) and datos.get("error"):
        return {"error": "SIN_PEDIDOS",
                "mensaje": "No se pudieron leer los pedidos del cliente."}
    if not isinstance(datos, dict):
        return {"error": "SIN_PEDIDOS",
                "mensaje": "No se pudieron leer los pedidos del cliente."}

    buscado = _norm(numero)
    for o in datos.get("orders") or []:
        for d in o.get("salesDocuments") or []:
            # 'FA/ F001-0073326' -> _norm lo deja 'FAF0010073326', que nunca
            # coincide con lo que escribe el asesor. Se compara por el sufijo.
            crudo = d.get("sunatDocumentNumber") or ""
            limpio = crudo.split("/")[-1].strip()
            if _norm(limpio) == buscado:
                return {
                    "numero": limpio,
                    "tipo_codigo": d.get("documentType") or "01",
                    "empresa_codigo": d.get("companyCode") or "",
                    "empresa": d.get("companyName") or "",
                    "fecha": d.get("documentDate") or "",
                    "monto": d.get("amount"),
                    "moneda": d.get("currency") or "",
                }
            for nc in d.get("creditNotes") or []:
                crudo_nc = nc.get("sunatDocumentNumber") or ""
                limpio_nc = crudo_nc.split("/")[-1].strip()
                if _norm(limpio_nc) == buscado:
                    return {
                        "numero": limpio_nc,
                        "tipo_codigo": nc.get("documentType") or "07",
                        "empresa_codigo": nc.get("companyCode") or "",
                        "empresa": nc.get("companyName") or "",
                        "fecha": nc.get("documentDate") or "",
                        "monto": nc.get("amount"),
                        "moneda": nc.get("currency") or "",
                    }

    return {"error": "DOC_NO_ENCONTRADO",
            "mensaje": (f"El documento {numero} no aparece en los últimos "
                        f"{PEDIDOS_A_REVISAR} pedidos de ese cliente. Verificá "
                        "el número o el cliente.")}


async def pdf(numero: str, tipo: str, empresa: str) -> dict:
    """El PDF del documento, descargado y en base64."""
    datos = await catusita_api.get(
        "/api/electronic-documents/search",
        {"DocumentNumber": numero, "DocumentType": tipo, "CompanyCode": empresa},
    )
    if isinstance(datos, dict) and datos.get("error"):
        return datos

    filas = datos if isinstance(datos, list) else []
    if not filas:
        return {"error": "NO_ENCONTRADO",
                "mensaje": f"No se encontró el documento electrónico {numero}."}

    f = filas[0]
    if not f.get("hasPdf") or not f.get("pdfUrl"):
        return {"error": "SIN_PDF",
                "mensaje": f"El documento {numero} no tiene PDF emitido."}

    contenido, motivo = await catusita_api.descargar_con_motivo(
        f["pdfUrl"], timeout=TIMEOUT_DESCARGA)

    # Un 404 acá no es una caída: es que ese PDF no está publicado y no va a
    # estarlo. Medido: las facturas de CATUSITA TIRE GROUP (empresa 03) viven en
    # /FCFacturaPDF/ y esa carpeta está vacía — 10 de 10 dan 404. Las de
    # REPUESTOS JAPONESES (empresa 04, /RJFacturaPDF/) bajan las 4 de 4.
    #
    # Se separan porque el asesor hace cosas distintas con cada una: ante la
    # primera deja de insistir y pide el documento por otro canal; ante la
    # segunda vuelve a intentar en un rato.
    if not contenido:
        if motivo == "NO_EXISTE":
            return {"error": "PDF_NO_PUBLICADO",
                    "numero": numero,
                    "empresa": f.get("companyName") or "",
                    "xml_url": f.get("xmlUrl") if f.get("hasXml") else ""}
        return {"error": "DESCARGA_FALLIDA", "numero": numero, "motivo": motivo}

    return {
        "numero": f.get("documentNumber") or numero,
        "tipo": f.get("documentType") or "Documento",
        "cliente": f.get("clientName") or "",
        "empresa": f.get("companyName") or "",
        "fecha": f.get("issueDate") or "",
        "pdf_base64": base64.b64encode(contenido).decode(),
        "filename": f"{f.get('documentNumber') or numero}.pdf",
        "mime": "application/pdf",
        "xml_url": f.get("xmlUrl") if f.get("hasXml") else "",
    }


async def pagos(numero: str, tipo: str, empresa: str) -> dict:
    """Estado de pago: saldo, vencimiento, y si se canjeó por letras.

    La API envuelve el detalle en `companies[].documents[]` porque el mismo
    número puede existir en más de una empresa del grupo. Se aplana: al asesor
    le importa el documento, no en qué sociedad quedó registrado.
    """
    datos = await catusita_api.get(
        "/api/document-payment-application/search",
        {"DocumentNumber": numero, "DocumentType": tipo, "CompanyCode": empresa},
    )
    if isinstance(datos, dict) and datos.get("error"):
        return datos
    if not isinstance(datos, dict):
        return {"error": "RESPUESTA_INESPERADA",
                "mensaje": "La API no devolvió el estado de pago como se esperaba."}

    if not datos.get("found"):
        return {"error": "NO_ENCONTRADO",
                "mensaje": datos.get("message") or f"No se encontró {numero}."}

    documentos = []
    for c in datos.get("companies") or []:
        for d in c.get("documents") or []:
            documentos.append({
                "numero": d.get("documentNumber") or "",
                "tipo": d.get("documentTypeName") or "",
                "empresa": d.get("companyName") or "",
                "cliente": d.get("clientName") or "",
                "moneda": d.get("currency") or "",
                "monto_total": d.get("totalAmount"),
                "monto_aplicado": d.get("appliedAmount"),
                "saldo_pendiente": d.get("pendingBalance"),
                "estado": d.get("documentStatus") or "",
                "fecha_emision": d.get("issueDate") or "",
                "fecha_vencimiento": d.get("dueDate") or "",
                "canjeado_por_letras": bool(d.get("hasLetterExchange")),
                "numero_canje": d.get("exchangeNumber") or "",
            })

    return {
        "numero": numero,
        "encontrado": True,
        "mensaje": datos.get("message") or "",
        "documentos": documentos,
    }
