"""El único endpoint que `acceso` necesita: la cartera del asesor.

    GET /api/client/CustomerbySeller?SellerId={id}

Está separado del backend del área `clientes` a propósito, aunque llame al mismo
endpoint. Si `acceso` importara el backend del área, la política de seguridad del
multiagente dependería de un área — y cambiar esa área podría romper el control
de acceso de las otras dos sin que nadie lo note.

── `SellerId` es el `vendedor_id` de la tabla `vendedores` ────────────────────

Sale derecho, sin traducir: la columna `vendedor_id` ya guarda el número de SAP
('22', '50', '58'), y la API lo confirma devolviendo el mismo nombre y el mismo
`codeSeller` que la columna `codigo`.

Los `V001`, `V002` que aparecen en `auth` con `USE_AUTH_MOCK=true` NO son eso:
son etiquetas del sandbox. Contra la API real caen en el bucket 'Gerencia
Oficina' y devuelven 3589 clientes — o sea, la cartera de nadie. Un asesor
autenticado por sandbox tiene acceso a todo; en producción `USE_AUTH_MOCK` va
en false y por eso importa.
"""
from vendedores.plataforma_vendedores import catusita_api


def _adaptar(fila: dict) -> dict:
    """La fila de la API con los nombres que usa el resto del multiagente.

    `acceso` busca por `ruc` y `razon_social`; renombrar acá es lo que permite
    que cambiar de API no obligue a tocar el control de acceso.
    """
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


async def cartera(vendedor_id: str) -> dict:
    """Clientes asignados al asesor. Lanza si falla: quien llama decide.

    `acceso.py` atrapa la excepción y falla cerrado. Devolver `{}` en silencio
    haría que una caída del backend se viera igual que «no tenés clientes», y
    eso autorizaría de menos sin que nadie entienda por qué.
    """
    if not vendedor_id:
        return {"clientes": []}

    datos = await catusita_api.get("/api/client/CustomerbySeller",
                                   {"SellerId": vendedor_id})

    # El contrato de esta función es lanzar ante un fallo, no devolver un error
    # blando: `acceso` distingue «falló» de «no tiene clientes» por la excepción.
    if isinstance(datos, dict) and datos.get("error"):
        raise RuntimeError(f"cartera de {vendedor_id}: {datos['error']}")

    filas = datos if isinstance(datos, list) else []
    return {
        "vendedor_id": vendedor_id,
        "vendedor_nombre": (filas[0].get("nameSeller") if filas else "") or "",
        "total_clientes": len(filas),
        "clientes": [_adaptar(f) for f in filas],
    }
