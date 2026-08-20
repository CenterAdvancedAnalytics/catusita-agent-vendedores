"""Área `pedidos` de Vendedores.

Contesta: ¿Dónde está el pedido y ya llegó?

CONTRATO PÚBLICO: MODELO, NODO, TOOLS. Nadie importa `backend`, `servicio`,
`prompt` ni `agente` desde afuera.

No le habla a ninguna otra área. Si le falta un dato, vuelve al orquestador.
"""
from vendedores.agentes.pedidos.agente import NODO, MODELO
from vendedores.agentes.pedidos.tools import TOOLS

# Lo que el orquestador ve de esta área. Es la PREGUNTA que contesta, no
# la lista de sus tools: el orquestador delega en el área y es ella la que
# decide cuáles usar y en qué orden.
#
# Dice también qué NO tiene y que resuelve el cliente sola. Sin eso el
# orquestador pasa primero por `clientes` para "verificar" —un salto de más en
# cada consulta— o delega igual algo que esta área no puede contestar y se
# entera después de esperarla.
DESCRIPCION = (
    "Pedidos de un cliente y su despacho: estado del pedido, monto, N° de factura "
    "SUNAT, notas de crédito, guía de remisión y si ya se entregó.\n"
    "Le pasás el cliente por NOMBRE o RUC y ella lo resuelve dentro de la cartera "
    "del asesor — no hace falta consultar `clientes` antes.\n"
    "También QUÉ PRODUCTOS compra un cliente: lo que más lleva, en unidades y "
    "en plata, sacado del detalle de sus facturas.\n"
    "NO tiene: totales de venta de la cartera ni acumulados por período."
)

__all__ = ["MODELO", "DESCRIPCION", "NODO", "TOOLS"]
