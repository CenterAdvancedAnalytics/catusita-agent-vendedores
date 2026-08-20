from shared.sap_client import sap


async def consultar_cartera(vendedor_id: str, estado: str = None, tipo: str = None) -> dict:
    """
    Devuelve la cartera de clientes asignada al vendedor.
    estado: 'activo', 'suspendido', 'bloqueado'
    tipo: 'taller', 'distribuidor', 'consumidor_final'
    """
    return await sap.get_cartera_vendedor(vendedor_id, estado=estado, tipo=tipo)


async def consultar_perfil_cliente(ruc: str) -> dict:
    """Devuelve el perfil completo de un cliente por RUC."""
    return await sap.get_cliente(ruc)
