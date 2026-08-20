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


# Cuántos clientes se le muestran al modelo cuando no filtró nada.
#
# ── Por qué hay un tope y por qué es este ────────────────────────────────────
#
# Devolver la cartera entera costaba 95 SEGUNDOS. Medido, con 187 clientes:
#
#     backend.cartera()        57.691 chars (~14.400 tokens) ->  0.1s de API
#     el modelo del área       leyó eso y escribió una tabla  -> 95s
#     lo que llegó al asesor   162 chars
#
# O sea: minuto y medio generando una tabla markdown de 187 filas que NADIE VE,
# porque a WhatsApp solo va la respuesta final del orquestador.
#
# 25 es lo que entra en un mensaje de WhatsApp sin que el asesor deje de leer.
# Para el resto están los filtros: son datos que la API ya trajo, no hay una
# segunda llamada.
MAX_CLIENTES = 25

# Cuántos distritos se resumen. Con más, el resumen vuelve a ser una lista.
MAX_DISTRITOS = 12


def _distrito_corto(valor: str) -> str:
    """'SANTIAGO DE SURCO/LIMA/LIMA' -> 'SANTIAGO DE SURCO'."""
    return (valor or "").split("/")[0].strip()


async def cartera(vendedor_id: str, distrito: str | None = None,
                  buscar: str | None = None, limite: int = MAX_CLIENTES,
                  estado: str | None = None, tipo: str | None = None) -> dict:
    """Cartera del asesor: un resumen y una muestra, no un volcado.

    La API devuelve los 187 de una sola vez y en 0.1s, así que los filtros se
    aplican acá sobre lo ya traído — no cuestan otra llamada.
    """
    if not vendedor_id:
        return {"error": "SIN_VENDEDOR", "clientes": []}

    datos = await catusita_api.get("/api/client/CustomerbySeller",
                                   {"SellerId": vendedor_id})
    if isinstance(datos, dict) and datos.get("error"):
        return datos

    filas = [_adaptar(f) for f in (datos if isinstance(datos, list) else [])]
    total = len(filas)

    # Cada fila repite `vendedor` y `codigo_vendedor` con el MISMO valor: es la
    # cartera de un solo asesor. Van una vez arriba y se sacan de las filas.
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

    # Dónde están sus clientes. Es la pregunta que sigue casi siempre («¿a
    # quiénes le vendo en Surco?») y resumirla cuesta nada.
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
        "clientes": muestra,
    }

    if len(coincidentes) > len(muestra):
        resultado["mensaje"] = (
            f"Tiene {len(coincidentes)} clientes y acá van {len(muestra)}. "
            "MOSTRALOS: razón social + RUC de cada uno, que es lo que necesita "
            "para pedirte después sus pedidos o su factura. Sin el RUC te lo "
            "va a tener que volver a preguntar.\n"
            "Después decí el total y ofrecele filtrar por distrito o buscar por "
            "nombre. Lo que NO podés es inventar ni completar los que faltan: "
            "no los tenés.")

    if distrito and not coincidentes:
        resultado["mensaje"] = (
            f"Ningún cliente de su cartera está en '{distrito}'. Los distritos "
            "donde sí tiene están en `por_distrito` de una consulta sin filtro.")

    # Se avisa; no se filtra. Ver la nota del encabezado.
    no_soportados = [n for n, v in (("estado", estado), ("tipo", tipo)) if v]
    if no_soportados:
        resultado["filtros_ignorados"] = {
            "campos": no_soportados,
            "mensaje": ("La cartera no trae estado ni tipo de cliente, así que "
                        "no se pudo filtrar por eso. Aclaráselo al usuario."),
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
