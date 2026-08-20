"""
Webhook que recibe los eventos `whatsapp.message.received` de Kapso
(WhatsApp Cloud API oficial).

El payload de Kapso (sandbox) llega en formato BATCH: los campos
relevantes (message, phone_number_id, conversation) vienen dentro de
`data: [ {...}, ... ]`, no en la raíz. Ejemplo real:

{
  "type": "whatsapp.message.received",
  "batch": true,
  "data": [
    {
      "message": {
        "from": "51940351180",
        "text":  { "body": "Hola" },
        "kapso": { "content": "Hola", "direction": "inbound" },
        "type": "text"
      },
      "conversation": { "phone_number_id": "597907523413541", ... },
      "is_new_conversation": true,
      "phone_number_id": "597907523413541"
    }
  ],
  "batch_info": { ... }
}

Headers:
  X-Webhook-Event:     whatsapp.message.received
  X-Webhook-Signature: <HMAC-SHA256 hex del body crudo con el secret>
  X-Idempotency-Key:   <uuid>
"""
import os
import uuid
import json
import asyncio
import logging
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, HTTPException

from shared import auth
from shared import kapso as kapso_mod
from shared import waha as waha_mod
from shared import yahuar as yahuar_mod
from orchestrator.graph import run_agent_graph_full
from orchestrator import context
from db import models
from vendedores import chat as vendedores_chat

router_wh = APIRouter()


def _session_id(numero: str) -> str:
    """Conversación = una por vendedor por día (fecha en hora de Lima)."""
    fecha_lima = datetime.now(ZoneInfo("America/Lima")).date().isoformat()
    return hashlib.md5(f"{numero}|{fecha_lima}".encode()).hexdigest()

# Referencias fuertes a las tareas en segundo plano para que el GC no las
# recoja a media ejecución (asyncio solo guarda weakrefs a las tasks).
_tareas_bg: set = set()

USE_AUTH_MOCK  = os.getenv("USE_AUTH_MOCK", "true").lower() == "true"
PERSIST_TO_DB  = os.getenv("PERSIST_TO_DB", "false").lower() == "true"
# WHATSAPP_PROVIDER=waha  →  usa WAHA para enviar mensajes
# WHATSAPP_PROVIDER=kapso →  usa Kapso (default)
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "kapso").lower()

KAPSO_PHONE_NUMBER_ID = os.getenv("KAPSO_PHONE_NUMBER_ID", "")

# Cuando agreguemos un número Kapso aparte para clientes, ponemos su
# phone_number_id acá y enrutamos. Por ahora solo tenemos el de vendedores.
KAPSO_PHONE_NUMBER_ID_CLIENTES = os.getenv("KAPSO_PHONE_NUMBER_ID_CLIENTES", "")



def _resolver_agente_tipo(phone_number_id: str) -> str:
    """
    Decide si el mensaje va al agente de vendedores o de clientes
    según a qué phone_number_id de Kapso llegó.
    """
    if (
        KAPSO_PHONE_NUMBER_ID_CLIENTES
        and str(phone_number_id) == str(KAPSO_PHONE_NUMBER_ID_CLIENTES)
    ):
        return "cliente"
    # Por defecto: vendedores (es el primer número conectado).
    return "vendedor"


