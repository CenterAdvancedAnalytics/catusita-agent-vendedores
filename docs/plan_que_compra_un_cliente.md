# Qué compra más un cliente — procedimiento propuesto

Borrador para revisar. **Nada de esto está cargado todavía**: el proceso entra al
RAG cuando esté aprobado, y antes tiene que existir la tool.

---

## Lo que se midió (20/08/2026)

```
cliente 10088058122
  10 pedidos → 12 facturas → 12 XML → 62 líneas      TOTAL 1.2 s
```

El dato **no está en el JSON de ningún endpoint**. Está en el XML de la factura
electrónica, que por ley SUNAT trae el detalle:

```xml
<cac:InvoiceLine>
  <cbc:InvoicedQuantity unitCode="GLL">3.000</cbc:InvoicedQuantity>
  <cbc:LineExtensionAmount currencyID="USD">62.42</cbc:LineExtensionAmount>
  <cac:Item>
    <cbc:Description>VALVOLINE ACEITE SAE10W30 SP CLASSIC 1GL+1/4</cbc:Description>
    <cac:SellersItemIdentification><cbc:ID>C1030</cbc:ID>
```

Esto también destraba el «producto más vendido» de `docs/pendientes_ventas.md`,
que estaba dado por imposible. Lo era en JSON; en XML no.

---

## La tool que hay que construir primero

Un proceso no puede consultar nada por sí solo. Antes va esto, en el área
`pedidos` (que es la dueña de `/api/sales/orders/*`):

    consultar_compras(cliente, criterio="monto", pedidos=20, top=8)

### Sus variables

| variable | qué es | default | por qué |
|---|---|---|---|
| `cliente` | RUC o nombre | — | se resuelve con `acceso.verificar`, como el resto del área |
| `criterio` | `monto` \| `unidades` | **a confirmar** | dan rankings DISTINTOS — ver abajo |
| `pedidos` | cuántos pedidos hacia atrás | **a confirmar** | 20 tardó 1.2 s |
| `top` | cuántos productos devolver | 8 | lo que entra en un WhatsApp |

### Lo que devolvería

```
{
  "cliente": "...", "pedidos_revisados": 10, "lineas": 62,
  "productos": [{sku, descripcion, unidades, monto, marca}],
  "marcas":    [{marca, monto, porcentaje}]
}
```

### Los tres saltos

```
/api/sales/orders/documents   los pedidos del cliente
/api/electronic-documents     por cada factura -> su xmlUrl
:8086/ICFacturaXML/*.XML      el XML, con urllib (el :8086 manda un header
                              con espacio en el nombre y httpx lo rechaza)
```

---

## El proceso, como iría al RAG

**proceso**

    Que compra mas un cliente

**descripcion** — es lo único que se embebe, con las palabras del asesor

    que le vendo mas a este cliente, que productos compra normalmente, que
    marca prefiere, cual es su producto habitual, que suele llevar, en que
    gasta mas este cliente, su historial de compras por producto

**procedimiento** — borrador, pendiente de las decisiones de abajo

    Usá el area pedidos con consultar_compras. Le pasas el cliente por nombre o
    RUC y ella lo resuelve dentro de la cartera del asesor.

    Devolve las dos cosas, que son la pregunta real detras:
      - los productos que mas compra, con SKU, unidades y monto
      - el reparto por marca, en porcentaje

    Deci SIEMPRE sobre cuantos pedidos se calculo. "Compra mas Valvoline" no
    significa nada sin "en sus ultimos 10 pedidos".

    NO lo presentes como una prediccion ni como una recomendacion de compra. Es
    lo que ya compro. Que el asesor decida que le ofrece.

    Si el cliente no tiene facturas con XML, decilo — no estimes a partir de los
    montos totales de los pedidos.

---

## Lo que necesito que confirmes

### 1. ¿Por monto o por unidades?

Dan resultados distintos y no es un detalle:

```
por monto      1º VALVOLINE ACEITE 5W30   $443.54   (18 unidades)
por unidades   1º NARVA FOCOS H7          60 u      ($187.71)
```

El foco es el 6º en plata y el 1º en cantidad. **Cuál de los dos es «lo que más
compra»** depende de para qué lo usa el asesor: para saber qué reponerle, las
unidades; para saber dónde está la plata, el monto.

Mi sugerencia: **devolver los dos** y que el orquestador muestre el de monto
primero. Cuesta lo mismo.

### 2. ¿Qué ventana?

`pedidos=20` tardó 1.2 s. Un cliente grande puede tener cientos.

- ¿Los últimos 20 pedidos? ¿El último año? ¿Todo?
- Un cliente que dejó de comprar hace dos años seguiría apareciendo con sus
  compras viejas si no se corta por fecha.

### 3. Las notas de crédito — **esto puede dar un número equivocado**

Si el cliente devolvió mercadería, hay una NC que anula esa venta. Hoy el cálculo
**la ignora**: cuenta la factura completa como si se hubiera vendido.

Un cliente que compró 100 filtros y devolvió 80 aparecería como si comprara 100.

Se puede restar —las NC vienen en `creditNotes` de cada documento— pero hay que
decidir si una NC parcial descuenta líneas o solo monto. **Necesito saber cómo
las emiten en Catusita.**

### 4. La marca: de dónde se saca

Hoy tomo la primera palabra de la descripción, y sale mal:

```
VALVOLINE   57.4%    ✓
NARVA       13.8%    ✓
ACEITE      12.0%    ✗  no es una marca
REFRIG       7.6%    ✗
TRANSF.      1.7%    ✗
```

Lo correcto es cruzar el SKU contra `/api/article/filter?ItemCode=`, que trae
`brandName` de verdad. Cuesta una llamada por SKU distinto (~15 por cliente).

¿Se hace en vivo, o se arma una tabla local de SKU→marca que se refresque de
noche?

### 5. ¿Quién puede ver esto?

Son las compras de un cliente. Hoy `acceso.verificar` ya garantiza que el asesor
solo consulte los suyos. ¿Alcanza, o hay algo más que no debería salir por
WhatsApp?
