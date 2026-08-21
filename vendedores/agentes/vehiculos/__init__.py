"""Área `vehiculos` de Vendedores.

Contesta: ¿Qué auto es esta placa?

CONTRATO PÚBLICO: MODELO, NODO, TOOLS. Nadie importa `backend`, `servicio`,
`prompt` ni `agente` desde afuera.

No le habla a ninguna otra área. Si le falta un dato, vuelve al orquestador.
"""
from vendedores.agentes.vehiculos.agente import NODO, MODELO
from vendedores.agentes.vehiculos.tools import TOOLS

# Lo que el orquestador ve de esta área. Es la PREGUNTA que contesta, no
# la lista de sus tools: el orquestador delega en el área y es ella la que
# decide cuáles usar y en qué orden.
DESCRIPCION = (
    "Qué vehículo es una placa peruana: marca, modelo, año, VIN y motor. "
    "También manda la foto de la tarjeta al chat.\n"
    "Tarda 30-60 segundos: avisale al asesor que espere y pedila una sola vez."
)

__all__ = ["MODELO", "DESCRIPCION", "NODO", "TOOLS"]
