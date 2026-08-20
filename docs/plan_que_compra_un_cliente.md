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

| variable | qué es | default | quién decide |
|---|---|---|---|
| `cliente` | RUC o nombre | — | el asesor. Se resuelve con `acceso.verificar`, como el resto del área |
| `criterio` | `monto` \| `unidades` | `monto` | el **agente**, según cómo preguntó — ver el procedimiento |
| `pedidos` | cuántos hacia atrás | `20` | el **agente**, si el asesor acota el período |
| `top` | cuántos productos devolver | `8` | fijo. Es lo que entra en un WhatsApp |

Ninguna queda «a confirmar»: `criterio` y `pedidos` los resuelve el agente
leyendo la consulta, y eso está escrito en el `procedimiento`. Lo que sí queda
pendiente son las tres decisiones del final, que son de negocio.

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

Sin nombres propios: lo van a usar los 48 con acceso, cada uno preguntando por
clientes distintos. Va la situación, no el caso.

    que le vendo mas a este cliente, que productos me compra normalmente, que
    suele llevar, cual es su producto habitual, que me compra siempre, en que
    gasta mas, cuales son sus productos mas frecuentes, que marca prefiere,
    que marca de aceite usa, que marca de filtros lleva, cuanto me compra de
    una marca, su historial de compras por producto, ranking de lo que compra,
    que le puedo ofrecer segun lo que ya compra, que compro el ultimo año

**procedimiento** — borrador, pendiente de las decisiones de abajo

    Usá el area pedidos con consultar_compras. Le pasas el cliente por nombre o
    RUC y ella lo resuelve dentro de la cartera del asesor.

    Devolve las dos cosas, que son la pregunta real detras:
      - los productos que mas compra, con SKU, unidades y monto
      - el reparto por marca, en porcentaje

    ── PREGUNTALE AL ASESOR, no elijas vos ────────────────────────────────

    Hay dos cosas que cambian el resultado y que solo el sabe:

    1. POR PLATA O POR CANTIDAD. No dan lo mismo — un foco puede ser el 6º en
       monto y el 1º en unidades. Si no lo aclaro, mostrá el de MONTO y decile
       en una linea que tambien lo podes ordenar por unidades.

       Solo preguntaselo si la respuesta cambiaria lo que va a hacer: si dijo
       "que le repongo", son unidades; si dijo "donde esta la plata", es monto.
       Si ya se entiende de como pregunto, no lo hagas escribir de nuevo.

    2. DESDE CUANDO. Por defecto son sus ultimos pedidos. Si el asesor dice
       "este año", "el ultimo mes" o "siempre", pasalo como viene.

    ── Lo que NO se negocia ───────────────────────────────────────────────

    Deci SIEMPRE sobre cuantos pedidos se calculo. "Compra mas Valvoline" no
    significa nada sin "en sus ultimos 10 pedidos".

    NO lo presentes como una prediccion ni como una recomendacion de compra. Es
    lo que YA compro. Que el asesor decida que le ofrece.

    Si el cliente no tiene facturas con XML, decilo — no estimes a partir de los
    montos totales de los pedidos.

    Si hubo devoluciones (notas de credito), avisalo: el calculo puede estar
    contando mercaderia que volvio.

---

## Lo que resuelve el propio agente, preguntándole al asesor

Dos de las decisiones no son tuyas: son del asesor y cambian según para qué
pregunta. Van en el `procedimiento` de arriba, no en una variable fija.

**Monto o unidades.** Dan rankings distintos:

```
por monto      1º VALVOLINE ACEITE 5W30   $443.54   (18 unidades)
por unidades   1º NARVA FOCOS H7          60 u      ($187.71)
```

El foco es 6º en plata y 1º en cantidad. Para reponerle stock importan las
unidades; para saber dónde está la plata, el monto. El agente muestra monto por
defecto y ofrece el otro — y solo repregunta si de verdad cambia lo que el
asesor va a hacer. Hacerlo escribir dos veces por algo que ya se entendía es
peor que elegir bien por él.

**Desde cuándo.** Por defecto los últimos pedidos; si el asesor dice «este año»
o «el último mes», se pasa como viene.

---

## Lo que necesito que confirmes vos

Estas tres no las puede decidir ni el agente ni el asesor.

### 1. Las notas de crédito — **esto puede dar un número equivocado**

Si el cliente devolvió mercadería, hay una NC que anula esa venta. Hoy el cálculo
**la ignora**: cuenta la factura completa como si se hubiera vendido.

Un cliente que compró 100 filtros y devolvió 80 aparecería como si comprara 100.

Se puede restar —las NC vienen en `creditNotes` de cada documento— pero hay que
decidir si una NC parcial descuenta líneas o solo monto. **Necesito saber cómo
las emiten en Catusita.**

### 2. La marca: de dónde se saca

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

### 3. ¿Quién puede ver esto?

Son las compras de un cliente. Hoy `acceso.verificar` ya garantiza que el asesor
solo consulte los suyos. ¿Alcanza, o hay algo más que no debería salir por
WhatsApp?

---

## Antes de cargarlo: medir, no suponer

Es el paso que ya nos ahorró un proceso mal cargado. Con la `descripcion` de
arriba, cada una de estas tiene que recuperar el proceso por encima de 0.50:

```
que le vendo mas a este cliente
que productos me compra normalmente
que suele llevar este cliente
que marca prefiere
que marca de aceite usa
cuanto me compra de Valvoline
que le puedo ofrecer a este cliente
```

Y estas NO tienen que recuperarlo — son consultas de otra cosa que comparten
palabras:

```
que pedidos tiene este cliente          -> area pedidos, normal
cuanto me debe este cliente             -> cobranzas
que productos hay en stock              -> area productos
quienes son mis clientes                -> area clientes
```

El riesgo concreto acá es el último grupo: «que productos…» aparece en los dos
lados. Si un «¿qué productos hay del filtro 33120?» recupera este proceso, el
agente va a buscar el historial de compras de un cliente que nadie nombró.

Si alguna falla, se corrige la `descripcion` — nunca se baja el umbral.

Y el corte fino no lo hace el coseno: el área `conocimiento` recibe el campo
`cubre` y descarta lo que no aplica. Eso ya está andando.
