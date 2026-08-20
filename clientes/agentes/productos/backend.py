"""Acceso de `productos` (clientes) a la API de Catusita.

    GET /api/stock/filter?ItemCode=      cuánto hay
    GET /api/price/filter?ItemCode=      cuánto vale
    GET /api/article/filter?SearchText=  qué existe (y las fotos)

Es un archivo distinto del de `vendedores` aunque peguen a los MISMOS endpoints.
Esa duplicación es el punto, no un descuido: acá vive la frontera.

── La frontera es una ALLOWLIST, no una regla ─────────────────────────────────

Cada respuesta se recorta a los campos permitidos, uno por uno. No se filtra
«lo prohibido»: se copia «lo permitido».

La diferencia importa. Con una blocklist, el día que el endpoint agregue un
campo nuevo, pasa derecho al modelo y de ahí al chat de un cliente — sin que
nadie cambie una línea ni salte un test. Con allowlist, un campo nuevo
simplemente no existe de este lado hasta que alguien decida agregarlo a mano.

Y al pasar del wrapper a la API directa la allowlist ya sirvió para algo: la
respuesta de stock ahora trae `por_empresa`, o sea en qué sociedad del grupo
está la mercadería. Ese campo no está en `_STOCK`, así que nunca salió por acá.

── El precio ──────────────────────────────────────────────────────────────────

`precios()` no acepta el argumento `cliente_codigo`. En vendedores sí lo acepta,
y esa es toda la diferencia entre los dos precios que devuelve la API:

    sin CodeClient  ->  LISTA_DEFECTO   (el de mostrador, público)
    con CodeClient  ->  LISTA_CLIENTE   (el negociado, interno)

No tener el argumento es que no haya forma de pedir el segundo desde este canal.
No es un default prudente — es que la llamada no se puede escribir.
"""
import asyncio
import base64
import mimetypes
import os

from clientes.agentes.productos import busqueda
from clientes.plataforma_clientes import catusita_api

TIMEOUT_IMAGEN = 30.0
MAX_CATALOGO = 15


def _recortar(datos: dict, permitidos: tuple) -> dict:
    """Deja SOLO los campos de `permitidos`. Los errores pasan enteros.

    Un `{"error": ...}` no se recorta porque no trae datos del negocio y porque
    el orquestador necesita el texto completo para poder explicarlo.
    """
    if not isinstance(datos, dict) or datos.get("error"):
        return datos
    return {k: datos[k] for k in permitidos if k in datos}


# Campos que este canal puede ver. Agregar uno acá es una decisión explícita.
_STOCK = ("sku", "nombre", "marca", "unidad", "disponible")
_PRECIO = ("sku", "precio_lista", "moneda")
_PRODUCTO = ("sku", "nombre", "categoria", "marca")


def _lista(datos) -> list[dict] | dict:
    if isinstance(datos, dict) and datos.get("error"):
        return datos
    return datos if isinstance(datos, list) else []


async def stock(sku: str) -> dict:
    """Si hay o no. NO devuelve la cantidad exacta ni en qué empresa está.

    La cantidad se omite a propósito: a un cliente le sirve saber si puede pasar
    a buscarlo, y el número exacto es información de inventario que además
    envejece mal — se la damos y a la hora ya no es cierta.
    """
    datos = _lista(await catusita_api.get("/api/stock/filter", {"ItemCode": sku}))
    if isinstance(datos, dict):
        return datos
    if not datos:
        return {"error": "PRODUCTO_NO_ENCONTRADO",
                "mensaje": f"No encontramos ningún producto con el código '{sku}'."}

    total = 0.0
    for f in datos:
        try:
            total += float(f.get("stock") or 0)
        except (TypeError, ValueError):
            pass

    primera = datos[0]
    return _recortar({
        "sku": primera.get("itemCode") or sku,
        "nombre": primera.get("itemName") or "",
        "marca": primera.get("brandName") or primera.get("nameSupply") or "",
        "unidad": primera.get("inventoryUnitOfMeasure") or "",
        "disponible": total > 0,
    }, _STOCK)


