"""Área `productos` de Vendedores.

Contesta: ¿Qué pieza es, existe, cuánto vale y cómo se ve?
Entidad:  el SKU

    EXPONE DATOS SENSIBLES:
      - precio NETO (el que el asesor cotiza, ya con descuento)
      - stock desglosado por almacén

    Por eso su gemela en `clientes/` NO es una copia: aquella pide
    `tipo="lista"` y su backend ni siquiera acepta el argumento `tipo`.

CONTRATO PÚBLICO: MODELO, NODO, TOOLS.

Nadie importa `servicio`, `backend`, `prompt` ni `agente` desde afuera. El
orquestador delega en el ÁREA, no llama a sus tools: no sabe que existen
`consultar_stock` ni `enviar_imagen_producto`.

No le habla a ninguna otra área. Si le falta un dato, vuelve al orquestador.
Hoy no le falta ninguno: se basta con el SKU.
"""
from vendedores.agentes.productos.agente import NODO, MODELO
from vendedores.agentes.productos.tools import TOOLS

# Lo que el orquestador ve de esta área. Es la PREGUNTA que contesta, no
# la lista de sus tools: el orquestador delega en el área y es ella la que
# decide cuáles usar y en qué orden.
DESCRIPCION = (
    "Qué pieza es, si hay stock, cuánto vale y cómo se ve. Catálogo, SKU, "
    "disponibilidad, precio y foto de un producto (la foto se manda al chat).\n"
    "Buscá acá también las equivalencias entre marcas y para qué vehículo aplica "
    "una pieza, que salen de la descripción del catálogo."
)

__all__ = ["MODELO", "DESCRIPCION", "NODO", "TOOLS"]