async def _procesar_item(item: dict) -> dict:
    """
    Procesa un único mensaje entrante de Kapso (un elemento del batch).
    """
    message = item.get("message") or {}
    # phone_number_id puede venir en la raíz del item o dentro de conversation
    phone_number_id = str(
        item.get("phone_number_id")
        or (item.get("conversation") or {}).get("phone_number_id")
        or ""
    )

    # ----------------------------------------------------------------------
    # Filtrar por phone_number_id esperado
    # ----------------------------------------------------------------------
    permitidos = {
        x for x in (KAPSO_PHONE_NUMBER_ID, KAPSO_PHONE_NUMBER_ID_CLIENTES) if x
    }
    if permitidos and phone_number_id not in {str(x) for x in permitidos}:
        print(
            f"[WEBHOOK] ignorado: phone_number_id={phone_number_id!r} "
            f"no está en {permitidos}"
        )
        return {"status": "ignored", "reason": "unknown phone_number_id"}

    # ----------------------------------------------------------------------
    # Extraer número del remitente y texto
    # ----------------------------------------------------------------------
    numero = (message.get("from") or "").lstrip("+").split("@")[0]

    # Texto: primero text.body; si no, kapso.content (incluye debouncing/agrupación)
    texto = (
        (message.get("text") or {}).get("body")
        or (message.get("kapso") or {}).get("content")
    )

    print(f"[WEBHOOK] numero={numero!r} texto={texto!r} phone_number_id={phone_number_id}")

    if not texto:
        print("[WEBHOOK] ignorado: mensaje sin texto (imagen/audio/sticker)")
        return {"status": "ignored", "reason": "no text"}

    if not numero:
        print("[WEBHOOK] ignorado: sin numero (message.from vacío)")
        return {"status": "ignored", "reason": "no from"}

    # ----------------------------------------------------------------------
    # Resolver agente tipo + autenticación
    # ----------------------------------------------------------------------
    agente_tipo = _resolver_agente_tipo(phone_number_id)
    print(f"[WEBHOOK] agente_tipo={agente_tipo}")

    perfil = await auth.get_user_profile(numero, agente_tipo)
    print(f"[WEBHOOK] perfil: {perfil}")

    if not perfil.get("autenticado"):
        print("[WEBHOOK] perfil no autenticado — pidiendo identificación")
        try:
            await kapso_mod.kapso.send_message(
                numero, phone_number_id, perfil.get("mensaje", "")
            )
        except Exception as e:
            logging.error(f"Error enviando msg de auth: {e}", exc_info=True)
            print(f"[WEBHOOK] ERROR enviando auth_required: {e}")
        return {"status": "auth_required"}

    # ----------------------------------------------------------------------
    # Comando de reset
    # ----------------------------------------------------------------------
    if texto.strip().lower() in ("reiniciar", "reset", "nueva conversacion"):
        print("[WEBHOOK] comando de reinicio")
        await context.clear_history(numero)
        await kapso_mod.kapso.send_message(
            numero, phone_number_id,
            "Conversación reiniciada. ¿En qué puedo ayudarte?",
        )
        return {"status": "ok"}

    # ----------------------------------------------------------------------
    # Ejecutar el agente
    # ----------------------------------------------------------------------
    # id de correlación para los logs; la conversación se agrupa por session_id
    conversation_id = _session_id(numero)
    # Datos de envío para que las tools puedan encolar media (ej. foto de placa)
    perfil["numero"] = numero
    perfil["phone_number_id"] = phone_number_id

    historial = await context.get_history(numero)
    print(
        f"[WEBHOOK] procesando con el grafo: "
        f"conversation_id={conversation_id} historial={len(historial)} msgs"
    )

    respuesta, media_list, _tools = await run_agent_graph_full(texto, perfil, historial)

    print(f"[WEBHOOK] respuesta (LangGraph): {respuesta!r}")

    # ----------------------------------------------------------------------
    # Persistir y enviar
    # ----------------------------------------------------------------------
    await context.save_message(numero, "user", texto)
    await context.save_message(numero, "assistant", respuesta)

    try:
        envio = await kapso_mod.kapso.send_message(numero, phone_number_id, respuesta)
        print(f"[WEBHOOK] enviado a WhatsApp -> {envio}")
    except Exception as e:
        logging.error(f"Error enviando mensaje por Kapso: {e}", exc_info=True)
        print(f"[WEBHOOK] ERROR enviando a WhatsApp: {e}")

    # ----------------------------------------------------------------------
    # Enviar media encolada por las tools (ej. foto de la tarjeta vehicular)
    # ----------------------------------------------------------------------
    for media in media_list:
        try:
            if media.get("documento_base64"):
                await kapso_mod.kapso.send_document_base64(
                    numero,
                    phone_number_id,
                    media["documento_base64"],
                    filename=media.get("filename", "documento.pdf"),
                    caption=media.get("caption", ""),
                    mime=media.get("mime", "application/pdf"),
                )
                print(f"[WEBHOOK] documento enviado a WhatsApp ({media.get('filename')})")
            else:
                await kapso_mod.kapso.send_image_base64(
                    numero,
                    phone_number_id,
                    media["imagen_base64"],
                    caption=media.get("caption", ""),
                    filename=media.get("filename", "imagen.png"),
                )
                print(f"[WEBHOOK] foto enviada a WhatsApp ({media.get('filename')})")
        except Exception as e:
            logging.error(f"Error enviando media por Kapso: {e}", exc_info=True)
            print(f"[WEBHOOK] ERROR enviando media: {e}")

    return {"status": "ok"}


