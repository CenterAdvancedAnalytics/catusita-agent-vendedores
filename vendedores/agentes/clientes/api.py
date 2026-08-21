"""Los endpoints de cliente de Catusita. Una función por endpoint y nada más.

Cada función expone LOS MISMOS parámetros que declara el swagger, aunque hoy no
se usen todos. Así se ve de un vistazo qué sabe hacer la API, y agregar una
capacidad es cambiar quién la llama, no descubrir que existía.

    GET /api/client/CustomerbySeller   SellerId, CodeClient, RUCClient
    GET /api/client/CustomerbyFilter   CodeClient, RUCClient, NameClient

Ninguno acepta filtro por distrito ni localidad: eso hay que hacerlo sobre lo
ya traído.

`catusita_api.get()` saca los parámetros vacíos antes de mandar, así que pasar
None es seguro — y necesario, porque la API devuelve 500 ante un string vacío.
"""
from vendedores.plataforma_vendedores import catusita_api


async def customer_by_seller(seller_id: str | int,
                             code_client: str | None = None,
                             ruc_client: str | None = None) -> dict | list:
    """La cartera de un vendedor.

    Con `ruc_client` o `code_client` contesta si ESE cliente es de ESE vendedor
    —una fila o vacío— en una sola llamada, sin bajar la cartera entera.
    """
    return await catusita_api.get("/api/client/CustomerbySeller", {
        "SellerId": seller_id,
        "CodeClient": code_client,
        "RUCClient": ruc_client,
    })


async def customer_by_filter(code_client: str | None = None,
                             ruc_client: str | None = None,
                             name_client: str | None = None) -> dict | list:
    """Busca un cliente en TODA la base, sin importar de qué vendedor sea.

    No tiene scope de cartera: lo que devuelva hay que verificarlo contra la del
    asesor antes de mostrarlo.
    """
    return await catusita_api.get("/api/client/CustomerbyFilter", {
        "CodeClient": code_client,
        "RUCClient": ruc_client,
        "NameClient": name_client,
    })
