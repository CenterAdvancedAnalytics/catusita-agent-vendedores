"""Control de acceso por cartera para el agente de vendedores.

Garantiza que un asesor solo pueda consultar clientes de SU cartera, y resuelve
nombres parciales a RUC. Esto NO se deja al system prompt: se valida en código
antes de tocar el Mock SAP.

`verificar_acceso_cartera` es el punto de entrada que usan las @tool de
orchestrator/lc_tools.py: devuelve un dict de error si debe bloquearse, o None
si la tool puede ejecutarse (habiendo normalizado, de paso, el RUC en `args`).
"""
from agents import cartera

# Tools cuyo RUC debe pertenecer a la cartera del asesor antes de ejecutarse.
# El valor es el nombre del argumento que contiene el RUC del cliente.
# Tras la migración a la API real, las tools de crédito/cobranzas/historial/
# pedidos/documentos están apagadas; solo queda consultar_perfil_cliente.
RUC_SCOPED_TOOLS = {
    "consultar_perfil_cliente": "ruc",
    "consultar_pedidos": "cliente_ruc",
    "enviar_documento": "cliente_ruc",
    "consultar_pago_documento": "cliente_ruc",
}


async def _rucs_de_cartera(perfil: dict) -> set:
    """RUCs de la cartera del asesor, cacheados en el perfil para no repetir
    llamadas al Mock SAP dentro del mismo turno."""
    cache = perfil.get("_cartera_rucs")
    if cache is not None:
        return cache
    data = await cartera.consultar_cartera(perfil.get("vendedor_id", "V001"))
    rucs = {c["ruc"] for c in data.get("clientes", [])} if isinstance(data, dict) else set()
    perfil["_cartera_rucs"] = rucs
    return rucs


async def _resolver_ruc(ruc_o_nombre: str, perfil: dict) -> dict:
    """
    Intenta resolver un RUC o nombre parcial al RUC de un cliente en la cartera
    del asesor.
    Retorna un diccionario:
      - {"status": "ok", "ruc": "..."} si se resolvió a un único cliente.
      - {"status": "multiple", "clientes": [...]} si hay varias coincidencias.
      - {"status": "none"} si no hay ninguna coincidencia.
    """
    ruc_clean = ruc_o_nombre.strip()
    vendedor_id = perfil.get("vendedor_id", "V001")
    try:
        data = await cartera.consultar_cartera(vendedor_id)
        clientes = data.get("clientes", []) if isinstance(data, dict) else []
    except Exception:
        clientes = []

    # 1. Intentar coincidencia exacta de RUC
    for c in clientes:
        if c.get("ruc") == ruc_clean:
            return {"status": "ok", "ruc": ruc_clean}

    # 2. Si no es coincidencia exacta, buscar por nombre (razon_social)
    nombre_lower = ruc_clean.lower()
    exact_matches = []
    partial_matches = []

    for c in clientes:
        ruc_val = c.get("ruc")
        razon_social = c.get("razon_social", "")
        razon_lower = razon_social.lower()

        if ruc_val:
            if nombre_lower == razon_lower:
                exact_matches.append(c)
            elif nombre_lower in razon_lower or razon_lower in nombre_lower:
                partial_matches.append(c)

    if len(exact_matches) == 1:
        return {"status": "ok", "ruc": exact_matches[0]["ruc"]}
    elif len(exact_matches) > 1:
        return {"status": "multiple", "clientes": exact_matches}

    if len(partial_matches) == 1:
        return {"status": "ok", "ruc": partial_matches[0]["ruc"]}
    elif len(partial_matches) > 1:
        return {"status": "multiple", "clientes": partial_matches}

    return {"status": "none"}


async def verificar_acceso_cartera(name: str, args: dict, perfil: dict) -> dict | None:
    """
    Valida que el RUC (o nombre) que recibe una tool scopeada pertenezca a la
    cartera del asesor. Si procede, normaliza `args[arg_ruc]` al RUC resuelto.

    Devuelve:
      - None  → la tool puede ejecutarse (no es scopeada, no es asesor, o el
                cliente sí es de su cartera).
      - dict  → error a devolver tal cual (ACCESO_DENEGADO / MULTIPLE_COINCIDENCIAS
                / CLIENTE_NO_ENCONTRADO).
    """
    arg_ruc = RUC_SCOPED_TOOLS.get(name)
    if not arg_ruc or perfil.get("tipo") != "asesor":
        return None

    ruc = (args.get(arg_ruc) or "").strip()
    if ruc:
        # Detectar si el input tiene formato de RUC
        es_ruc_formato = ruc.isdigit() and len(ruc) == 11

        res = await _resolver_ruc(ruc, perfil)
        if res["status"] == "ok":
            args[arg_ruc] = res["ruc"]
            ruc = res["ruc"]
        elif res["status"] == "multiple":
            clientes_simplificados = [
                {"ruc": c["ruc"], "razon_social": c["razon_social"]}
                for c in res["clientes"]
            ]
            return {
                "error": "MULTIPLE_COINCIDENCIAS",
                "mensaje": (
                    f"Se encontraron múltiples clientes con el término '{ruc}' en tu cartera. "
                    "Por favor, pregúntale al usuario a cuál de ellos se refiere."
                ),
                "clientes": clientes_simplificados,
            }
        else:
            if es_ruc_formato:
                return {
                    "error": "ACCESO_DENEGADO",
                    "mensaje": (
                        "Ese cliente no pertenece a tu cartera asignada. "
                        "Solo puedo darte información de tus propios clientes."
                    ),
                }
            else:
                return {
                    "error": "CLIENTE_NO_ENCONTRADO",
                    "mensaje": (
                        f"No se encontró ningún cliente con el término '{ruc}' en tu cartera. "
                        "Por favor, pídele al usuario que verifique el nombre o te proporcione su RUC."
                    ),
                }

    if ruc and ruc not in await _rucs_de_cartera(perfil):
        return {
            "error": "ACCESO_DENEGADO",
            "mensaje": (
                "Ese cliente no pertenece a tu cartera asignada. "
                "Solo puedo darte información de tus propios clientes."
            ),
        }

    return None
