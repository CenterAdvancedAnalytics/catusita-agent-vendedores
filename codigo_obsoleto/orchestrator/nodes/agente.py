"""Nodo 'agente': llama a Claude con las tools del canal y agrega el AIMessage al estado."""
import os
from datetime import datetime, timezone, timedelta
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

from orchestrator.graph_state import AgentState
from orchestrator.lc_tools import TOOLS_VENDEDOR_LC, TOOLS_CLIENTE_LC
from orchestrator.prompts import SYSTEM_VENDEDOR, SYSTEM_CLIENTE

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Perú no usa horario de verano: UTC-5 fijo todo el año (sin dependencia de tzdata).
_PERU_TZ = timezone(timedelta(hours=-5))
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_hora_peru() -> str:
    """Fecha y hora actual de Perú, lista para inyectar al prompt."""
    ahora = datetime.now(_PERU_TZ)
    return (f"{_DIAS[ahora.weekday()]} {ahora.day} de {_MESES[ahora.month - 1]} "
            f"de {ahora.year}, {ahora:%H:%M} (hora de Perú)")

# Dos modelos pre-bindeados para evitar re-bindear en cada llamada.
# Se crean lazy al primer uso del nodo.
_bound: dict = {}


def _get_bound(canal: str, perfil: dict) -> ChatAnthropic:
    if canal not in _bound:
        tools = TOOLS_VENDEDOR_LC if canal == "vendedor" else TOOLS_CLIENTE_LC
        _bound[canal] = ChatAnthropic(model=_MODEL, max_tokens=2048).bind_tools(tools)
    return _bound[canal]


def _bloque_contexto(contexto: dict) -> str | None:
    """Construye un bloque de texto con las entidades pre-resueltas para inyectar al modelo."""
    if not contexto:
        return None
    lineas = ["[Contexto pre-resuelto — usa estos datos directamente sin re-preguntar]"]
    if contexto.get("ruc_detectado"):
        label = f" ({contexto['nombre_resuelto']})" if contexto.get("nombre_resuelto") else ""
        lineas.append(f"- RUC del cliente: {contexto['ruc_detectado']}{label}")
    if contexto.get("pedido_detectado"):
        lineas.append(f"- ID del pedido: {contexto['pedido_detectado']}")
    if contexto.get("placa_detectada"):
        lineas.append(f"- Placa del vehículo: {contexto['placa_detectada']}")
    if contexto.get("sku_detectado"):
        lineas.append(f"- SKU del producto: {contexto['sku_detectado']}")
    return "\n".join(lineas) if len(lineas) > 1 else None


async def nodo_agente(state: AgentState) -> dict:
    canal = state.get("canal", "vendedor")
    perfil = state.get("perfil", {})
    contexto = state.get("contexto_resuelto") or {}
    validacion = state.get("validacion") or {}

    if canal == "vendedor":
        system_text = SYSTEM_VENDEDOR.format(
            nombre=perfil.get("nombre", "Asesor"),
            linea_asignada=perfil.get("linea_asignada", "general"),
            vendedor_id=perfil.get("vendedor_id", "V001"),
        )
    else:
        system_text = SYSTEM_CLIENTE

    # Fecha/hora actual de Perú: el agente la necesita para razonar con
    # vencimientos de letras, "esta semana", "último pedido", etc.
    system_text = f"{system_text}\n\nFecha y hora actual: {_fecha_hora_peru()}."

    mensajes = list(state["messages"])

    # Inyectar contexto pre-resuelto en el último mensaje del usuario (solo primera vez)
    bloque = _bloque_contexto(contexto)
    if bloque and mensajes:
        ultimo = mensajes[-1]
        # Solo inyectar si es el mensaje del usuario que aún no tiene el bloque
        if isinstance(ultimo, HumanMessage) and "[Contexto pre-resuelto" not in (ultimo.content or ""):
            texto_original = ultimo.content if isinstance(ultimo.content, str) else ""
            mensajes[-1] = HumanMessage(content=f"{texto_original}\n\n{bloque}")

    # Si el validador rechazó el borrador, inyectar el motivo como nota de sistema
    if validacion.get("ok") is False and validacion.get("motivo"):
        nota = (
            f"[CORRECCIÓN REQUERIDA] Tu respuesta anterior fue rechazada por el siguiente motivo: "
            f"{validacion['motivo']}. "
            f"Por favor, revisa los tool_results disponibles y responde corrigiendo ese problema."
        )
        mensajes = mensajes + [HumanMessage(content=nota)]

    model = _get_bound(canal, perfil)
    messages_with_system = [SystemMessage(content=system_text)] + mensajes
    response = await model.ainvoke(messages_with_system)
    return {"messages": [response]}
