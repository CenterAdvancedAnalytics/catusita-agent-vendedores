import os
import httpx
from dotenv import load_dotenv

load_dotenv()

SAP_BASE_URL = os.getenv("SAP_BASE_URL", "https://mock-sap-catusita-production.up.railway.app")
SAP_API_KEY = os.getenv("SAP_API_KEY", "catusita-mock-key-2024")


class SAPClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=SAP_BASE_URL,
            headers={"X-API-Key": SAP_API_KEY},
            timeout=10.0,
        )

    async def _get(self, path: str, params: dict = None, timeout: float = None) -> dict:
        """Helper para GET con manejo de errores uniforme.

        `timeout` permite sobreescribir el timeout por defecto del cliente
        (útil para endpoints lentos).
        """
        try:
            kwargs = {"params": params}
            if timeout is not None:
                kwargs["timeout"] = timeout
            resp = await self._http.get(path, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"error": f"No encontrado: {path}"}
            return {"error": f"Error del servidor SAP: {e.response.status_code}"}
        except httpx.TimeoutException:
            return {"error": "La consulta tardó demasiado. Inténtalo de nuevo en un momento."}
        except httpx.RequestError:
            return {"error": "No se pudo conectar al servidor SAP. Inténtalo en unos minutos."}

    async def get_stock(self, sku_code: str) -> dict:
        return await self._get(f"/stock/{sku_code}")

    async def get_precios(self, sku_code: str, tipo: str = None) -> dict:
        params = {"tipo": tipo} if tipo else None
        return await self._get(f"/precios/{sku_code}", params=params)

    async def get_pedidos(self, cliente_ruc: str, estado: str = None, limite: int = None) -> dict:
        params = {}
        if estado:
            params["estado"] = estado
        if limite:
            params["limite"] = limite
        return await self._get(f"/pedidos/{cliente_ruc}", params=params or None)

    async def get_despacho(self, pedido_id: str = None, factura: str = None) -> dict:
        params = {}
        if pedido_id:
            params["pedido_id"] = pedido_id
        if factura:
            params["factura"] = factura
        return await self._get("/despacho", params=params or None)

    async def get_imagen(self, sku: str) -> dict:
        """Foto(s) de un producto ya descargadas (base64). Timeout largo: descarga imágenes."""
        return await self._get(f"/imagen/{sku}", timeout=30.0)

    async def get_documento(self, numero: str, tipo: str = "01", empresa: str = "04") -> dict:
        """Factura o NC en PDF ya descargado (base64). Requiere N° + tipo (01/07) +
        empresa (04/01). Timeout largo: descarga el PDF del server 8086."""
        params = {"numero": numero, "tipo": tipo, "empresa": empresa}
        return await self._get("/documento", params=params, timeout=30.0)

    async def get_documento_pagos(self, numero: str, tipo: str = "01",
                                  empresa: str = "04", cliente: str = None) -> dict:
        """Cómo se pagó/canceló un documento: estado de deuda, letras (canje), notas
        de crédito y detalle de pagos. Requiere N° SUNAT + tipo (01/07) + empresa."""
        params = {"numero": numero, "tipo": tipo, "empresa": empresa}
        if cliente:
            params["cliente"] = cliente
        return await self._get("/documento/pagos", params=params, timeout=20.0)

    async def get_cliente(self, ruc: str) -> dict:
        return await self._get(f"/clientes/{ruc}")

    async def get_vehiculo(self, placa_o_vin: str) -> dict:
        """Datos de catálogo de un vehículo por placa/VIN. Lo usa el buscador
        para filtrar repuestos compatibles."""
        return await self._get(f"/vehiculo/{placa_o_vin}")

    async def get_catalogo(self, q: str = None, categoria: str = None,
                            marca: str = None, con_stock: bool = None) -> dict:
        params = {}
        if q:
            params["q"] = q
        if categoria:
            params["categoria"] = categoria
        if marca:
            params["marca"] = marca
        if con_stock is not None:
            params["con_stock"] = str(con_stock).lower()
        return await self._get("/catalogo", params=params or None)

    async def get_cartera_vendedor(self, vendedor_id: str,
                                    estado: str = None, tipo: str = None) -> dict:
        params = {}
        if estado:
            params["estado"] = estado
        if tipo:
            params["tipo"] = tipo
        return await self._get(f"/vendedor/{vendedor_id}/clientes", params=params or None)


sap = SAPClient()