@router_wh.post("/whatsapp")
async def webhook_whatsapp(request: Request):
    # ----------------------------------------------------------------------
    # [0] Leer el body crudo para verificar la firma antes de parsearlo
    # ----------------------------------------------------------------------
    raw_body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    event = request.headers.get("X-Webhook-Event", "")
    idempotency_key = request.headers.get("X-Idempotency-Key", "")

    print(
        f"[WEBHOOK] event={event!r} idempotency_key={idempotency_key!r} "
        f"sig={(signature[:16] + '...') if signature else 'MISSING'}"
    )

    if not kapso_mod.verify_signature(raw_body, signature):
        print("[WEBHOOK] firma inválida — rechazado")
        raise HTTPException(status_code=401, detail="invalid signature")

    # ----------------------------------------------------------------------
    # [1] Parsear el JSON ya verificado
    # ----------------------------------------------------------------------
    try:
        data = json.loads(raw_body)
    except Exception as e:
        print(f"[WEBHOOK] body no es JSON válido: {e}")
        raise HTTPException(status_code=400, detail="invalid json")

    try:
        print(f"[WEBHOOK] body recibido: {json.dumps(data, ensure_ascii=False)}")
    except Exception:
        print(f"[WEBHOOK] body recibido (raw): {data}")

    # ----------------------------------------------------------------------
    # [2] Sólo nos interesan los `whatsapp.message.received`.
    #     El tipo viene en el header X-Webhook-Event y/o en data["type"].
    #     Otros eventos (sent / delivered / read / failed / conversation.*)
    #     los reconocemos con 200 OK y los ignoramos.
    # ----------------------------------------------------------------------
    tipo_evento = event or data.get("type", "")
    if tipo_evento != "whatsapp.message.received":
        print(f"[WEBHOOK] ignorado: evento {tipo_evento!r} no relevante")
        return {"status": "ignored", "reason": f"event {tipo_evento}"}

    # ----------------------------------------------------------------------
    # [2.5] IDEMPOTENCIA: si Kapso reenvía el mismo webhook (porque tardamos
    #       en responderle el 200), NO lo reprocesamos. Sin esto, una consulta
    #       lenta (ej. la consulta de placa) hace que Kapso reintente y cada reintento
    #       dispare otra ejecución del agente → decenas de respuestas repetidas.
    # ----------------------------------------------------------------------
    if idempotency_key and await context.ya_procesado(idempotency_key):
        print(f"[WEBHOOK] idempotente: {idempotency_key!r} ya procesado, ignorando")
        return {"status": "ignored", "reason": "duplicate idempotency_key"}

    # ----------------------------------------------------------------------
    # [3] El payload puede venir en batch (data: [ {...}, ... ]) o como un
    #     único objeto en la raíz. Normalizamos a una lista.
    # ----------------------------------------------------------------------
    items = data.get("data")
    if not isinstance(items, list):
        items = [data]

    # ----------------------------------------------------------------------
    # [4] ACK INMEDIATO + procesamiento en segundo plano.
    #
    #     Kapso espera el 200 OK en pocos segundos; si tardamos (porque el
    #     agente llama una tool lenta, ej. la consulta de placa), Kapso da por fallida la
    #     entrega y REENVÍA el webhook → cada reenvío genera otra respuesta
    #     al usuario (el spam que veíamos). La solución correcta es responder
    #     200 al instante y hacer el trabajo del agente aparte, de modo que
    #     Kapso nunca tenga motivo para reintentar.
    # ----------------------------------------------------------------------
    tarea = asyncio.create_task(_procesar_items_bg(items))
    _tareas_bg.add(tarea)
    tarea.add_done_callback(_tareas_bg.discard)

    return {"status": "accepted"}