async def precios(sku: str) -> dict:
    """Precio de LISTA. Sin `cliente_codigo`: ver la nota del encabezado."""
    datos = _lista(await catusita_api.get("/api/price/filter", {"ItemCode": sku}))
    if isinstance(datos, dict):
        return datos
    if not datos:
        return {"error": "PRODUCTO_NO_ENCONTRADO",
                "mensaje": f"No hay precio publicado para el código '{sku}'."}

    f = datos[0]
    return _recortar({
        "sku": f.get("itemCode") or sku,
        "precio_lista": f.get("finalPrice"),
        "moneda": f.get("currency") or "USD",
    }, _PRECIO)


async def catalogo(q: str | None = None, categoria: str | None = None,
                   marca: str | None = None) -> dict:
    """Búsqueda tolerante a cómo escribe la gente. Ver `busqueda.py`.

    `SearchText` de la API es substring literal: 'filtro de aceite para Toyota'
    devuelve cero. Se le pide UN token y el filtro fino corre acá.

    El cliente escribe todavía peor que el asesor —no sabe los códigos ni cómo
    los abrevia el catálogo—, así que acá importa más que en vendedores.
    """
    analisis = busqueda.analizar(q or "")
    tokens, codigos = analisis["tokens"], analisis["codigos"]
    consulta_api = (codigos[0] if codigos
                    else busqueda.token_mas_fuerte(tokens)) or (q or "")

    datos = _lista(await catusita_api.get(
        "/api/article/filter", {"SearchText": consulta_api, "BrandName": marca}))
    if isinstance(datos, dict):
        return datos

    if not datos and codigos:
        for tramo in busqueda.tramos_de_codigo(codigos[0]):
            if len(tramo) < busqueda.MIN_TOKEN:
                continue
            datos = _lista(await catusita_api.get(
                "/api/article/filter", {"SearchText": tramo, "BrandName": marca}))
            if isinstance(datos, dict):
                return datos
            if datos:
                break

    restantes = [t for t in tokens if t != consulta_api]
    encontrados, soltados = busqueda.filtrar(datos, restantes, codigos)

    productos = [
        _recortar({
            "sku": f.get("itemCode") or "",
            "nombre": f.get("itemName") or "",
            "categoria": f.get("subSpecialtyName") or f.get("specialtyName") or "",
            "marca": f.get("brandName") or f.get("nameSupply") or "",
        }, _PRODUCTO)
        for f in encontrados
    ]

    if categoria:
        buscada = busqueda.normalizar(categoria)
        productos = [p for p in productos
                     if buscada in busqueda.normalizar(p.get("categoria") or "")]

    resultado: dict = {"productos": productos[:MAX_CATALOGO]}
    if analisis["ignorados"]:
        resultado["no_se_filtro_por"] = {
            "valores": analisis["ignorados"],
            "mensaje": ("El año y el código de motor no están en el texto del "
                        "catálogo, así que NO se filtró por ellos. Avisale y "
                        "pedile que confirme el modelo."),
        }
    if soltados:
        resultado["se_relajo"] = {
            "terminos": soltados,
            "mensaje": ("Con todos los términos no había ninguno, así que se "
                        f"buscó sin {soltados}. Decilo al mostrar los resultados."),
        }
    return resultado


async def imagen(sku: str) -> dict:
    """Foto(s) del producto, descargadas y en base64.

    No pasa por `_recortar`: lo que devuelve son bytes de una imagen y su nombre
    de archivo, no datos del negocio. La allowlist protege campos, y acá no hay
    ninguno que pueda filtrar información interna.
    """
    datos = _lista(await catusita_api.get("/api/article/filter", {"ItemCode": sku}))
    if isinstance(datos, dict):
        return datos
    if not datos:
        return {"error": "PRODUCTO_NO_ENCONTRADO",
                "mensaje": f"No encontramos ningún producto con el código '{sku}'."}

    fila = datos[0]
    urls = [u for u in (fila.get("images") or []) if u]
    if not urls:
        return {"error": "SIN_IMAGEN",
                "mensaje": f"El producto {sku} no tiene foto disponible."}

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
