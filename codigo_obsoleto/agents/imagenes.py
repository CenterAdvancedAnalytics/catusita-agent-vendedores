from shared.sap_client import sap


async def obtener_imagenes(sku: str) -> dict:
    """Devuelve las fotos de un producto (ya descargadas en base64) por su SKU."""
    return await sap.get_imagen(sku)
