"""Subagente de búsqueda de catálogo (A2A).

Reemplaza el match determinista por un mini-agente Claude que RAZONA los términos:
maneja sinónimos/abreviaturas, ignora el año y el código de motor cuando rompen el
match, y REINTENTA relajando la búsqueda hasta encontrar productos o agotar estrategias.

Su única acción es la tool `buscar` (que consulta el catálogo real). El harness de
Python recorre el loop de tool-use, se queda con el primer término que trae productos
y devuelve la MISMA forma que agents.catalog_rag.buscar_catalogo (drop-in).

Motivación: incidencia #20 — 'filtro para KIA Picanto 2015' devolvía 0 porque el año
y el motor no están en el nombre del producto y el match exigía TODOS los tokens.
"""
import os
import json
import logging

from shared import llm
from shared.sap_client import sap

# Modelo del subagente. Por defecto el mismo que ya funciona en prod; se puede
# bajar a Haiku por env (SUBAGENT_MODEL) para abaratar/acelerar el hot path.
_SUBAGENT_MODEL = os.getenv("SUBAGENT_MODEL", llm.MODEL)
_MAX_ITERS = int(os.getenv("SUBAGENT_BUSCADOR_MAX_ITERS", "4"))

SYSTEM_BUSCADOR = """Eres el BUSCADOR del catálogo de repuestos automotrices de Catusita.
Recibes la consulta de un vendedor y tu ÚNICO trabajo es ENCONTRAR productos usando la tool `buscar`.

Cómo armar el término de búsqueda (CLAVE):
- Usa el SUSTANTIVO del repuesto + la marca/modelo. Ej: "filtro de aire picanto".
- NUNCA metas en el término el AÑO (2015, 2018…) ni el CÓDIGO DE MOTOR (G4LA, 1GD, 2ZR…):
  el catálogo NO los tiene en el nombre y hacen que la búsqueda devuelva 0.
- El catálogo abrevia: posterior/trasero→"post", delantero→"del", habitáculo→"cabina" o "aire",
  izquierdo→"izq", derecho→"der". Si la palabra natural no trae nada, prueba la abreviatura.
- Si un término da total=0, REINTENTA con menos palabras (deja el sustantivo + marca) o con un
  sinónimo. No te rindas al primer intento fallido.
- Apenas `buscar` devuelva productos (total>0), TERMINA: no sigas buscando.
- Si tras varios intentos razonables sigue en 0, es que el producto no está: termina.

Haz como máximo 4 búsquedas. Sé breve; no expliques, solo busca."""

_TOOL_BUSCAR = [{
    "name": "buscar",
    "description": "Busca productos en el catálogo por un término de texto. Devuelve total y una muestra.",
    "input_schema": {
        "type": "object",
        "properties": {
            "termino": {
                "type": "string",
                "description": ("Palabras clave: sustantivo del repuesto + marca/modelo. "
                                "NO incluyas año ni código de motor."),
            }
        },
        "required": ["termino"],
    },
}]


async def buscar_catalogo_subagente(query: str, placa: str = None, vin: str = None) -> dict:
    """Busca en el catálogo razonando los términos. Misma salida que catalog_rag.buscar_catalogo.

    Si el subagente falla (LLM caído, etc.), cae al buscador determinista (query literal)."""
    vehiculo = None
    if placa or vin:
        veh = await sap.get_vehiculo(placa or vin)
        if isinstance(veh, dict) and "error" not in veh:
            vehiculo = veh
            if veh.get("repuestos_compatibles"):
                return {"query": query, "vehiculo": vehiculo,
                        "resultados": veh["repuestos_compatibles"],
                        "total": len(veh["repuestos_compatibles"])}
            # Enriquecer la consulta con marca/modelo si el vehículo se identificó
            extra = " ".join(str(veh.get(k, "")) for k in ("marca", "modelo")).strip()
            if extra:
                query = f"{query} {extra}".strip()

    try:
        mejor, termino = await _loop_busqueda(query)
    except Exception as e:
        logging.error(f"[buscador_subagente] fallo, uso buscador determinista: {e}")
        cat = await sap.get_catalogo(q=query)
        return {"query": query, "vehiculo": vehiculo,
                "resultados": cat.get("productos", []), "total": cat.get("total", 0),
                **({"nota": cat["nota"]} if cat.get("nota") else {})}

    salida = {"query": query, "vehiculo": vehiculo,
              "resultados": mejor.get("productos", []), "total": mejor.get("total", 0)}
    if termino and termino.lower() != (query or "").lower():
        salida["termino_busqueda"] = termino
    if mejor.get("nota"):
        salida["nota"] = mejor["nota"]
    return salida


async def _loop_busqueda(query: str) -> tuple[dict, str | None]:
    """Loop de tool-use: el subagente propone términos, el catálogo responde, se corta
    apenas un término trae productos. Devuelve (mejor_resultado, termino_usado)."""
    messages = [{"role": "user", "content": f"Consulta del vendedor: {query!r}. Encuentra los productos."}]
    mejor: dict = {"total": 0, "productos": []}
    termino_usado: str | None = None

    for _ in range(_MAX_ITERS):
        resp = await llm.create_message(
            system=SYSTEM_BUSCADOR, messages=messages, tools=_TOOL_BUSCAR,
            max_tokens=512, model=_SUBAGENT_MODEL,
        )
        if resp.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use" or block.name != "buscar":
                continue
            termino = (block.input or {}).get("termino", "").strip()
            cat = await sap.get_catalogo(q=termino) if termino else {}
            total = cat.get("total", 0)
            prods = cat.get("productos", [])
            if prods and not mejor["productos"]:
                mejor = {"total": total, "productos": prods, "nota": cat.get("nota")}
                termino_usado = termino
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(
                    {"termino": termino, "total": total,
                     "muestra": [p.get("nombre", "") for p in prods[:5]]},
                    ensure_ascii=False),
            })
        messages.append({"role": "user", "content": tool_results})

        if mejor["productos"]:  # ya encontró: cortar el loop
            break

    return mejor, termino_usado
