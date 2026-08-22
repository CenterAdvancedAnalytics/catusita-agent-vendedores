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
import asyncio

from vendedores.agentes.pedidos import comprobantes
from vendedores.plataforma_vendedores import catusita_api

# Cuántos pedidos traer cuando nadie dice cuántos. La API pide el número; sin
# tope devolvería el histórico entero de un cliente de veinte años.
PEDIDOS_POR_DEFECTO = 20

# Los XML viven en otro puerto y son de ~20 KB cada uno.
TIMEOUT_XML = 30.0

# Cuántos productos devolver en cada ranking. Es lo que entra en un WhatsApp;
# el modelo igual tiene las líneas crudas si necesita re-cortar.
MAX_PRODUCTOS = 10


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

    # Sumado acá y no por el modelo. Medido sobre estos mismos 10 pedidos: el
    # Haiku del área dio 16.650,30 y el Sonnet del orquestador 18.650,30. La
    # suma real es 17.450,29 — dos modelos, dos errores distintos, los mismos
    # diez números. Y ninguno de los dos puede notar que se equivocó.
    #
    # Va POR MONEDA: un cliente puede tener pedidos en USD y en soles, y un solo
    # número sería un total falso.
    por_moneda: dict[str, float] = {}
    for p in lista:
        m = p.get("moneda") or "?"
        por_moneda[m] = round(por_moneda.get(m, 0.0) + float(p.get("monto") or 0), 2)

    return {
        "ruc": cliente_ruc,
        "cliente": (ordenes[0].get("clientName") if ordenes else "") or "",
        "total_pedidos": len(lista),
        "total_por_moneda": por_moneda,
        "pedidos": lista,
    }


async def _marca(sku: str) -> str:
    """La marca de un SKU, del catálogo. Vacío si no está.

    `ItemCode` NO busca exacto: busca por coincidencia. Pedir `48328` devuelve
    cuatro filas —el 48328 de NARVA, y ENR48328 / ENR48328B / ENR48328Y de
    ENERTECH, que lo llevan como `supplierCatalogNumber`—.

    Tomar la primera daba una marca distinta en cada corrida, y siempre para el
    mismo lado: ENERTECH tiene un equivalente para casi cada foco NARVA. En un
    ranking real eso movió 685 dólares y 3 SKU de una marca a la otra entre dos
    ejecuciones sobre los mismos datos.

    Así que se busca la fila cuyo `itemCode` sea EXACTAMENTE el pedido. Las
    demás son equivalencias, no ese producto.
    """
    d = await catusita_api.get("/api/article/filter", {"ItemCode": sku})
    if not isinstance(d, list):
        return ""
    objetivo = sku.strip().upper()
    for fila in d:
        if (fila.get("itemCode") or "").strip().upper() == objetivo:
            return fila.get("brandName") or ""
    return ""


async def marcas(cliente_ruc: str, cantidad: int = 20) -> dict:
    """Qué MARCAS compra un cliente, ordenadas por plata.

    Contesta «¿qué marca compra más este cliente?» de una sola llamada.

    Hace el mismo recorrido que `compras()` —pedidos, facturas, XML— y le suma
    un paso: la marca de cada SKU, del catálogo. Es a propósito que repita ese
    trabajo en vez de colgarse de `compras()`: aquella devuelve el TOP 10 de
    productos, y agrupar marcas sobre diez de cien líneas da un ranking falso.
    Acá se agrupa sobre TODOS los SKU y recién ahí se ordena.

    Las marcas van completas, no recortadas: son pocas —un cliente real dio
    cuatro— y son la respuesta.
    """
    detalle = await compras(cliente_ruc, cantidad=cantidad, _completo=True)
    if detalle.get("error"):
        return detalle

    todos = detalle.pop("_todos", [])
    if not todos:
        return {"cliente": detalle.get("cliente", ""), "ruc": cliente_ruc,
                "error": "SIN_DETALLE",
                "mensaje": "No se pudo leer el detalle de sus facturas."}

    # Una consulta al catálogo por SKU, todas en paralelo. Medido: 10 SKU en
    # 0.1s. El endpoint de artículos tolera concurrencia.
    encontradas = await asyncio.gather(*(_marca(p["sku"]) for p in todos))

    agrupado: dict[str, dict] = {}
    for p, m in zip(todos, encontradas):
        clave = m or "(sin marca en el catálogo)"
        acum = agrupado.setdefault(clave, {"marca": clave, "monto": 0.0,
                                           "unidades": 0.0, "skus": 0})
        acum["monto"] += p["monto"]
        acum["unidades"] += p["unidades"]
        acum["skus"] += 1

    ranking = sorted(agrupado.values(), key=lambda x: -x["monto"])
    for r in ranking:
        r["monto"] = round(r["monto"], 2)
        r["unidades"] = round(r["unidades"], 1)

    return {
        "cliente": detalle.get("cliente", ""),
        "ruc": cliente_ruc,
        "moneda": detalle.get("moneda", ""),
        "pedidos_revisados": detalle.get("pedidos_revisados", 0),
        "facturas_leidas": detalle.get("facturas_leidas", 0),
        "skus_distintos": len(todos),
        "por_marca": ranking,
        **({"ojo_monedas": detalle["ojo_monedas"]} if "ojo_monedas" in detalle else {}),
        **({"devoluciones": detalle["devoluciones"]} if "devoluciones" in detalle else {}),
    }


