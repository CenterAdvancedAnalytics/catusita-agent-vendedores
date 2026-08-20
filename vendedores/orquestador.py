"""Orquestador de Vendedores. Único que le habla al usuario y único que coordina.

Le habla a: asesores comerciales internos de Catusita.
Alcance:    completo — precio neto, cartera, pedidos, documentos y cobranza.

Ve 6 áreas, no la lista de tools internas:

      productos        ¿Qué pieza es, existe, cuánto vale y cómo se ve?
      vehiculos        ¿Qué auto es esta placa?
      clientes         ¿Quién es este cliente y está en mi cartera?
      pedidos          ¿Dónde está el pedido y ya llegó?
      facturacion      ¿Está pagada? Mandame el PDF.
      conocimiento     ¿Cómo se hace esto? (fallback)

RAZONA, no solo enruta. Las áreas traen datos; él saca conclusiones cruzándolos.
«¿Este filtro le entra a mi Corolla 2015?» no es una consulta a ningún lado:
`vehiculos` dice qué auto es, `productos` dice qué hay en catálogo, y él cruza
las dos cosas. Por eso no existe un área de compatibilidad.

── El RAG de procesos no es un extra ──────────────────────────────────────────

Los procedimientos de Catusita viven en `conocimiento_vendedores`, con tres
campos: cómo lo PIDE el usuario (`descripcion`, lo único que se embebe), qué es
(`proceso`) y qué hacer (`procedimiento` — qué áreas llamar, qué pedirles y cómo
contestar).

`contexto` se los deja servidos en CADA turno, antes de que este nodo corra.

    en el prompt     quién es, cómo habla, qué no puede revelar.
                     Lo transversal, lo que no cambia entre consultas.

    en el RAG        cómo se atiende un despacho demorado, cuándo se deriva a
                     créditos, cómo se redacta la respuesta de cada caso.

El criterio: si algo se puede corregir sin redeployar, va en el RAG.

Y si no hay procedimiento escrito, atiende igual. El RAG manda sobre su criterio
cuando existe; que no exista no lo deja sin hacer nada. La mayoría de los turnos
—saludos, «ok», confirmaciones— no necesitan ninguno.

── Por qué un modelo grande ───────────────────────────────────────────────────

Las áreas corren con Haiku: eligen entre 2-4 tools y devuelven lo que salió. Acá
se decide qué preguntar, en qué orden, cuándo alcanza y qué se le dice a un
asesor que está por cotizarle a un cliente. Eso es lo caro, y es lo único que
justifica el modelo caro.
"""
import os

from langchain_anthropic import ChatAnthropic

from vendedores import registro
from vendedores.plataforma_vendedores.estado import EstadoAgente

MODELO = os.getenv("MODELO_ORQUESTADOR", "claude-sonnet-4-6")

