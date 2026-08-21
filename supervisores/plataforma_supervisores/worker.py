"""Entrypoint de los contenedores de agentes.

    python -m supervisores.plataforma_supervisores.worker

Levanta turnos de UNA cola —la de su multiagente— y corre el grafo entero.

── Qué hace y qué no ──────────────────────────────────────────────────────────

NO despacha por área. El worker no sabe qué es `productos` ni `facturacion`: le
entrega el turno al grafo y el ORQUESTADOR decide a qué áreas va, con
`Command(goto=...)`, todo dentro de este mismo proceso.

Esa es la división:

    el worker         transporte. Saca de Redis, corre, devuelve.
    el orquestador    coordinación. A qué área, en qué orden, cuándo cortar.

Una versión anterior tenía una cola por área y el worker despachaba a cada una.
Era un error: convertía cada delegación en un viaje por Redis y obligaba a
ejecución durable con checkpoints, perdiendo justamente el `Command(goto=...)`
en proceso con el que el orquestador coordina. Ver plataforma/colas.py.

── Quien encola y quienes consumen ────────────────────────────────────────────

    catusita-recepcion      uvicorn recepcion.main:app   rutea por padrón y encola
    catusita-vendedores     worker vendedores            grafo de vendedores
    catusita-clientes       worker clientes              grafo de clientes
    catusita-supervisores   worker supervisores          grafo de supervisores

Los workers no tienen puerto: hablan con recepción solo por Redis. Fuera de esta
cola corren `panel` (solo lectura) y `yahuar` (placas), que no consumen de acá.

Un proceso por multiagente atiende todas sus conversaciones. En asyncio eso no
las serializa: un `await` de 60 s contra la consulta de placas no bloquea el
event loop.
"""
import asyncio
import importlib
import logging
import os
import time

from langchain_core.messages import AIMessage, HumanMessage