async def _procesar_items_bg(items: list) -> None:
    """Procesa los mensajes del batch fuera del ciclo request/response.

    Se ejecuta después de haberle devuelto el 200 a Kapso, así una tool lenta
    nunca provoca reintentos del webhook. Cada item se aísla en su try/except
    para que un error en uno no tumbe a los demás.
    """
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            await _procesar_item(item)
        except Exception as e:
            logging.error(f"Error procesando item del webhook: {e}", exc_info=True)
            print(f"[WEBHOOK] ERROR procesando item (bg): {e}")


# ---------------------------------------------------------------------------
# Selector de proveedor de mensajería
# ---------------------------------------------------------------------------

def _messenger():
    """Devuelve el cliente activo según WHATSAPP_PROVIDER."""
    if WHATSAPP_PROVIDER == "waha":
        return waha_mod.waha
    return kapso_mod.kapso


# ---------------------------------------------------------------------------
# Webhook de WAHA
# ---------------------------------------------------------------------------

async def _procesar_item_waha(data: dict) -> dict:
    """
    Normaliza el payload de WAHA y lo procesa con la misma lógica interna.

    Formato WAHA (versión 2026.x):
    {
      "event": "message",
      "session": "default",
      "payload": {
        "id": "...",
        "from": "51940351180@c.us",
        "fromMe": false,
        "body": "Hola",
        "type": "chat"
      }
    }
    """
    event   = data.get("event", "")
    payload = data.get("payload") or {}

    # Solo mensajes entrantes de texto
    if event != "message":
        print(f"[WAHA] ignorado: evento {event!r}")
        return {"status": "ignored", "reason": f"event {event}"}

    if payload.get("fromMe"):
        print("[WAHA] ignorado: fromMe=true")
        return {"status": "ignored", "reason": "fromMe"}

    from_field = payload.get("from") or ""
    numero     = from_field.split("@")[0].lstrip("+")
    texto      = payload.get("body") or ""
    tipo       = payload.get("type", "")

    if not from_field:
        print("[WAHA] ignorado: no from", flush=True)
        return {"status": "ignored", "reason": "no from"}

    # ── Guardrail Yahuar — ANTES del filtro de tipo para capturar sus imágenes ─
    # Yahuar tiene un LID interno distinto a su teléfono. Acumulamos todos los
    # identificadores conocidos: número de teléfono, LID del env var y LID
    # aprendido automáticamente la primera vez que respondió.
    yahuar_num      = yahuar_mod.YAHUAR_NUMBER          # "51977504279"
    yahuar_lid_env  = os.getenv("YAHUAR_LID", "")      # "250032309711048" (manual)
    yahuar_lid_redis = await yahuar_mod.get_yahuar_lid()  # aprendido automáticamente

    yahuar_ids = {yahuar_num}
    if yahuar_lid_env:
        yahuar_ids.add(yahuar_lid_env)
    if yahuar_lid_redis:
        yahuar_ids.add(yahuar_lid_redis)

    es_yahuar = numero in yahuar_ids

    print(f"[WAHA] from={from_field!r} numero={numero!r} tipo={tipo!r} texto={texto[:60]!r}", flush=True)

    if es_yahuar:
        # 1. ¿Hay un relay activo? (Yahuar mandó texto y ahora manda la foto)
        relay_info = await yahuar_mod.get_relay_dest()
        if relay_info:
            destino_r, placa_r = relay_info if isinstance(relay_info, tuple) else (relay_info, "")
            print(f"[YAHUAR] follow-up tipo={tipo!r} hasMedia={payload.get('hasMedia')} → relay a {destino_r!r}", flush=True)
            return await _reenviar_yahuar(payload, destino_r, placa_r)

        # 2. ¿Hay consulta pendiente? Primera respuesta de Yahuar.
        pendiente = await yahuar_mod.get_pendiente()
        if pendiente:
            destino = pendiente["from_field"]
            placa   = pendiente.get("placa", "")
            print(f"[YAHUAR] primera respuesta tipo={tipo!r} → relay a {destino!r} placa={placa!r}", flush=True)
            await yahuar_mod.open_relay(destino, placa)
            return await _reenviar_yahuar(payload, destino, placa)

        print(f"[YAHUAR] ignorado: sin consulta pendiente ni relay activo", flush=True)
        return {"status": "ignored", "reason": "yahuar no pending"}

    # ── Auto-aprendizaje: detectar Yahuar desde LID desconocido ─────────────
    pendiente_peek = await yahuar_mod.peek_pendiente()
    if pendiente_peek and from_field != pendiente_peek.get("from_field"):
        print(f"[YAHUAR] LID desconocido detectado: {numero!r} — guardando", flush=True)
        await yahuar_mod.save_yahuar_lid(numero)
        return await _manejar_respuesta_yahuar(payload)

    # ── Filtro de tipo (solo para usuarios normales, no para Yahuar) ─────────
    if tipo not in ("chat", "text", ""):
        print(f"[WAHA] ignorado: tipo {tipo!r} (no es texto del usuario)")
        return {"status": "ignored", "reason": f"type {tipo}"}

    if not texto:
        print("[WAHA] ignorado: no texto", flush=True)
        return {"status": "ignored", "reason": "no text"}

    # ── Resolver LID → teléfono para el login ────────────────────────────────
    # WAHA entrega el LID interno de Meta ('<lid>@lid') para varios contactos, pero
    # el registro de asesores es por número de teléfono. Lo traducimos vía WAHA
    # antes de autenticar. Las respuestas siguen yendo a from_field (el LID).
    if "@lid" in from_field:
        tel = await waha_mod.resolve_lid_to_phone(from_field)
        if tel:
            print(f"[WAHA] LID {numero!r} → tel {tel!r}", flush=True)
            numero = tel

    # Reutilizamos toda la lógica del agente — phone_number_id vacío porque
    # WAHA no tiene ese concepto (usamos session en su lugar).
    agente_tipo = "vendedor"  # por ahora solo el canal de vendedores
    perfil = await auth.get_user_profile(numero, agente_tipo)
    print(f"[WAHA] perfil: {perfil}", flush=True)

    if not perfil.get("autenticado"):
        await _messenger().send_message(from_field, "", perfil.get("mensaje", ""))
        return {"status": "auth_required"}

    if texto.strip().lower() in ("reiniciar", "reset", "nueva conversacion"):
        await context.clear_history(numero)
        await _messenger().send_message(from_field, "", "Conversación reiniciada. ¿En qué puedo ayudarte?")
        return {"status": "ok"}

    # id de correlación para los logs; la conversación se agrupa por session_id
    conversation_id = _session_id(numero)
    perfil["numero"]          = numero
    perfil["from_field"]      = from_field   # necesario para Yahuar relay
    perfil["phone_number_id"] = ""

    historial = await context.get_history(numero)

    sess     = _session_id(numero)
    vend_id  = perfil.get("vendedor_id")
    vend_nom = perfil.get("nombre")

    # El mensaje del usuario se guarda ANTES de correr el agente, no después.
    # Dos motivos:
    #   1. Si el agente revienta, antes el mensaje se perdía de la BD y quedaba
    #      una conversación con la pregunta faltante.
    #   2. `solicitud_proceso_nuevo_vendedores.mensaje_id` necesita este id, y el
    #      agente lo va a abrir mientras corre.
    # Best-effort: no poder registrar no es motivo para dejar sin contestar.
    mensaje_id = None
    try:
        mensaje_id = await vendedores_chat.guardar(
            numero, "user", texto, vendedor_id=vend_id, vendedor_nombre=vend_nom,
            session_id=sess, tipo="texto",
        )
    except Exception as e:
        logging.error(f"Error guardando el mensaje del usuario: {e}")

    respuesta, media_list, tools_usadas = await run_agent_graph_full(texto, perfil, historial)

    await context.save_message(numero, "user", texto)
    await context.save_message(numero, "assistant", respuesta)

    try:
        await vendedores_chat.guardar(
            numero, "assistant", respuesta, vendedor_id=vend_id, vendedor_nombre=vend_nom,
            session_id=sess, tipo="texto", tools=tools_usadas,
        )
    except Exception as e:
        logging.error(f"Error guardando la respuesta: {e}")


    try:
        await _messenger().send_message(from_field, "", respuesta)
        print(f"[WAHA] respuesta enviada a {from_field!r}")
    except Exception as e:
        logging.error(f"Error enviando por WAHA: {e}", exc_info=True)
        print(f"[WAHA] ERROR enviando: {e}")

    for media in media_list:
        try:
            if media.get("documento_base64"):
                await _messenger().send_document_base64(
                    from_field, "",
                    media["documento_base64"],
                    filename=media.get("filename", "documento.pdf"),
                    caption=media.get("caption", ""),
                    mime=media.get("mime", "application/pdf"),
                )
            else:
                await _messenger().send_image_base64(
                    from_field, "",
                    media["imagen_base64"],
                    caption=media.get("caption", ""),
                    filename=media.get("filename", "imagen.png"),
                )
        except Exception as e:
            print(f"[WAHA] ERROR enviando media: {e}")

    return {"status": "ok"}


