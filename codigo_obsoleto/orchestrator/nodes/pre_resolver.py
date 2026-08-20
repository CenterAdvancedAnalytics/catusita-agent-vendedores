"""Nodo pre_resolver: extrae entidades del mensaje del usuario antes de pasarlo al LLM.

Resuelve de forma determinista (sin LLM cuando sea posible):
  - RUC (11 dígitos)
  - Pedido ID (PED-XXXXXX o similar)
  - Placa (ABC-123 o ABCD12)
  - SKU (FIL-BOC-0001, etc.)
  - Nombre de cliente → RUC (búsqueda en cartera del asesor)

El resultado se guarda en state["contexto_resuelto"] y el nodo agente lo inyecta
como bloque de contexto al inicio del primer mensaje del usuario, evitando
que Claude tenga que re-preguntar datos que ya mencionó el usuario.
"""
import re
from langchain_core.messages import HumanMessage

from orchestrator.graph_state import AgentState

# ─── Patrones regex ───────────────────────────────────────────────────────────

_RE_RUC     = re.compile(r"\b(20\d{9}|10\d{9})\b")
_RE_PEDIDO  = re.compile(r"\b(PED-\d{3,8})\b", re.IGNORECASE)
_RE_PLACA   = re.compile(r"\b([A-Z]{3}-?\d{3,4}|[A-Z0-9]{6,7})\b")
_RE_SKU     = re.compile(r"\b([A-Z]{2,4}-[A-Z]{2,4}-\d{3,6})\b", re.IGNORECASE)


def _extraer_texto(messages: list) -> str:
    """Concatena el texto de los últimos 4 mensajes para buscar entidades."""
    partes = []
    for m in messages[-4:]:
        if isinstance(m, HumanMessage):
            content = m.content if isinstance(m.content, str) else ""
            partes.append(content)
    return " ".join(partes)


async def _resolver_nombre_a_ruc(texto: str, perfil: dict) -> dict | None:
    """Busca coincidencia de nombre parcial en la cartera del asesor.

    Retorna {"ruc": "...", "razon_social": "..."} si hay coincidencia única.
    Retorna None si no aplica o hay ambigüedad.
    """
    if perfil.get("tipo") != "asesor":
        return None

    from agents.cartera import consultar_cartera
    try:
        data = await consultar_cartera(perfil.get("vendedor_id", "V001"))
        clientes = data.get("clientes", []) if isinstance(data, dict) else []
    except Exception:
        return None

    texto_lower = texto.lower()
    matches = []
    for c in clientes:
        razon = (c.get("razon_social") or "").lower()
        if len(razon) >= 4 and razon in texto_lower:
            matches.append(c)

    if len(matches) == 1:
        return {"ruc": matches[0]["ruc"], "razon_social": matches[0]["razon_social"]}
    return None


async def nodo_pre_resolver(state: AgentState) -> dict:
    """Extrae entidades del mensaje y las almacena en contexto_resuelto."""
    perfil = state.get("perfil", {})
    texto = _extraer_texto(state.get("messages", []))

    contexto: dict = {}

    # RUC
    rucs = _RE_RUC.findall(texto)
    if rucs:
        contexto["ruc_detectado"] = rucs[0]

    # Pedido
    pedidos = _RE_PEDIDO.findall(texto.upper())
    if pedidos:
        contexto["pedido_detectado"] = pedidos[0].upper()

    # Placa (solo si no parece ser un RUC o pedido con mismo patrón)
    for m in _RE_PLACA.finditer(texto.upper()):
        val = m.group(1)
        if not _RE_RUC.match(val) and not _RE_PEDIDO.match(val):
            contexto["placa_detectada"] = val
            break

    # SKU
    skus = _RE_SKU.findall(texto.upper())
    if skus:
        contexto["sku_detectado"] = skus[0].upper()

    # Nombre de cliente → RUC (solo para asesores, solo si no hay RUC ya)
    if not contexto.get("ruc_detectado"):
        resolucion = await _resolver_nombre_a_ruc(texto, perfil)
        if resolucion:
            contexto["ruc_detectado"] = resolucion["ruc"]
            contexto["nombre_resuelto"] = resolucion["razon_social"]

    return {"contexto_resuelto": contexto}
