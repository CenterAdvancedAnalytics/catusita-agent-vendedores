from shared.sap_client import sap


async def consultar_stock(sku_code: str) -> dict:
    result = await sap.get_stock(sku_code)
    if "error" in result:
        return result
    return result
