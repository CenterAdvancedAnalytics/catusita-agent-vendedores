"""Memoria. Lo que el orquestador sabe antes de que le pregunten.

Corre antes que el orquestador y le deja servido todo lo que no es el mensaje
de ahora. Tres capas, que se agregan en este orden:

    1. MEMORIA CORTO PLAZO   la conversación en curso
       Redis, TTL 2h. Es lo único implementado hoy.
       -> migrar desde orchestrator/context.py::get_history

    2. MEMORIA LARGO PLAZO   qué sabemos de quien escribe
       Qué preguntó antes, qué compra, qué vehículos maneja, qué se le
       prometió. Sobrevive entre conversaciones.
       -> pgvector sobre Postgres

    3. RAG DE PROCESOS       cómo se hace esto en Catusita
       Los procedimientos: cómo se determina si una pieza calza, cuándo se
       deriva a créditos, qué se responde ante un reclamo. Reglas de negocio
       recuperables, no hardcodeadas en un prompt.
       -> pgvector; migraciones 004 y 008, aplicadas.

       ESTA CAPA CORRE EN TODOS LOS TURNOS. No es un plan B para cuando el
       orquestador se traba: es de donde saca cómo trabajar, siempre. Cada
       proceso trae su `procedimiento` completo: qué áreas consultar, qué
       pedirle a cada una y cómo se contesta.

       Lo que no está en la tabla no lo tiene escrito — y ahí atiende igual,
       con sus áreas y su criterio.

── Por qué esto está acá y no en un área ──────────────────────────────────────

Las áreas TRAEN DATOS. El orquestador RAZONA. Esa es la división.

Un ejemplo concreto: «¿este filtro le entra a mi Corolla 2015?» no es una
consulta, es una conclusión. `vehiculos` trae qué auto es, `productos` trae qué
hay en catálogo, y el orquestador cruza las dos cosas siguiendo el
procedimiento que le recupera el RAG. No hay un área de compatibilidad porque
no hay un dato de compatibilidad que buscar.

Por eso este nodo alimenta al orquestador y no a las áreas: es él quien
necesita saber cómo se razona.
"""
import asyncio
import json
import logging

from supervisores.agentes.conocimiento import servicio
from supervisores.plataforma_supervisores import redis as redis_mod
from supervisores.plataforma_supervisores.estado import EstadoAgente

TTL = 7200        # 2 horas de inactividad
MAX_MENSAJES = 20 # 10 turnos (user + assistant)

# Cuántos procesos se le pasan al orquestador por turno. Bajo a propósito: cada
# uno se paga en tokens en todas las llamadas del turno.
MAX_PROCESOS = 3

# El umbral NO se define acá. Vive en `servicio.UMBRAL` y esta puerta usa el
# mismo. Tenerlo en dos lados es la forma más fácil de que la búsqueda
# automática y la búsqueda a pedido dejen de coincidir sin que nadie lo note:
# el mismo mensaje encontraría proceso por un camino y no por el otro.
UMBRAL = servicio.UMBRAL


async def nodo_contexto(state: EstadoAgente) -> dict:
    """Arma la memoria y la deja en el estado.

    Ya no hace falta pasarle el multiagente: este módulo vive dentro de
    `supervisores/`, así que sus claves y sus tablas son las de supervisores y ninguna otra.

    Hoy arma la capa 1 y la 3. La 2 —memoria de largo plazo— se agrega acá.

    Las dos van en paralelo: son dos sistemas distintos (Redis y Postgres) y
    encadenarlas suma sus latencias al principio de CADA turno, antes de que el
    usuario vea nada.
    """
    historial, procesos = await asyncio.gather(
        _corto_plazo(state["conversacion"]),
        _procesos(_ultima_consulta(state)),
    )
    return {"historial": historial, "procesos": procesos}


