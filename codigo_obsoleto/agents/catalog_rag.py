from agents.buscador_subagente import buscar_catalogo_subagente


async def buscar_catalogo(query: str, placa: str = None, vin: str = None) -> dict:
    """Busca productos en el catálogo.

    La búsqueda se delega al SUBAGENTE de búsqueda (A2A), que razona los términos
    (sinónimos, ignora año/motor, reintenta al dar 0). Si el subagente falla, él
    mismo cae al buscador determinista. Salida idéntica a la de antes (drop-in).
    """
    return await buscar_catalogo_subagente(query, placa=placa, vin=vin)