from supervisores import chat
from supervisores.plataforma_supervisores import colas
from supervisores.plataforma_supervisores import db
from supervisores.plataforma_supervisores.nodos import contexto
from supervisores.plataforma_supervisores import padron
from supervisores.plataforma_supervisores import redis as redis_mod
from supervisores.plataforma_supervisores.contrato import (
    Resultado, VersionIncompatible, desempacar, empacar,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# El BRPOP se despierta cada tanto para poder atender un apagado. No es polling.
BLOQUEO = 5

MULTIAGENTE = "supervisores"

# Cada delegación son dos pasos (orquestador -> área -> orquestador).
# Es el freno para que un loop de delegaciones no queme la API key.
RECURSION_LIMITE = int(os.getenv("LANGGRAPH_RECURSION_LIMIT", "20"))


def cargar_grafo():
    """El grafo compilado del multiagente.

    Se importa acá y no arriba para que el worker de vendedores no cargue el
    módulo de clientes ni por accidente de import.
    """
    mod = importlib.import_module("supervisores.grafo")
    grafo = getattr(mod, "grafo", None)
    if grafo is None:
        raise RuntimeError(
            "supervisores/grafo.py no expone `grafo` compilado. "
            f"Falta descomentar `grafo = construir().compile()`."
        )
    fallos = mod.verificar_topologia(grafo)
    if fallos:
        for f in fallos:
            logging.error(f"[topología] {f}")
        raise RuntimeError(f"grafo de supervisores mal armado: {len(fallos)} violación(es)")
    return grafo


async def correr() -> None:
    # Antes de escuchar nada: que el mapa de colas cierre. Una cola sin
    # consumidor acumula turnos en silencio, que es la peor forma de fallar.
    fallos = colas.problemas()
    if fallos:
        for f in fallos:
            logging.error(f"[colas] {f}")
        raise RuntimeError(f"mapa de colas inconsistente: {len(fallos)} problema(s)")

    cola = colas.COLA
    grafo = cargar_grafo()
    registro = importlib.import_module("supervisores.registro")

    # Se distingue lo DESCUBIERTO de lo DELEGABLE. Un área sin subgrafo
    # compilado existe en el disco y el orquestador no la ve: contar las dos
    # cosas juntas hacía que el log dijera "5 áreas" cuando solo 2 atienden.
    areas = registro.mapa()
    delegables = set(registro.nodos())
    logging.info(
        f"worker de supervisores · cola={cola} · {len(delegables)} de {len(areas)} áreas atienden"
    )
    for nombre, (modelo, tools) in sorted(areas.items()):
        marca = "" if nombre in delegables else "   (sin subgrafo, no se delega)"
        logging.info(f"    {nombre:16} {modelo:28} {len(tools)} tools{marca}")

    # El padrón, antes de escuchar: si no publico, el router no sabe que estos
    # números son míos y sus dueños entran al multiagente equivocado.
    await padron.publicar()
    asyncio.create_task(_republicar())

    r = await redis_mod.get()
    while True:
        # Un traspié de Redis no puede matar al worker. Sin este try, un
        # TimeoutError tumba el proceso, Railway lo reinicia, vuelve a caerse:
        # un crash-loop que se ve como "el agente no responde" y que en los
        # logs aparece como un traceback de red, no como un problema del agente.
        try:
            item = await r.brpop([cola], timeout=BLOQUEO)
        except Exception as e:
            logging.error(f"[cola] Redis no respondio: {e}")
            await asyncio.sleep(2)
            continue
        if item is None:
            continue
        _, crudo = item

        try:
            turno = desempacar(crudo)
        except VersionIncompatible as e:
            # Reintentar no arregla un desajuste de versiones entre despliegues.
            logging.error(f"contrato incompatible: {e}")
            await r.rpush(f"muertos:{cola}", crudo)
            continue
        except Exception as e:
            logging.error(f"sobre ilegible: {e}")
            await r.rpush(f"muertos:{cola}", crudo)
            continue

        t0 = time.time()
        try:
            res = await _correr_turno(grafo, turno)
        except Exception as e:
            logging.exception(f"turno {turno.conversacion} falló: {e}")
            # El error vuelve igual. El usuario merece un "no pude"; el silencio
            # es peor que un error.
            res = Resultado(
                conversacion=turno.conversacion, multiagente=MULTIAGENTE,
                ok=False, error=str(e),
                responder_a=turno.responder_a, lock=turno.lock,
            )
        res.duracion_ms = int((time.time() - t0) * 1000)
        await r.lpush(colas.COLA_RESPUESTAS, empacar(res))
        logging.info(f"turno {turno.conversacion} en {res.duracion_ms} ms ok={res.ok}")


async def _republicar() -> None:
    """Republica el padrón cada tanto. Sin esto, un alta no tendría efecto hasta
    el próximo deploy: el número entraría al multiagente equivocado sin que nadie
    entienda por qué."""
    while True:
        await asyncio.sleep(padron.CADA_SEGUNDOS)
        try:
            await padron.publicar()
        except Exception as e:
            logging.error(f"[padron] republicación falló: {e}")


async def _perfil(numero: str) -> dict:
    """Quién es este número, según MI roster."""
    pool = await db.get()
    async with pool.acquire() as c:
        f = await c.fetchrow(
            """SELECT supervisor_id, nombre FROM supervisores
                WHERE activo AND whatsapp = $1""", numero)
    if not f:
        return {"tipo": "supervisor", "autenticado": False, "numero": numero}
    return {"tipo": "supervisor", "autenticado": True, "numero": numero,
            "supervisor_id": f["supervisor_id"], "nombre": f["nombre"]}


# Lo que la API de Anthropic acepta como imagen. Un mimetype fuera de esta lista
# no se adjunta: mandarlo igual haría fallar el turno completo por una foto.
IMAGENES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def _mensaje_del_usuario(turno):
    """El turno como lo ve el modelo: texto, y las fotos que mandó el usuario.

    Recepción ya bajó los bytes al cerrar el turno (la URL de WAHA es efímera y
    a esta altura probablemente ya caducó), así que acá solo hay que armarlos en
    bloques de contenido.

    Sin esto, un asesor que fotografía la pieza que tiene en la mano —que es
    justo lo que hace cuando no sabe el código— recibe una respuesta escrita
    como si no hubiera mandado nada.

    Si no hay imágenes válidas, el contenido queda como string. No es cosmético:
    un turno de solo texto no debería pagar el formato multimodal.
    """
    adjuntos = [
        {"type": "image", "source": {
            "type": "base64",
            "media_type": (m.get("mimetype") or "").split(";")[0].strip(),
            "data": m["bytes_b64"],
        }}
        for m in (turno.media or [])
        if m.get("bytes_b64")
        and (m.get("mimetype") or "").split(";")[0].strip() in IMAGENES
    ]
    if not adjuntos:
        # ── Un mensaje sin texto NO puede llegar vacío al modelo ──────────────
        #
        # Anthropic rechaza la conversación entera:
        #
        #     messages.2: user messages must have non-empty content
        #
        # y el turno muere en 158 ms sin que nada indique que la causa fue un
        # sticker. Pasa seguido: audios, stickers, fotos sin epígrafe, o una
        # respuesta de un solo emoji.
        #
        # Se reemplaza por una descripción de lo que llegó, para que el
        # orquestador pueda contestar algo con sentido en vez de reventar.
        if turno.texto:
            return HumanMessage(content=turno.texto)

        cuantos = len(turno.media or [])
        return HumanMessage(content=(
            f"(el usuario mandó {cuantos} archivo(s) que no puedo ver, sin texto)"
            if cuantos else
            "(el usuario mandó un mensaje sin texto: puede ser un audio, un "
            "sticker o una nota de voz)"
        ))

    descartados = len(turno.media or []) - len(adjuntos)
    if descartados:
        logging.warning(f"[media] {descartados} adjunto(s) del usuario sin formato usable")

    # El texto va PRIMERO: es lo que le da sentido a la foto («¿este me sirve
    # para un Corolla?»). Al revés el modelo describe la imagen y recién después
    # se entera de qué le preguntaron.
    return HumanMessage(content=[{"type": "text", "text": turno.texto or " "}] + adjuntos)


async def _persistir(turno, respuesta: str, tools: list, perfil: dict,
                     res_ms: int | None) -> None:
    """Escribe el par usuario/asistente en la tabla del multiagente.

    Nunca lanza. Si Postgres está caído, se pierde el registro de ese turno y se
    anota en el log — pero el asesor ya recibió su respuesta y eso es lo que
    importa. Que la telemetría pueda tumbar una conversación sería el peor
    intercambio posible.
    """
    try:
        vid = perfil.get("vendedor_id")
        nombre = perfil.get("nombre")
        if turno.texto:
            await chat.guardar(turno.conversacion, "user", turno.texto,
                               vendedor_id=vid, vendedor_nombre=nombre,
                               tipo="imagen" if turno.media else "texto")
        if respuesta:
            await chat.guardar(turno.conversacion, "assistant", respuesta,
                               vendedor_id=vid, vendedor_nombre=nombre,
                               tools=tools or [], latencia_ms=res_ms)
    except Exception as e:
        logging.error(f"[chat] no se pudo persistir el turno {turno.conversacion}: {e}")


async def _correr_turno(grafo, turno) -> Resultado:
    """Corre el grafo entero para un turno y arma la respuesta.

    ── Qué entra al estado ────────────────────────────────────────────────────

    El mensaje del usuario como HumanMessage, y nada más en `messages`. El
    historial va aparte (`historial`) porque el orquestador lo antepone al
    system, mientras que `messages` es lo que pasa DENTRO de este turno — lo que
    las áreas van escribiendo mientras trabajan.

    Mezclarlos haría que `validar` viera los turnos viejos como si fueran parte
    de este y contara tools que se usaron ayer.

    ── El recursion_limit ─────────────────────────────────────────────────────

    Cada delegación son dos pasos (orquestador -> área -> orquestador). Con seis
    áreas y algún reintento de validación, 20 alcanza de sobra. Es el freno para
    que un loop de delegaciones no queme la API key.
    """
    arranque = time.time()
    numero = turno.conversacion
    perfil = await _perfil(numero)

    estado = {
        "messages": [_mensaje_del_usuario(turno)],
        "conversacion": numero,
        "perfil": perfil,
        "historial": [],
        "resuelto": {},
        "media_pendiente": [],
        "validacion": {},
        "intentos_validacion": 0,
    }

    final = await grafo.ainvoke(estado, {"recursion_limit": RECURSION_LIMITE})

    mensajes = final.get("messages") or []
    texto = ""
    for m in reversed(mensajes):
        if isinstance(m, AIMessage) and m.content:
            texto = m.content if isinstance(m.content, str) else str(m.content)
            break

    tools = [tc.get("name", "") for m in mensajes if isinstance(m, AIMessage)
             for tc in (getattr(m, "tool_calls", None) or [])]

    await contexto.guardar(numero, "user", turno.texto)
    if texto:
        await contexto.guardar(numero, "assistant", texto)

    # ── Y en Postgres, que es lo que sobrevive ───────────────────────────────
    #
    # Redis es la memoria del agente: diez mensajes con TTL de dos horas. Sirve
    # para que la conversación tenga hilo, no para tener registro.
    #
    # Sin esto, `chat_messages_supervisores` se quedó sin una sola fila desde el
    # corte: el panel dejó de ver conversaciones y —lo que más duele— se cortó
    # la única fuente para saber qué piden los asesores. Los 13 procesos del RAG
    # salieron justamente de leer esos chats.
    #
    # Va al final y no puede tumbar el turno: el asesor ya tiene su respuesta y
    # perder telemetría es mucho más barato que perder la contestación.
    await _persistir(turno, texto, tools, perfil,
                     res_ms=int((time.time() - arranque) * 1000))

    return Resultado(
        conversacion=numero,
        multiagente=MULTIAGENTE,
        ok=bool(texto),
        texto=texto,
        media=final.get("media_pendiente") or [],
        responder_a=turno.responder_a,
        lock=turno.lock,
        tools=tools,
        error="" if texto else "el grafo terminó sin respuesta",
    )


def main() -> None:
    asyncio.run(correr())


if __name__ == "__main__":
    main()
