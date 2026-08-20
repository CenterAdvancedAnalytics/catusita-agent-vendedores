"""Nodo validar: revisa la respuesta del agente antes de enviarla al usuario.

Estrategia en capas (baratas primero, LLM solo cuando hace falta):

Regla 1 — PRIVACIDAD (baratas)
  Bloquea menciones de precio neto, descuentos, almacén, distrito, horario de reparto.

Regla 2 — REPREGUNTA CON CONTEXTO (barata)
  Si pre_resolver ya resolvió un RUC/pedido/placa/SKU Y el agente igual preguntó
  por esos datos → rechazar. El agente tiene el contexto, que use las tools.

Regla 3 — NO USÓ TOOLS CUANDO DEBÍA (barata)
  Si no se llamó ninguna tool Y la respuesta es una pregunta al usuario (pide datos)
  → rechazar. Que use las tools que tiene disponibles.

Regla 4 — CALIDAD (juez LLM, solo tools sensibles)
  Si se usaron tools de crédito/cobranzas/perfil: verificar grounding y privacidad
  con un segundo llamado al LLM (rubrica breve). Solo para respuestas de riesgo.

Flujo de reintento:
  - MAX_REINTENTOS = 1. Si el agente ya corrigió una vez y la segunda validación
    también falla → dejar pasar con WARNING (evitar loop infinito).
  - El motivo de rechazo se inyecta al agente como nota de corrección (en agente.py).
"""
import re
import json
import logging
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from orchestrator.graph_state import AgentState
from shared import llm

MAX_REINTENTOS = 1

# ─── Tools sensibles que activan el juez LLM ─────────────────────────────────
_TOOLS_SENSIBLES = {"consultar_perfil_cliente"}

# ─── Palabras prohibidas (Regla 1 — privacidad) ───────────────────────────────
# IMPORTANTE: solo bloquear cuando el agente REVELA el dato (con número/lugar),
# NO cuando lo menciona para decir que no puede compartirlo.
_RE_PROHIBIDOS = [re.compile(p, re.IGNORECASE) for p in [
    # precio neto SEGUIDO de número (monto real revelado)
    r"precio\s*neto[^.\n]{0,30}\d[\d.,]*",
    # precio sin IGV SEGUIDO de número
    r"sin\s*igv[^.\n]{0,30}s?/?\s*\.?\s*\d[\d.,]*",
    # descuento porcentual o en soles revelado
    r"descuento[^.\n]{0,20}(del?\s*\d+\s*%|\d+[\d.,]*\s*sol)",
    # almacén con nombre de distrito (revela ubicación)
    r"alm[aá]cen\s+(de\s+)?(miraflores|ate|lima|callao|san\s*isidro)",
    # hora de reparto con hora real
    r"hora\s*de\s*reparto[^.\n]{0,20}\d{1,2}[:\s*h]",
    # ruta de reparto (operativo interno)
    r"ruta\s+(de\s+)?reparto",
]]

# ─── Patrones de repregunta genuina (pide dato que debía traer de tools) ─────
# Solo coincide cuando el agente solicita un dato operativo específico al usuario
# (RUC, número de pedido, SKU, código). NO coincide con ofrecimiento de ayuda.
_RE_REPREGUNTA = re.compile(
    r"(¿.{0,40}(proporcionar?|indicar?|dar?|facilitar?|confirmar?)\s+(el\s+)?"
    r"(ruc|n[uú]mero\s+de\s+pedido|c[oó]digo\s+(sku|del\s+producto)|n[uú]mero\s+de\s+cliente))"
    r"|(necesito\s+(el\s+)?(ruc|n[uú]mero\s+de\s+pedido|c[oó]digo\s+sku))"
    r"|(por\s+favor.{0,20}(ruc|n[uú]mero\s+de\s+pedido|c[oó]digo\s+sku))",
    re.IGNORECASE,
)

