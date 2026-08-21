"""Acceso de `clientes` (vendedores) a la API de Catusita.

    GET /api/client/CustomerbySeller?SellerId=   la cartera del asesor
    GET /api/client/CustomerbyFilter?RUCClient=  el perfil de uno

Devuelve datos y hechos sobre los datos. Nada de instrucciones para el modelo:
eso vive en `prompt.py`, que es donde se lee junto con el resto de las reglas.
"""
from vendedores.plataforma_vendedores import catusita_api

# Tope de filas que se le pasan al modelo. NO es cosmético: la cartera completa
# son 187 clientes (~14.400 tokens) y medirlo dio 95 segundos de corrida para
# una lista que nadie ve. Los filtros trabajan sobre lo ya traído, sin otra
# llamada a la API.
MAX_CLIENTES = 25
MAX_DISTRITOS = 12


def _adaptar(fila: dict) -> dict:
    return {
        "ruc": fila.get("rucClient") or "",
        "codigo": fila.get("codeClient") or "",
        "razon_social": fila.get("nameClient") or "",
        "direccion": fila.get("address") or "",
        "distrito": fila.get("locality") or "",
        "email": fila.get("email") or "",
        "codigo_vendedor": fila.get("codeSeller") or "",
        "vendedor": fila.get("nameSeller") or "",
    }


def _distrito_corto(valor: str) -> str:
    """'SANTIAGO DE SURCO/LIMA/LIMA' -> 'SANTIAGO DE SURCO'."""
    return (valor or "").split("/")[0].strip()


async def cartera(vendedor_id: str, distrito: str | None = None,
                  buscar: str | None = None, limite: int = MAX_CLIENTES) -> dict:
    if not vendedor_id:
        return {"error": "SIN_VENDEDOR", "clientes": []}

    datos = await catusita_api.get("/api/client/CustomerbySeller",
                                   {"SellerId": vendedor_id})
    if isinstance(datos, dict) and datos.get("error"):
        return datos

    filas = [_adaptar(f) for f in (datos if isinstance(datos, list) else [])]
    total = len(filas)

    # `vendedor` y `codigo_vendedor` son el mismo valor en todas las filas: es la
    # cartera de un solo asesor. Van una vez arriba.
    vendedor_nombre = filas[0]["vendedor"] if filas else ""
    for f in filas:
        f.pop("vendedor", None)
        f.pop("codigo_vendedor", None)

    coincidentes = filas
    if distrito:
        d = distrito.lower()
        coincidentes = [c for c in coincidentes if d in (c["distrito"] or "").lower()]
    if buscar:
        b = buscar.lower()
        coincidentes = [c for c in coincidentes
                        if b in (c["razon_social"] or "").lower() or b in (c["ruc"] or "")]

    conteo: dict[str, int] = {}
    for c in coincidentes:
        d = _distrito_corto(c["distrito"])
        if d:
            conteo[d] = conteo.get(d, 0) + 1
    por_distrito = dict(sorted(conteo.items(), key=lambda x: -x[1])[:MAX_DISTRITOS])

    tope = max(1, min(int(limite or MAX_CLIENTES), 60))
    muestra = coincidentes[:tope]

    resultado = {
        "vendedor_id": vendedor_id,
        "vendedor_nombre": vendedor_nombre,
        "total_clientes": total,
        "coinciden": len(coincidentes),
        "por_distrito": por_distrito,
        "mostrados": len(muestra),
        "truncado": len(coincidentes) > len(muestra),
        "clientes": muestra,
    }
    return resultado


async def perfil(ruc: str) -> dict:
    datos = await catusita_api.get("/api/client/CustomerbyFilter",
                                   {"RUCClient": ruc})
    if isinstance(datos, dict) and datos.get("error"):
        return datos

    filas = datos if isinstance(datos, list) else []
    if not filas:
        return {"error": "NO_ENCONTRADO", "ruc": ruc}
    return _adaptar(filas[0])
