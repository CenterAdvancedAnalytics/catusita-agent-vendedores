"""Orquestador de Vendedores. Único que le habla al usuario y único que coordina.

Ve 6 áreas, no la lista de tools internas:

      productos        ¿Qué pieza es, existe, cuánto vale y cómo se ve?
      vehiculos        ¿Qué auto es esta placa?
      clientes         ¿Quién es este cliente y está en mi cartera?
      pedidos          ¿Dónde está el pedido, ya llegó y qué compra?
      facturacion      ¿Está pagada? Mandame el PDF.
      conocimiento     ¿Cómo se hace esto? (fallback)

Los procedimientos de Catusita viven en `conocimiento_vendedores`; `contexto` se
los deja servidos en cada turno. El criterio de qué va dónde: si algo se puede
corregir sin redeployar, va en el RAG.
"""
import os

from langchain_anthropic import ChatAnthropic

from vendedores import registro
from vendedores.plataforma_vendedores.estado import EstadoAgente

MODELO = os.getenv("MODELO_ORQUESTADOR", "claude-sonnet-4-6")

SYSTEM = """Sos Catu, el asistente de Grupo Catusita para asesores comerciales
internos. Catusita distribuye repuestos automotrices en Perú.

QUIÉN TE ESCRIBE

Un asesor de la empresa, desde el celular, entre visitas o con un cliente
esperando. Escribe corto, con typos y sin contexto.

CÓMO CONTESTÁS

Para WhatsApp: 3-4 líneas por bloque, sin tablas largas, sin markdown pesado.
Directo: el asesor quiere el dato, no la explicación.

Cuando nombres algo del sistema, va con su identificador — cliente con RUC,
producto con SKU, pedido y factura con su número. Es lo que necesita para el
paso siguiente. Si son varios, poné menos elementos, nunca menos campos.

CÓMO TRABAJÁS

Tenés áreas, no herramientas sueltas. Delegá en la que corresponda y usá lo que
traiga. Si una pregunta necesita dos, usá las dos antes de contestar y cruzá vos
el resultado.

Si te sirvieron procesos de Catusita, seguilos. Si no, resolvé igual con tus
áreas y tu criterio: que no haya procedimiento escrito no te impide trabajar.

LO QUE NO HACÉS

No inventás. Precio, stock, fechas, estados de pago: si no salió de un área, no
existe.

No autorizás excepciones ni precios especiales. Podés decir cómo se piden.

No prometés. «Te llega mañana» solo si un área lo dijo.

Si un área devuelve un error, decilo y seguí. No reintentes en bucle.

El asesor ve solo SU cartera. Si el área rechaza un cliente ajeno, comunicalo
sin buscar la vuelta."""

_llm = None


def _modelo():
    """Se arma en la primera llamada, no al importar: `tools_de_delegacion()`
    necesita que todas las áreas ya estén cargadas."""
    return ChatAnthropic(model=MODELO, temperature=0).bind_tools(
        registro.tools_de_delegacion()
    )


def _bloque_procesos(procesos: list) -> str:
    """Los procedimientos que `contexto` recuperó, listos para ejecutar.

    `descripcion` no entra: sirvió para encontrarlos y el orquestador ya tiene
    el mensaje del usuario adelante.
    """
    lineas = [
        "\nCÓMO SE HACE ESTO EN CATUSITA",
        "",
        "Esto no es contexto: es el procedimiento. Seguilo.",
        "Si ninguno resuelve lo que preguntan, atendelo igual con tus áreas.",
    ]
    for p in procesos:
        lineas.append(f"\n— {p['proceso']}")
        lineas.append(f"{p['procedimiento']}")
    return "\n".join(lineas)


def _system_del_turno(state: EstadoAgente) -> str:
    """El prompt fijo más lo que cambia en este turno.

    Lo variable va DESPUÉS del bloque fijo para que el prefijo sea idéntico en
    todas las llamadas y el prompt caching lo aproveche.
    """
    partes = [SYSTEM]

    perfil = state.get("perfil") or {}
    if nombre := perfil.get("nombre"):
        partes.append(f"\nEstás hablando con {nombre}.")

    if procesos := state.get("procesos"):
        partes.append(_bloque_procesos(procesos))

    val = state.get("validacion") or {}
    if val.get("ok") is False and (motivo := val.get("motivo")):
        partes.append(
            f"\nCORRECCIÓN: tu respuesta anterior no se envió porque {motivo} "
            f"Rehacela teniendo eso en cuenta."
        )

    return "\n".join(partes)


async def nodo_orquestador(state: EstadoAgente) -> dict:
    """Un paso del orquestador: mirar todo y decidir si delega o contesta."""
    global _llm
    if _llm is None:
        _llm = _modelo()

    mensajes = [{"role": "system", "content": _system_del_turno(state)}]
    mensajes += state.get("historial") or []
    mensajes += state["messages"]

    return {"messages": [await _llm.ainvoke(mensajes)]}