_ERRORES_YAHUAR = (
    "creo que escribiste", "no encontré", "no pude", "no existe",
    "inválida", "incorrecta", "no reconozco", "no tengo datos",
)

# Frases que indican que Yahuar saluda o pide aclaración (no es una respuesta final).
# Ampliado (incidencia #21): antes se colaba su saludo ("¿qué información buscas?")
# y confundía al vendedor. Estos mensajes NO se reenvían; se auto-responde a Yahuar.
_ACLARACION_YAHUAR = (
    "necesito entender", "¿es un", "cuéntame más", "qué consulta",
    "necesito saber", "puedes aclarar", "me puedes decir",
    "qué tipo de", "para poder ayudarte",
    "qué información buscas", "que informacion buscas", "veo que me pasas",
    "soy yahuar", "en qué te puedo ayudar", "en que te puedo ayudar",
    "¿qué deseas", "puedo ayudarte",
)

_INSTRUCCION_PLACA = (
    "Esta es la foto de una Tarjeta de Identificación Vehicular peruana. "
    "Extrae y devuelve EN TEXTO, como lista clave: valor, todos los datos legibles: "
    "placa, marca, modelo, año de fabricación, color, número de serie/VIN, número de motor, "
    "categoría, combustible y propietario(s) si aparecen. "
    "Solo los datos, sin comentarios ni explicaciones."
)


