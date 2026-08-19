"""Acceso de `clientes` (vendedores) a la API de Catusita.

    GET /api/client/CustomerbySeller?SellerId=   la cartera del asesor
    GET /api/client/CustomerbyFilter?RUCClient=  el perfil de uno

Ojo: `plataforma_vendedores/backend_cartera.py` llama al mismo primer endpoint.
No es un descuido — está duplicado a propósito. Aquel lo usa el control de
acceso, que es una política del multiagente; si dependiera de este archivo, un
cambio en esta área podría romper el control de acceso de `pedidos` y
`facturacion` sin que nadie lo note.

── Lo que la API NO trae, y antes parecía que sí ──────────────────────────────

La cartera devuelve identificación y ubicación: RUC, razón social, dirección,
distrito, email y a qué vendedor pertenece. Nada más.

No hay estado (activo/suspendido/bloqueado), no hay tipo (taller/distribuidor),
no hay límite de crédito, no hay saldo ni fecha de última compra. Los filtros
`estado` y `tipo` de la tool no tienen contra qué filtrar, así que se declaran
no soportados en la respuesta en vez de ignorarse en silencio: un filtro que se
ignora hace que el asesor crea que pidió «solo los activos» y reciba todos.

Para saber cuándo compró un cliente hay que ir a `pedidos` — un pedido por vez.
"""
from vendedores.plataforma_vendedores import catusita_api


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


async def cartera(vendedor_id: str, estado: str | None = None,
                  tipo: str | None = None) -> dict:
    if not vendedor_id:
        return {"error": "SIN_VENDEDOR", "clientes": []}

    datos = await catusita_api.get("/api/client/CustomerbySeller",
                                   {"SellerId": vendedor_id})
    if isinstance(datos, dict) and datos.get("error"):
        return datos

    filas = datos if isinstance(datos, list) else []
    resultado = {
        "vendedor_id": vendedor_id,
        "vendedor_nombre": (filas[0].get("nameSeller") if filas else "") or "",
        "total_clientes": len(filas),
        "clientes": [_adaptar(f) for f in filas],
    }

    # Se avisa; no se filtra. Ver la nota del encabezado.
    pedidos_no_soportados = [n for n, v in (("estado", estado), ("tipo", tipo)) if v]
    if pedidos_no_soportados:
        resultado["filtros_ignorados"] = {
            "campos": pedidos_no_soportados,
            "mensaje": ("La cartera no trae estado ni tipo de cliente, así que "
                        "no se pudo filtrar. Esta es la cartera completa: "
                        "aclarale al usuario que el filtro no se aplicó."),
        }
    return resultado


async def perfil(ruc: str) -> dict:
    datos = await catusita_api.get("/api/client/CustomerbyFilter",
                                   {"RUCClient": ruc})
    if isinstance(datos, dict) and datos.get("error"):
        return datos

    filas = datos if isinstance(datos, list) else []
    if not filas:
        return {"error": "NO_ENCONTRADO",
                "mensaje": f"No hay ningún cliente con RUC {ruc}."}
    return _adaptar(filas[0])
