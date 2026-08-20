from shared.sap_client import sap


async def consultar_precio(sku_code: str, tipo: str = None) -> dict:
    """
    tipo puede ser 'neto' (solo para vendedores) o 'lista' (para clientes).
    Si no se especifica, devuelve ambos precios.
    """
    return await sap.get_precios(sku_code, tipo=tipo)
