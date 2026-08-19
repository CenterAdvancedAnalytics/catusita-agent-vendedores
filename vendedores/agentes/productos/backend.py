"""Acceso de `productos` (vendedores) a la API de Catusita.

    GET /api/stock/filter?ItemCode=      cuánto hay
    GET /api/price/filter?ItemCode=      cuánto vale
    GET /api/article/filter?SearchText=  qué existe (y las fotos)

── Por qué su propio backend y no uno compartido ──────────────────────────────

Había un `shared/sap_client.py` con los 12 endpoints, y todas las áreas lo
importaban. Eso significaba que `productos` podía llamar a la cartera de un
asesor sin que nada lo impidiera: el método estaba ahí, a un punto de distancia.

Acá no está. `productos` no tiene forma de consultar una cartera porque no
existe la función en su backend. No es una regla que haya que recordar — es que
el código no está.

── El precio, y lo que cambió al ir directo ───────────────────────────────────

El wrapper inventaba dos precios, "neto" y "lista". La API real no tiene esa
distinción: tiene UNA lista de precios que depende de a qué cliente se le venda.

    sin CodeClient   ->  priceListName: "LISTA_DEFECTO"   (el de mostrador)
    con CodeClient   ->  priceListName: "LISTA_CLIENTE"   (el que le toca a él)

Así que el precio del cliente sale mandando su código, y el genérico sale sin
mandarlo. `priceListName` viaja hasta el modelo a propósito: es lo único que
distingue un precio del otro, y sin eso el asesor no sabe cuál está cotizando.

Cuidado con `CodeSeller`: mandarlo SIN `CodeClient` devuelve 500. Por eso solo
se manda acompañado.

── «No encontrado» es una lista vacía ─────────────────────────────────────────

    /api/stock/filter?ItemCode=NOEXISTE   ->   200  {"data": [], "isValid": true}

No es un 404 ni un error. Acá se traduce a `{"error": ...}` para que
`servicio.con_sugerencias` lo detecte y ofrezca alternativas del catálogo.

── Las fotos ──────────────────────────────────────────────────────────────────

`article/filter` trae `images` como URLs a otro puerto (`:8086`), no como
base64. WhatsApp necesita los bytes, así que se descargan acá. Es la única
función del área que hace dos saltos de red, y por eso tiene su propio timeout.
"""
import asyncio
import base64
import mimetypes
import os

from vendedores.plataforma_vendedores import catusita_api

TIMEOUT_IMAGEN = 30.0

# Una consulta de catálogo puede traer decenas de artículos; al asesor le entran
# unos pocos por WhatsApp. El corte es acá y no en el modelo para no gastar
# contexto en 80 productos que no va a leer.
MAX_CATALOGO = 25


def _articulo(fila: dict) -> dict:
    return {
        "sku": fila.get("itemCode") or "",
        "nombre": fila.get("itemName") or "",
        "marca": fila.get("brandName") or fila.get("nameSupply") or "",
        "categoria": fila.get("subSpecialtyName") or fila.get("specialtyName") or "",
        "codigo_proveedor": fila.get("supplierCatalogNumber") or "",
        "aplicacion": fila.get("foreignName") or "",
        "unidad": fila.get("inventoryUnitOfMeasure") or "",
    }


def _lista(datos) -> list[dict] | dict:
    """Normaliza la respuesta: lista de filas, o el `{"error": ...}` tal cual."""
    if isinstance(datos, dict) and datos.get("error"):
        return datos
    return datos if isinstance(datos, list) else []


async def stock(sku: str) -> dict:
    datos = _lista(await catusita_api.get("/api/stock/filter", {"ItemCode": sku}))
    if isinstance(datos, dict):
        return datos
    if not datos:
        return {"error": "PRODUCTO_NO_ENCONTRADO",
                "mensaje": f"No existe ningún producto con el SKU '{sku}'."}

    # Un SKU puede venir repetido, una fila por empresa del grupo.
    total = 0.0
    por_empresa = []
    for f in datos:
        try:
            cantidad = float(f.get("stock") or 0)
        except (TypeError, ValueError):
            cantidad = 0.0
        total += cantidad
        por_empresa.append({"empresa": f.get("companyDefinition") or "",
                            "cantidad": cantidad})

    primera = datos[0]
    return {
        **_articulo(primera),
        "stock_total": total,
        "unidad": primera.get("inventoryUnitOfMeasure") or "",
        "por_empresa": por_empresa,
        "hay_stock": total > 0,
    }