async def compras(cliente_ruc: str, cantidad: int = 20,
                  _completo: bool = False) -> dict:
    """Qué productos compró un cliente, sacados del XML de sus facturas.

    ── Por qué existe y por qué es la única que hace tres saltos ──────────────

    Ningún endpoint devuelve el detalle de un pedido: `/api/sales/orders/documents`
    da el monto total y nada más. Por eso «¿qué compra más este cliente?» estaba
    anotado como imposible en docs/pendientes_ventas.md.

    Está en el XML de la factura electrónica, que por ley SUNAT trae las líneas.
    Llegar ahí son tres saltos encadenados:

        1. los pedidos del cliente          -> sus facturas
        2. cada factura                     -> su xmlUrl
        3. el XML                           -> SKU, unidades, importe

    Medido sobre un cliente real: 10 pedidos, 12 facturas, 62 líneas, 1.2 s.

    ── Por qué devuelve las líneas Y los totales ──────────────────────────────

    Los totales se suman ACÁ, en Python. El modelo no suma: se equivoca en
    aritmética y un total mal calculado sale con toda la cara de verdad —
    «tu cliente compró USD 1.801 de Valvoline» y es mentira.

    Las líneas van igual para que el modelo pueda re-cortar sin una tool nueva:
    «¿y solo Valvoline?», «¿y del último mes?» se contestan con lo que ya tiene.
    """
    pedidos_ = await pedidos(cliente_ruc, cantidad=cantidad)
    if pedidos_.get("error"):
        return pedidos_

    # Facturas y notas de crédito, por separado: las primeras suman y las
    # segundas restan.
    docs, notas_cred = [], []
    for p in pedidos_.get("pedidos") or []:
        for d in p.get("documentos") or []:
            if d.get("numero"):
                docs.append((d["numero"], d.get("tipo_codigo") or "01",
                             d.get("empresa_codigo") or "", p.get("fecha", "")))
            for nc in d.get("notas_credito") or []:
                if nc.get("numero"):
                    notas_cred.append((nc["numero"], nc.get("tipo_codigo") or "07",
                                       nc.get("empresa_codigo") or d.get("empresa_codigo") or "",
                                       nc.get("fecha", "")))

    if not docs:
        return {"cliente": pedidos_.get("cliente", ""), "ruc": cliente_ruc,
                "pedidos_revisados": pedidos_.get("total_pedidos", 0),
                "error": "SIN_FACTURAS",
                "mensaje": ("Ese cliente no tiene facturas con detalle en los "
                            "últimos pedidos, así que no se puede saber qué "
                            "productos compró. No lo deduzcas de los montos.")}

    # Las URLs de los XML. Una consulta por documento, en paralelo: son de
    # `electronic-documents`, que sí tolera concurrencia.
    urls = await asyncio.gather(*(_url_xml(n, t, e) for n, t, e, _ in docs))

    # Los XML se bajan del :8086, que manda un header con espacio en el nombre.
    # Solo `catusita_api.descargar` (urllib, en un hilo) lo tolera.
    xmls = await asyncio.gather(
        *(catusita_api.descargar(u, timeout=TIMEOUT_XML) for u in urls if u))

    por_sku: dict[str, dict] = {}
    total_lineas = 0
    for crudo in xmls:
        if not crudo:
            continue
        for l in comprobantes.lineas(crudo.decode("utf-8", errors="replace")):
            total_lineas += 1
            clave = l["sku"] or l["descripcion"][:40]
            acum = por_sku.setdefault(clave, {
                "sku": l["sku"], "descripcion": l["descripcion"],
                "unidades": 0.0, "monto": 0.0, "moneda": l["moneda"]})
            acum["unidades"] += l["unidades"]
            acum["monto"] += l["monto"]

    if not por_sku:
        return {"cliente": pedidos_.get("cliente", ""), "ruc": cliente_ruc,
                "error": "SIN_DETALLE",
                "mensaje": "No se pudo leer el detalle de sus facturas."}

    # ── Restar lo devuelto ────────────────────────────────────────────────────
    #
    # Sin esto los números mienten y no poco: un cliente de prueba tenía 20
    # unidades de refrigerante en el ranking y había devuelto 14. Lo real eran
    # 6, y el producto se cae del top.
    #
    # No todas las NC restan lo mismo. El catálogo 09 de SUNAT distingue una
    # DEVOLUCIÓN —el cliente entregó la mercadería— de un DESCUENTO, donde se
    # quedó con ella y solo se le bajó el precio. Restar unidades en un
    # descuento haría figurar como devolución una rebaja negociada.
    devueltas = {"unidades": 0.0, "monto": 0.0, "documentos": 0}
    if notas_cred:
        urls_nc = await asyncio.gather(
            *(_url_xml(n, t, e) for n, t, e, _ in notas_cred))
        xmls_nc = await asyncio.gather(
            *(catusita_api.descargar(u, timeout=TIMEOUT_XML) for u in urls_nc if u))

        for crudo in xmls_nc:
            if not crudo:
                continue
            texto = crudo.decode("utf-8", errors="replace")
            codigo = comprobantes.motivo(texto)
            if codigo in comprobantes.SOLO_PLATA:
                resta_unidades = False
            elif codigo in comprobantes.DEVUELVE_MERCADERIA or not codigo:
                resta_unidades = True
            else:
                continue          # corrección de descripción y similares: no toca nada

            devueltas["documentos"] += 1
            for l in comprobantes.lineas(texto):
                clave = l["sku"] or l["descripcion"][:40]
                if clave not in por_sku:
                    continue      # devolvió algo que no está en los pedidos leídos
                if resta_unidades:
                    por_sku[clave]["unidades"] -= l["unidades"]
                    devueltas["unidades"] += l["unidades"]
                por_sku[clave]["monto"] -= l["monto"]
                devueltas["monto"] += l["monto"]

        # Un producto puede quedar en cero o negativo si se devolvió todo.
        por_sku = {k: v for k, v in por_sku.items()
                   if v["unidades"] > 0.01 or v["monto"] > 0.01}

    productos = sorted(por_sku.values(), key=lambda x: -x["monto"])
    for p in productos:
        p["monto"] = round(p["monto"], 2)
        p["unidades"] = round(p["unidades"], 1)

    # Si una factura vino en soles y otra en dólares, sumarlas sería inventar
    # un número. Se avisa en vez de mezclar.
    monedas = {p["moneda"] for p in productos if p["moneda"]}

    resultado = {
        "cliente": pedidos_.get("cliente", ""),
        "ruc": cliente_ruc,
        "moneda": monedas.pop() if len(monedas) == 1 else "",
        "pedidos_revisados": pedidos_.get("total_pedidos", 0),
        "facturas_leidas": sum(1 for x in xmls if x),
        "lineas": total_lineas,
        # Los dos ordenamientos, ya sumados. El modelo elige cuál mostrar.
        "por_monto": productos[:MAX_PRODUCTOS],
        "por_unidades": sorted(productos, key=lambda x: -x["unidades"])[:MAX_PRODUCTOS],
    }

    # Para `marcas()`, que necesita agrupar sobre TODOS los SKU y no sobre el
    # top 10. Se saca con `pop` antes de devolverlo, así nunca llega al modelo:
    # son cien líneas que no le sirven y que le costarían contexto.
    if _completo:
        resultado["_todos"] = [p for p in productos if p.get("sku")]

    if len(monedas) > 1:
        resultado["ojo_monedas"] = {
            "monedas": sorted(monedas),
            "mensaje": ("Hay facturas en más de una moneda y los montos están "
                        "sumados sin convertir. Usá el ranking por UNIDADES, "
                        "que sí es comparable, y avisá del problema."),
        }

    if devueltas["documentos"]:
        resultado["devoluciones"] = {
            "notas_credito": devueltas["documentos"],
            "unidades": round(devueltas["unidades"], 1),
            "monto": round(devueltas["monto"], 2),
            "mensaje": ("Estos totales YA tienen descontado lo devuelto. No "
                        "hace falta que lo aclares salvo que pregunten."),
        }
    return resultado


async def _url_xml(numero: str, tipo: str, empresa: str) -> str:
    """El `xmlUrl` de una factura. Cadena vacía si no tiene."""
    datos = await catusita_api.get("/api/electronic-documents/search",
                                   {"DocumentNumber": numero,
                                    "DocumentType": tipo, "CompanyCode": empresa})
    if not isinstance(datos, list) or not datos:
        return ""
    d = datos[0]
    return d.get("xmlUrl", "") if d.get("hasXml") else ""


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