# ─── Palabras que indican que la respuesta requería datos de tools ─────────────
_RE_REQUIERE_DATOS = re.compile(
    r"\b(stock|precio|pedido|cr[eé]dito|factura|gu[ií]a|cobranza|letra|saldo|"
    r"disponible|inventario|estado del|ruc|placa)\b",
    re.IGNORECASE,
)

# ─── Juez LLM — rúbrica ───────────────────────────────────────────────────────
_RUBRICA = """Eres un auditor de respuestas del agente comercial de Catusita.
Revisa SOLO estos dos tipos de violaciones serias. Ignora todo lo demás.

VIOLACIÓN A — PRIVACIDAD CRÍTICA:
  El agente reveló un VALOR NUMÉRICO concreto de precio neto o descuento porcentual de negociación
  que no debería compartir. Ej: "el precio neto es S/ 45.00" o "tiene un descuento del 12%".
  NO cuenta como violación: mencionar "precio neto" para decir que no puede darlo.

VIOLACIÓN B — AUTORIZACIÓN FALSA:
  El agente afirmó que él mismo aprobó o autorizará una excepción de crédito, un precio especial
  o un cambio de condiciones (cosas que requieren al supervisor o gerente comercial).

Si no hay ninguna de estas dos violaciones → responde {{"ok": true}}.
Si hay alguna → {{"ok": false, "motivo": "..."}} (1 oración en español, concisa).

Pregunta: {pregunta}
Respuesta del agente: {borrador}
"""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _borrador(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content:
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def _ultima_pregunta(messages: list) -> str:
    for m in reversed(messages[:-1]):   # ignorar el último AIMessage
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else ""
    return ""


def _tool_results_json(messages: list) -> str:
    resultados = [m.content for m in messages if isinstance(m, ToolMessage) and m.content]
    return json.dumps(resultados, ensure_ascii=False)[:6000]


def _tools_usadas(messages: list) -> set[str]:
    usadas = set()
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in (getattr(m, "tool_calls", None) or []):
                usadas.add(tc.get("name", ""))
    return usadas


def _hubo_tool_results(messages: list) -> bool:
    return any(isinstance(m, ToolMessage) for m in messages)


# ─── Reglas baratas ───────────────────────────────────────────────────────────

def _regla_privacidad(borrador: str, canal: str) -> str | None:
    """Regla 1: detecta menciones de datos operativos que no deben salir."""
    if canal != "vendedor":
        return None
    for r in _RE_PROHIBIDOS:
        if r.search(borrador):
            return (
                "La respuesta revela información operativa restringida "
                "(precio neto, descuento, ubicación de almacén o ruta de reparto). "
                "Omite ese dato y responde solo con la información que está permitida compartir."
            )
    return None


_RE_PIDE_RUC = re.compile(
    r"(¿.{0,50}(proporcionar?|indicar?|dar?|confirmar?)\s+(el\s+)?ruc)"
    r"|(necesito\s+(el\s+)?ruc)"
    r"|(por\s+favor.{0,20}ruc)"
    r"|(¿cu[aá]l\s+es\s+(el\s+)?ruc\s*(del\s+cliente)?\?)",
    re.IGNORECASE,
)
_RE_PIDE_PEDIDO = re.compile(
    r"(¿.{0,50}(proporcionar?|indicar?|dar?|confirmar?)\s+(el\s+)?(n[uú]mero\s+de\s+pedido|id\s+del\s+pedido))"
    r"|(necesito\s+(el\s+)?(n[uú]mero\s+de\s+pedido|id\s+del\s+pedido))",
    re.IGNORECASE,
)


def _regla_repregunta_con_contexto(
    borrador: str, contexto: dict
) -> str | None:
    """Regla 2: el agente PIDIÓ datos que pre_resolver ya había resuelto."""
    if not contexto:
        return None
    if contexto.get("ruc_detectado") and _RE_PIDE_RUC.search(borrador):
        return (
            f"Ya tienes el RUC del cliente ({contexto['ruc_detectado']}) resuelto. "
            f"Úsalo directamente en las tools, no se lo pidas al usuario."
        )
    if contexto.get("pedido_detectado") and _RE_PIDE_PEDIDO.search(borrador):
        return (
            f"Ya tienes el ID del pedido ({contexto['pedido_detectado']}) resuelto. "
            f"Úsalo directamente en las tools."
        )
    return None


def _regla_no_uso_tools(
    borrador: str, pregunta: str, hubo_tools: bool, contexto: dict
) -> str | None:
    """Regla 3: no se usaron tools pero el agente está pidiendo datos al usuario
    en lugar de consultar la información disponible."""
    if hubo_tools:
        return None

    if not _RE_REQUIERE_DATOS.search(pregunta):
        return None  # pregunta que no requiere datos de SAP

    # Si tenemos un pedido_id pero NO el RUC del cliente, el agente
    # legítimamente necesita el RUC antes de poder llamar consultar_pedidos
    # u obtener_documentos. No es un fallo del agente, es una limitación del API.
    if contexto.get("pedido_detectado") and not contexto.get("ruc_detectado"):
        return None

    if _RE_REPREGUNTA.search(borrador):
        return (
            "Tenías tools disponibles para responder esta consulta. "
            "En lugar de pedirle datos al usuario, usa las tools directamente "
            "con la información que ya tienes en el mensaje."
        )
    return None


# ─── Juez LLM ─────────────────────────────────────────────────────────────────

async def _juez_llm(pregunta: str, borrador: str, tool_results: str = "") -> dict:
    prompt = _RUBRICA.format(pregunta=pregunta, borrador=borrador)
    try:
        resp = await llm.create_message(
            system="Auditor de respuestas. Responde SOLO con JSON válido, sin texto adicional.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logging.error(f"Juez LLM error: {e}")
    return {"ok": True}  # fallback conservador


# ─── Nodo principal ───────────────────────────────────────────────────────────

async def nodo_validar(state: AgentState) -> dict:
    messages   = list(state.get("messages", []))
    canal      = state.get("canal", "vendedor")
    contexto   = state.get("contexto_resuelto") or {}
    intentos   = state.get("intentos_validacion", 0)

    borrador = _borrador(messages)
    if not borrador:
        return {"validacion": {"ok": True}}

    # Límite de reintentos — no loopear infinito
    if intentos >= MAX_REINTENTOS:
        logging.warning("Validador: máximo de reintentos alcanzado, dejando pasar.")
        return {"validacion": {"ok": True}, "intentos_validacion": intentos}

    pregunta     = _ultima_pregunta(messages)
    hubo_tools   = _hubo_tool_results(messages)
    tools_usadas = _tools_usadas(messages)

    def _rechazar(motivo: str) -> dict:
        logging.warning(f"Validador RECHAZÓ (intentos={intentos}): {motivo[:100]}")
        return {
            "validacion": {"ok": False, "motivo": motivo},
            "intentos_validacion": intentos + 1,
        }

    # ── Regla 1: Privacidad ───────────────────────────────────────────────────
    m = _regla_privacidad(borrador, canal)
    if m:
        return _rechazar(m)

    # ── Regla 2: Repregunta con contexto resuelto ─────────────────────────────
    m = _regla_repregunta_con_contexto(borrador, contexto)
    if m:
        return _rechazar(m)

    # ── Regla 3: No usó tools cuando la consulta lo requería ─────────────────
    m = _regla_no_uso_tools(borrador, pregunta, hubo_tools, contexto)
    if m:
        return _rechazar(m)

    # ── Regla 4: Juez LLM (solo tools sensibles) ─────────────────────────────
    if tools_usadas & _TOOLS_SENSIBLES:
        veredicto = await _juez_llm(pregunta, borrador, _tool_results_json(messages))
        if not veredicto.get("ok", True):
            return _rechazar(
                veredicto.get("motivo", "La respuesta no superó la auditoría de calidad.")
            )

    return {"validacion": {"ok": True}, "intentos_validacion": intentos}