async def precios(sku: str, tipo: str, cliente_codigo: str | None = None) -> dict:
    """Precio del SKU. Con `cliente_codigo` es el del cliente; sin él, el genérico.

    `tipo` se mantiene en la firma porque las tools lo pasan, pero la API real no
    distingue neto de lista: lo que distingue es si va o no el código de cliente.
    Se devuelve `lista_precio` para que quede explícito cuál se está cotizando.
    """
    params = {"ItemCode": sku}
    if cliente_codigo:
        params["CodeClient"] = cliente_codigo

    datos = _lista(await catusita_api.get("/api/price/filter", params))
    if isinstance(datos, dict):
        return datos
    if not datos:
        return {"error": "PRODUCTO_NO_ENCONTRADO",
                "mensaje": f"No hay precio cargado para el SKU '{sku}'."}

    f = datos[0]
    return {
        "sku": f.get("itemCode") or sku,
        "precio": f.get("finalPrice"),
        "moneda": f.get("currency") or "USD",
        "lista_precio": f.get("priceListName") or "",
        "es_precio_de_cliente": bool(cliente_codigo),
    }


async def catalogo(q: str | None = None, categoria: str | None = None,
                   marca: str | None = None, con_stock: bool | None = None) -> dict:
    """Búsqueda de artículos. `categoria` y `con_stock` no existen en la API.

    `categoria` se aplica acá, filtrando por `subSpecialtyName`/`specialtyName`,
    porque la API no tiene ese parámetro pero sí devuelve el campo. `con_stock`
    en cambio exigiría una consulta de stock por artículo, así que no se aplica
    y se avisa — filtrar de mentira es peor que no filtrar.
    """
    datos = _lista(await catusita_api.get(
        "/api/article/filter", {"SearchText": q, "BrandName": marca}))
    if isinstance(datos, dict):
        return datos

    productos = [_articulo(f) for f in datos]

    if categoria:
        buscada = categoria.lower()
        productos = [p for p in productos if buscada in (p["categoria"] or "").lower()]

    resultado: dict = {
        "total": len(productos),
        "productos": productos[:MAX_CATALOGO],
    }
    if len(productos) > MAX_CATALOGO:
        resultado["mensaje"] = (
            f"Hay {len(productos)} coincidencias; se muestran las primeras "
            f"{MAX_CATALOGO}. Si ninguna sirve, pedile al usuario que precise.")
    if con_stock is not None:
        resultado["filtros_ignorados"] = {
            "campos": ["con_stock"],
            "mensaje": ("No se pudo filtrar por stock: hay que consultarlo SKU "
                        "por SKU. Usá consultar_stock con los que interesen."),
        }
    return resultado


async def imagen(sku: str) -> dict:
    """Foto(s) del producto, descargadas y en base64.

    Las URLs se bajan en paralelo: son dos o tres y secuencial suma latencia
    sobre un turno de WhatsApp que ya viene de dos llamadas.
    """
    datos = _lista(await catusita_api.get("/api/article/filter", {"ItemCode": sku}))
    if isinstance(datos, dict):
        return datos
    if not datos:
        return {"error": "PRODUCTO_NO_ENCONTRADO",
                "mensaje": f"No existe ningún producto con el SKU '{sku}'."}

    fila = datos[0]
    urls = [u for u in (fila.get("images") or []) if u]
    if not urls:
        return {"error": "SIN_IMAGEN",
                "mensaje": f"El producto {sku} no tiene foto cargada."}

    descargas = await asyncio.gather(
        *(catusita_api.descargar(u, timeout=TIMEOUT_IMAGEN) for u in urls))

    imagenes = []
    for url, contenido in zip(urls, descargas):
        if not contenido:
            continue
        nombre = os.path.basename(url) or f"{sku}.png"
        imagenes.append({
            "base64": base64.b64encode(contenido).decode(),
            "filename": nombre,
            "mime": mimetypes.guess_type(nombre)[0] or "image/png",
        })

    if not imagenes:
        return {"error": "SIN_IMAGEN",
                "mensaje": f"No se pudieron descargar las fotos de {sku}."}

    return {"sku": sku, "nombre": fila.get("itemName") or "", "imagenes": imagenes}


async def cerrar() -> None:
    await catusita_api.close()