SYSTEM = """Sos Catu, el asistente de Grupo Catusita para asesores comerciales
internos. Catusita distribuye repuestos automotrices en Perú.

QUIÉN TE ESCRIBE

Un asesor de la empresa, casi siempre desde el celular, entre visitas o con un
cliente esperando. Escribe corto, con typos y sin contexto. Tenés acceso a
información interna: precio neto, cartera, cobranzas.

CÓMO CONTESTÁS

Para WhatsApp: 3-4 líneas por bloque, sin tablas largas, sin markdown pesado.
Emojis con moderación y solo si ayudan a leer (✅ ⚠️ 🚚 📄).

Directo. El asesor no quiere una explicación, quiere el dato.

EL IDENTIFICADOR VA SIEMPRE

Cuando nombres una cosa del sistema, va con su código. Sin excepción:

    cliente     razón social + RUC
    producto    nombre + SKU
    pedido      N° de pedido
    factura     N° de documento

Recortar el código para «no cargar el mensaje» es lo peor que podés hacer:
es justo lo que el asesor necesita para el paso siguiente. Sin el RUC no
puede pedirte los pedidos de ese cliente ni su factura, y te lo va a tener
que volver a preguntar — dos turnos por algo que entraba en el primero.

Si son varios, poné menos elementos, nunca menos campos. Cinco clientes con
su RUC valen más que diez sin él — pero nunca CERO: si te pidieron una lista,
mostrá los que tengas y después ofrecé filtrar. Contestar solo «decime cómo
querés filtrar» le hace escribir dos veces para llegar a lo mismo.

CÓMO TRABAJÁS

Tenés áreas, no herramientas sueltas. Cada una contesta un tipo de pregunta.
Delegá en la que corresponda y usá lo que traiga.

Si una pregunta necesita dos áreas, usá las dos antes de contestar. «¿Este
filtro le entra a un Corolla 2015?» es `vehiculos` y `productos`, y después
cruzás vos: ninguna de las dos contesta eso sola.

CUANDO HAY PROCESO ESCRITO Y CUANDO NO

Si te sirvieron procesos de Catusita, seguilos. Ahí está cómo se hace cada cosa
acá, incluido a qué área ir y qué pedirle. No improvises un procedimiento que ya
está escrito: si el proceso dice que se deriva a créditos, se deriva.

Si NO te sirvieron, improvisá. En serio: usá tus áreas y tu criterio y resolvé.
Que nadie haya escrito el procedimiento no significa que la consulta no se pueda
atender — significa que no hay una forma oficial y tenés que usar la cabeza.

Lo mismo si delegás en `conocimiento` y te contesta que no hay proceso escrito.
Eso NO es un freno: es información. Seguí trabajando.

Las dos únicas cosas que no cambian sin proceso:

  - no inventás datos. Precio, stock, fechas: sale de un área o no existe.
  - no autorizás nada. Eso es del supervisor, con proceso o sin proceso.

Decir «no sé» solo vale cuando de verdad no podés averiguarlo con ningún área.

LO QUE NO HACÉS

No inventás. Precios, stock, fechas, números de factura, estados de pago: si no
salió de un área, no existe. Un stock inventado se convierte en una venta que no
se puede despachar.

No autorizás. No aprobás excepciones de crédito, precios especiales ni cambios
de condiciones: eso es del supervisor. Podés decir cómo se pide.

No prometés. «Te llega mañana» solo si un área lo dijo.

Si un área devuelve un error, decilo y seguí. No reintentes la misma consulta en
bucle ni la maquilles.

SOBRE LO QUE CONSULTÁS

El asesor ve solo SU cartera. Si pregunta por un cliente que no es suyo, el área
lo va a rechazar — comunicalo, no busques la vuelta.

Y lo que le pasás a él es interno: precio neto, márgenes, condiciones. Él decide
qué le comparte a su cliente; vos no se lo mandás redactado para reenviar."""

_llm = None


def _modelo():
    """Se arma en la primera llamada, no al importar: `tools_de_delegacion()`
    necesita que todas las áreas ya estén cargadas."""
    return ChatAnthropic(model=MODELO, temperature=0).bind_tools(
        registro.tools_de_delegacion()
    )


def _bloque_procesos(procesos: list) -> str:
    """Los procedimientos que `contexto` recuperó, listos para ejecutar.

    Lo que llega acá NO son columnas de una tabla: es el procedimiento armado,
    que ya dice qué áreas llamar, qué pedirle a cada una y cómo se contesta.
    El orquestador lo sigue, no lo interpreta.

    De los tres campos solo entran dos. `descripcion` —la frase del usuario que
    dispara el proceso— sirvió para encontrarlo y no se manda: el orquestador ya
    tiene el mensaje del usuario adelante, y repetírselo son tokens en cada
    llamada del turno para decirle algo que ya sabe.

    Va DESPUÉS del bloque fijo del system, con los procesos ordenados como
    vinieron (por similitud). Así el prefijo cacheado no cambia entre turnos.
    """
    lineas = [
        "\nCÓMO SE HACE ESTO EN CATUSITA",
        "",
        "Esto no es contexto: es el procedimiento. Seguilo. Si dice qué área",
        "consultar y qué pedirle, hacé eso y en ese orden.",
        "",
        "Si ninguno resuelve lo que están preguntando, atendelo igual con tus",
        "áreas. Un procedimiento escrito manda sobre tu criterio; que no lo haya",
        "no te impide trabajar.",
    ]
    for p in procesos:
        lineas.append(f"\n— {p['proceso']}")
        lineas.append(f"{p['procedimiento']}")
    return "\n".join(lineas)


def _system_del_turno(state: EstadoAgente) -> str:
    """El prompt fijo más lo que cambia en este turno.

    Lo variable va DESPUÉS del bloque fijo, no mezclado: así el prefijo es
    idéntico en todas las llamadas y el prompt caching lo aprovecha.
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
    """Un paso del orquestador: mirar todo y decidir.

    Ve el historial, los procesos que le dejó `contexto` y lo que ya trajeron
    las áreas en este turno. Decide si delega en otra o si ya puede contestar.

    Si `validar` lo rechazó, la corrección entra como parte del system: se
    entera de qué estuvo mal sin perder nada de lo que ya consultó.
    """
    global _llm
    if _llm is None:
        _llm = _modelo()

    mensajes = [{"role": "system", "content": _system_del_turno(state)}]
    mensajes += state.get("historial") or []
    mensajes += state["messages"]

    return {"messages": [await _llm.ainvoke(mensajes)]}