async def _reenviar_yahuar(payload: dict, destino: str, placa: str) -> dict:
    """
    Acumula TODOS los mensajes de Yahuar (3-4 en total) con debounce de 5s.
    Solo cuando Yahuar deja de escribir, procesa todo junto y envía al usuario.
    """
    from shared import llm as llm_mod

    texto_resp = payload.get("body") or ""
    texto_lower = texto_resp.lower()

    # ¿Hay un subagente bloqueante esperando esta placa? Si sí, el resultado final
    # se GUARDA en Redis (no se empuja al vendedor): el subagente lo recoge.
    bloqueante = await yahuar_mod.es_bloqueante(placa)

    # ── Aclaración de Yahuar: responder automáticamente sin acumular ──────────
    if any(p in texto_lower for p in _ACLARACION_YAHUAR):
        print(f"[YAHUAR] pide aclaración → respondiendo 'Placa vehicular'", flush=True)
        try:
            await _messenger().send_message(yahuar_mod.YAHUAR_CHAT_ID, "", "Placa vehicular")
        except Exception as e:
            print(f"[YAHUAR] ERROR respondiendo aclaración: {e}", flush=True)
        return {"status": "ok"}

    # ── Acumular este mensaje + actualizar timestamp ───────────────────────────
    ts_mio = await yahuar_mod.acumular_mensaje(payload)
    print(f"[YAHUAR] acumulado msg ts={ts_mio:.2f} texto={texto_resp[:60]!r}", flush=True)

    # ── Debounce: esperar 5s de silencio antes de procesar ───────────────────
    await asyncio.sleep(yahuar_mod.DEBOUNCE_SECS)

    # ¿Llegó otro mensaje después del mío? Si sí, ese task se encargará.
    ts_ultimo = await yahuar_mod.get_ultimo_ts()
    if ts_ultimo > ts_mio + 0.1:
        print(f"[YAHUAR] hay mensaje más reciente, este task cede", flush=True)
        return {"status": "ok"}

    # ¿Ya fue procesado por otro task? (doble seguro)
    if await yahuar_mod.acumulador_ya_procesado():
        print(f"[YAHUAR] acumulador ya procesado, saliendo", flush=True)
        return {"status": "ok"}

    # ── Soy el último — proceso todo ──────────────────────────────────────────
    mensajes = await yahuar_mod.get_y_limpiar_acumulador()
    print(f"[YAHUAR] procesando {len(mensajes)} mensajes acumulados", flush=True)

    messenger   = _messenger()
    textos      = []
    imagen_b64  = None
    imagen_mime = "image/jpeg"

    for i, msg in enumerate(mensajes):
        t       = msg.get("body") or ""
        has_m   = msg.get("hasMedia", False)
        media   = msg.get("media") or {}
        m_data  = media.get("data")
        print(f"[YAHUAR] msg[{i}] body={t[:40]!r} hasMedia={has_m} media_keys={list(media.keys())} data_len={len(m_data) if m_data else 0}", flush=True)
        if t:
            t_low = t.lower()
            if any(e in t_low for e in _ERRORES_YAHUAR):
                if bloqueante:
                    await yahuar_mod.guardar_resultado(placa, {
                        "error": "NO_ENCONTRADO",
                        "mensaje": (f"No pude obtener datos de la placa {placa}. "
                                    "Verifica que esté escrita correctamente."),
                    })
                    return {"status": "ok", "modo": "bloqueante"}
                await messenger.send_message(
                    destino, "",
                    f"⚠️ No pude obtener datos de la placa *{placa}*. "
                    "Verifica que esté escrita correctamente.",
                )
                return {"status": "ok"}
            textos.append(t)
        if has_m and not imagen_b64:
            if m_data:
                imagen_b64  = m_data
                imagen_mime = media.get("mimetype", "image/jpeg")
            elif media.get("url"):
                try:
                    import httpx, base64 as b64lib
                    from shared.waha import WAHA_BASE_URL, _headers
                    img_url = media["url"]
                    if img_url.startswith("/"):
                        img_url = f"{WAHA_BASE_URL}{img_url}"
                    async with httpx.AsyncClient(timeout=20) as c:
                        r = await c.get(img_url, headers=_headers())
                        if r.status_code == 200:
                            imagen_b64  = b64lib.b64encode(r.content).decode()
                            imagen_mime = media.get("mimetype") or r.headers.get("content-type", "image/jpeg").split(";")[0]
                            print(f"[YAHUAR] imagen descargada: {len(imagen_b64)} chars mime={imagen_mime}", flush=True)
                        else:
                            print(f"[YAHUAR] error descargando imagen: {r.status_code}", flush=True)
                except Exception as e:
                    print(f"[YAHUAR] excepción descargando imagen: {e}", flush=True)

    # Extraer datos del vehículo si hay imagen
    datos_vision = None
    if imagen_b64:
        try:
            datos_vision = await llm_mod.extraer_texto_de_imagen(
                imagen_b64, _INSTRUCCION_PLACA, media_type=imagen_mime
            )
            print(f"[YAHUAR] visión OK datos={bool(datos_vision)}", flush=True)
        except Exception as e:
            print(f"[YAHUAR] error visión: {e}", flush=True)

    # ── Modo bloqueante: guardar el resultado para el subagente y NO empujar ──
    if bloqueante:
        await yahuar_mod.guardar_resultado(placa, {
            "datos": datos_vision or ("\n\n".join(textos) if textos else ""),
            "imagen_base64": imagen_b64,
            "imagen_mime": imagen_mime,
        })
        print(f"[YAHUAR] modo bloqueante: resultado guardado para el subagente (placa {placa})", flush=True)
        return {"status": "ok", "modo": "bloqueante"}

    # Construir y enviar respuesta final
    if datos_vision:
        await messenger.send_message(destino, "", f"🚗 *Placa {placa}*\n\n{datos_vision}")
    elif textos:
        await messenger.send_message(destino, "", "\n\n".join(textos))

    if imagen_b64:
        ext      = imagen_mime.split("/")[-1].replace("jpeg", "jpg")
        filename = f"placa_{placa}.{ext}"
        try:
            await messenger.send_image_base64(
                destino, "", imagen_b64,
                caption=f"Tarjeta vehicular — {placa}",
                filename=filename,
            )
            print(f"[YAHUAR] foto enviada a {destino!r} ({filename})", flush=True)
        except Exception as e:
            print(f"[YAHUAR] ERROR enviando foto: {e}", flush=True)

    # Guardar el resultado de la placa en el historial (Redis + panel), en el
    # mismo hilo del vendedor. El relay va por fuera del flujo normal de guardado,
    # así que hay que persistirlo acá a mano.
    partes = []
    if datos_vision:
        partes.append(f"🚗 Placa {placa}\n\n{datos_vision}")
    elif textos:
        partes.append("\n\n".join(textos))
    if imagen_b64:
        partes.append(f"📷 [Foto de la tarjeta vehicular — {placa}]")
    if partes:
        contenido = "\n\n".join(partes)
        numero_dest = destino.split("@")[0].lstrip("+")
        if "@lid" in destino:
            tel = await waha_mod.resolve_lid_to_phone(destino)
            if tel:
                numero_dest = tel
        try:
            await context.save_message(numero_dest, "assistant", contenido)
            await vendedores_chat.guardar(numero_dest, "assistant", contenido)
        except Exception as e:
            print(f"[YAHUAR] error guardando resultado: {e}", flush=True)

    return {"status": "ok"}


