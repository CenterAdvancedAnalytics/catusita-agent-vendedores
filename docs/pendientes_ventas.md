# Pendientes — reportes de venta

Lo que los vendedores piden y hoy **no existe**, con lo que se midió sobre la API
real (`api.catusita.com:8092`) el 19/08/2026.

---

## 1. Acumulado de ventas por período → **es para supervisores**

> *«Catu, Acumulado de ventas agosto»* — Fernando, chat real
> *(incidencia 23, Rechazada)*

**No va en el agente de vendedores.** Un acumulado es una cifra de gestión, y el
canal donde vive es `supervisores`, no el WhatsApp del asesor.

Qué haría falta del lado de la API: un endpoint que devuelva el total vendido por
vendedor y período. Hoy no hay ninguno — los montos están por pedido y por
cliente, así que armar un acumulado obliga a recorrer la cartera entera y sumar.

Mientras tanto el agente lo dice y no estima. **Nunca sumar pedidos sueltos para
inventar un total**: sale un número plausible y equivocado, que es peor que no
dar ninguno.

---

## 2. Producto más vendido → **imposible con la API de hoy**

> *«Cual es el mas vendido y quienes lo han comprado en el último mes... lo ordenas
> de mayor a menor y colocas el precio que lo compraron»*

No es lento ni caro: **el dato no está**. Un pedido devuelve esto y nada más:

```
pedido_id  278395
fecha      2026-08-19T17:32:44
estado     PENDIENTE
monto      229.13
moneda     USD
cliente    BUENO RABANAL DE FLORES MARITZA
vendedor   Peña Alva Mariluz Milagros
documentos [factura, notas de crédito]
```

**Ningún endpoint de los diez devuelve las líneas de un pedido.** Sin saber qué
SKU tiene cada pedido, no hay forma de contar cuál se vendió más.

Qué haría falta: que la API exponga el detalle de líneas (SKU, cantidad, precio
unitario) por pedido o por período.

---

## 3. «A quiénes no le he vendido este año» → **funciona, pero tarda 17 s**

> *«Quiero saber a quienes no le he vendido este año»*

Medido sobre la cartera real de Peña Alva Mariluz (vendedor 22):

```
187 clientes · 187 consultas · 17.0 s · 0 errores

compraron en 2026    137
última compra antes   39
sin ningún pedido     11
─────────────────────────
sin vender            50 de 187
```

Sale de la cartera (1 llamada) más una consulta de pedidos **por cliente**. No hay
atajo: `SalesRepresentativeId` existe en `/api/sales/orders/documents` y **no
sirve** para esto — sin cliente devuelve `400: "Debe ingresar el código del
cliente o el RUC del cliente"`.

**Está sin conectar como tool.** Falta decidir cómo se entregan 17 segundos en un
chat de WhatsApp:

- avisar «dame un momento» y mandar el resultado después, o
- cachear la última compra por cliente y refrescarla de noche

### Ojo con el paralelismo

La primera medición dio **186 de 187 «sin ningún pedido»**, que era falso: la API
devolvía 500 y el código contaba cada error como «este cliente nunca compró».

`/api/sales/orders/*` **no tolera dos llamadas simultáneas**:

```
en serie    0 errores      2.03 s
de a 2     83% error 500   0.30 s
de a 6     96% error 500   0.10 s
```

Ya está resuelto con un lock en `catusita_api.py`, pero cualquier cosa que se
construya sobre estos endpoints tiene que contarlo: un 500 **no** es «no hay
datos».

---

## 4. Cartera completa como archivo

> *«Enviame mi cartera completa»* — Patricia, 216 clientes
> *(incidencia 22, Rechazada)*

Los datos están (la cartera sale en una llamada). Lo que falta es generar un
Excel o PDF y mandarlo por WhatsApp, igual que hoy se manda una factura.

El agente ya sabe enviar archivos —`media_pendiente` con `documento_base64`—, así
que es armar el archivo, no plomería nueva.