def _ultima_consulta(state: EstadoAgente) -> str:
    """Lo que el usuario acaba de escribir, para buscarle el proceso.

    Se busca con el mensaje de ahora y no con la conversación entera: el
    historial arrastra temas viejos y el vector promedio termina no pareciéndose
    a ninguno. Es el mismo motivo por el que no se reformula la consulta.
    """
    for m in reversed(state.get("messages") or []):
        if getattr(m, "type", "") == "human":
            contenido = m.content
            if isinstance(contenido, str):
                return contenido
            # Turno con imagen: el texto va en el primer bloque.
            return " ".join(b.get("text", "") for b in contenido
                            if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _clave(conversacion: str) -> str:
    """`conv:supervisores:51999...`

    El multiagente va en la clave, no solo en el valor: aunque el router fallara
    y mandara un número al worker equivocado, no podría leer el historial que ese
    número tiene del otro lado. La clave vieja era `conversation:{numero}`, sin
    esa separación.
    """
    return f"conv:supervisores:{conversacion}"


async def _corto_plazo(conversacion: str) -> list:
    """Historial de la conversación. Redis, TTL 2h.

    Ante un fallo de Redis devuelve [] en vez de romper: perder el contexto de
    los últimos turnos degrada la respuesta, pero dejar sin contestar a un asesor
    es peor. Se loguea para que no pase inadvertido.
    """
    try:
        r = await redis_mod.get()
        crudo = await r.get(_clave(conversacion))
        return json.loads(crudo) if crudo else []
    except Exception as e:
        logging.warning(f"contexto: no se pudo leer el historial ({e})")
        return []


async def guardar(conversacion: str, rol: str, contenido: str) -> None:
    """Agrega un mensaje al historial y renueva el TTL.

    Lo llama el worker al cerrar el turno, no el grafo: el grafo lee memoria, no
    la escribe. Si escribiera, un reintento de validación duplicaría mensajes.
    """
    try:
        r = await redis_mod.get()
        k = _clave(conversacion)
        historial = await _corto_plazo(conversacion)
        historial.append({"role": rol, "content": contenido})
        await r.setex(k, TTL, json.dumps(historial[-MAX_MENSAJES:], ensure_ascii=False))
    except Exception as e:
        logging.warning(f"contexto: no se pudo guardar el mensaje ({e})")


async def olvidar(conversacion: str) -> None:
    """Borra el historial. El comando «reiniciar» del usuario."""
    try:
        r = await redis_mod.get()
        await r.delete(_clave(conversacion))
    except Exception as e:
        logging.warning(f"contexto: no se pudo borrar el historial ({e})")


async def _largo_plazo(conversacion: str, perfil: dict) -> dict:
    """Lo que sabemos de esta persona de conversaciones anteriores.

    Ojo con el alcance: para un vendedor esto incluye su cartera y su
    histórico; para un cliente, solo lo suyo. La memoria hereda las mismas
    fronteras que los datos — recordar no puede ser una vía para revelar.

    TODO: diseñar. Qué se guarda, cuánto vive, y quién puede leerlo.
    """
    raise NotImplementedError


async def _procesos(consulta: str) -> list:
    """Procedimientos de Catusita que aplican a lo que están preguntando.

    Corre SIEMPRE, antes del orquestador. Busca por similitud contra la
    `descripcion` de cada proceso —cómo lo pide el usuario, no cómo se llama
    adentro— y devuelve los que aplican, con su `procedimiento` completo.

    Devuelve el procedimiento, no la respuesta: el orquestador sigue teniendo
    que razonar y decidir. Lo que cambia es que ya no improvisa el cómo.

    Los procesos son POR MULTIAGENTE. Cómo se atiende un reclamo de un cliente y
    cómo se le contesta a un vendedor sobre lo mismo no son el mismo
    procedimiento. Y no hace falta filtrar: cada multiagente tiene su propia
    tabla, así que la frontera es el nombre de la tabla y no un WHERE.

    ── Si no encuentra nada, no pasa nada ─────────────────────────────────────

    Devuelve una lista vacía y el turno sigue igual. No se registra nada, no se
    avisa a nadie.

    Es deliberado. Este nodo corre en CADA turno, incluidos «hola», «gracias» y
    «ok dale», que no necesitan ningún procedimiento. Cualquier cosa que se
    dispare al no encontrar proceso se dispararía sobre todos esos mensajes.

    Sin proceso, el orquestador atiende con sus áreas y su criterio. El RAG de
    procesos es una guía, no un permiso: que nadie haya escrito el procedimiento
    no significa que la consulta no se pueda resolver.

    ── Cuántos procesos entran ────────────────────────────────────────────────

    Cada uno son tokens en TODAS las llamadas del turno. Meter 5 procesos largos
    reconstruye el problema que esto vino a resolver, solo que pagado en
    recuperación en vez de en prompt. Por eso se corta en MAX_PROCESOS=3, aunque
    `servicio.buscar` devuelva más: la otra puerta —la tool del área— sí los
    quiere todos, porque ahí ya se está buscando a propósito.

    ── Por qué esto no puede fallar hacia arriba ──────────────────────────────

    Corre en todos los turnos, así que un error acá deja al multiagente entero
    sin contestar. Si OpenAI o Postgres no responden, se sigue sin procesos: el
    orquestador contesta peor, pero contesta.
    """
    if not consulta:
        return []
    try:
        procesos = await servicio.buscar(consulta)
    except Exception as e:
        logging.error(f"[contexto] no se pudieron recuperar procesos: {e}")
        return []
    return procesos[:MAX_PROCESOS]