async def _manejar_respuesta_yahuar(payload: dict) -> dict:
    """Llamado desde el auto-aprendizaje cuando detectamos un LID desconocido de Yahuar."""
    pendiente = await yahuar_mod.get_pendiente()
    if not pendiente:
        return {"status": "ignored", "reason": "no pending"}
    destino = pendiente["from_field"]
    placa   = pendiente.get("placa", "")
    await yahuar_mod.open_relay(destino)
    return await _reenviar_yahuar(payload, destino, placa)


@router_wh.post("/waha")
async def webhook_waha(request: Request):
    """Recibe eventos de WAHA (message, message.ack, session.status, etc.)."""
    raw_body = await request.body()

    # Verificación simple por API key en header (opcional pero recomendado)
    waha_key = request.headers.get("X-Api-Key", "")
    expected = os.getenv("WAHA_WEBHOOK_TOKEN", "")
    if expected and waha_key != expected:
        print(f"[WAHA] token inválido: {waha_key!r}")
        raise HTTPException(status_code=401, detail="invalid token")

    try:
        data = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    event_name = data.get("event", "")
    payload_preview = str(data)[:300]
    print(f"[WAHA] evento recibido: {event_name!r} payload={payload_preview}", flush=True)

    tarea = asyncio.create_task(_procesar_item_waha_bg(data))
    _tareas_bg.add(tarea)
    tarea.add_done_callback(_tareas_bg.discard)

    return {"status": "accepted"}


async def _procesar_item_waha_bg(data: dict) -> None:
    try:
        print("[WAHA] bg: iniciando procesamiento", flush=True)
        await _procesar_item_waha(data)
        print("[WAHA] bg: procesamiento completado", flush=True)
    except Exception as e:
        logging.error(f"Error procesando evento WAHA: {e}", exc_info=True)
        print(f"[WAHA] ERROR en background: {e}", flush=True)
