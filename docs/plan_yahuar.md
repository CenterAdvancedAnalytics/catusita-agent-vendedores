# Plan — el servicio Yahuar

Cómo devolverle al agente la consulta de placas, que hoy funciona en el stack
viejo y **no existe** en la arquitectura vertical.

---

## Qué es Yahuar

No es una API. Es un **número de WhatsApp** (`51977504279`) al que se le manda una
placa por chat y contesta con los datos del vehículo y una foto de la tarjeta de
identificación vehicular.

SUNARP se eliminó antes: tardaba 20-60 s, se colgaba seguido, y encima no
devolvía marca/modelo/año como campos — había que leerlos igual de una foto.

---

## Por qué no se puede portar tal cual

En `shared/yahuar.py`, esta línea es todo el problema:

```python
PENDING_KEY = "yahuar:pendiente"   # UNA sola clave, global
```

Solo puede haber **una consulta en vuelo**. Si un segundo vendedor pregunta
mientras la primera está pendiente, la clave se pisa: la respuesta del primero se
va al segundo, o se pierde.

En el monolito zafaba —un proceso, placas esporádicas—. En la arquitectura nueva
hay **tres contenedores** y los tres pueden preguntar.

Y no es un problema de implementación, es del recurso:

> **Yahuar es una sola conversación de WhatsApp.** Las respuestas vuelven todas
> por el mismo hilo, sin ningún identificador que las correlacione. Con dos
> consultas en vuelo, no hay forma de saber cuál respuesta es de quién.

No es que *convenga* un dueño único. Es que **no puede haber más de uno**.

---

## El agujero que hay que tapar sí o sí

**Recepción no sabe qué es Yahuar.** Su número no está en ningún padrón, así que
`router.py` lo manda a `POR_DEFECTO = "clientes"`:

```
Yahuar contesta con los datos del vehículo
   → recepción no lo reconoce
   → lo rutea al agente de CLIENTES
   → el agente le contesta a Yahuar como si fuera un cliente preguntando
   → el vendedor que pidió la placa nunca recibe nada
```

Esto pasa el día del corte del webhook, sin falta. Es la parte del plan que no se
puede dejar para después.

---

## El diseño

```
vendedores/vehiculos ─┐
                      ├─► "yahuar:solicitudes" ─► servicio yahuar ─► WhatsApp a Yahuar
clientes/vehiculos ───┘                                 ▲                    │
                                                        │                    ▼
      la tool espera en "yahuar:resultado:{placa}" ◄─────┤            Yahuar responde
                                                        │                    │
                                       "yahuar:entrantes" ◄── recepción ◄────┘
                                                          (lo intercepta ANTES del padrón)
```

Un contenedor más, sin puerto, igual que los tres workers. Se habla con todos por
Redis y con nadie más.

---

## Las piezas

### 1. Recepción intercepta

En `recepcion/router.py`, **antes** de consultar el padrón:

```python
if numero in ids_de_yahuar():      # el número fijo + el LID aprendido
    return Destino("yahuar", numero, "respuesta del relay de placas")
```

Y en `main.py`, ese destino hace `LPUSH yahuar:entrantes` en vez de encolar un
`Turno` normal. No pasa por el acumulador ni por el lock de conversación: no es
un usuario, es un servicio contestando.

### 2. El servicio `yahuar/`

Dueño único de la conversación. Un loop:

```
BRPOP yahuar:solicitudes  ──►  mandar "Placa vehicular: XXX" a Yahuar
                          ──►  juntar sus mensajes de yahuar:entrantes
                          ──►  procesar
                          ──►  SETEX yahuar:resultado:{placa}
```

**Una a la vez.** Ese es el punto del servicio: mientras atiende una placa no
saca la siguiente de la cola.

Adentro va todo lo que ya se aprendió a los golpes en el stack viejo:

**Debounce de 7 s.** Yahuar no contesta un mensaje: manda 3 o 4 —saludo, datos,
foto—. Hay que juntarlos y esperar 7 s de silencio antes de procesar. Sin esto se
le reenvía al vendedor el primer pedazo y se descartan los otros.

**Filtro de aclaraciones (incidencia 21).** Yahuar arranca con «¿qué información
buscas?» o «soy Yahuar, ¿en qué te puedo ayudar?». Eso NO se reenvía: se le
auto-responde «Placa vehicular» y se sigue esperando. La lista de frases está en
`webhooks/whatsapp.py::_ACLARACION_YAHUAR` y hay que llevársela entera.

**Aprendizaje del LID.** WhatsApp identifica a Yahuar con un LID distinto de su
teléfono, y cambia. El código viejo lo aprende solo: si llega un mensaje de un
número desconocido mientras hay una consulta pendiente, ese es Yahuar
(`yahuar:lid`, sin expiración). Sin esto el servicio no reconoce sus propias
respuestas.

**Visión.** La tarjeta llega como foto. Se pasa por Claude con visión
(`shared/llm.py::extraer_texto_de_imagen`) y la instrucción de
`_INSTRUCCION_PLACA`, que pide placa, marca, modelo, año, color, VIN, motor,
categoría, combustible y propietario como lista clave: valor.

**Errores.** Frases como «creo que escribiste», «no encontré», «no existe» →
`NO_ENCONTRADO`, con el mensaje de que verifique la placa. Están en
`_ERRORES_YAHUAR`.

**La foto también va al vendedor**, además del texto. Se devuelve en el resultado
para que la tool la ponga en `media_pendiente`.

### 3. La tool — no se toca

`vendedores/agentes/vehiculos/tools.py::consultar_placa` ya está escrita para
esto: encolar y esperar en `yahuar:resultado:{placa}` con `ESPERA_MAX = 90`.

Hoy devuelve un `NO_DISPONIBLE` explicando que el servicio no está desplegado.
Cuando exista, se reemplaza ese return por el encolar-y-esperar.

`clientes/agentes/vehiculos/tools.py` sigue en `NotImplementedError`, pero el
área **no es delegable** (no tiene `NODO`), así que el orquestador de clientes ni
la ve. Se conecta cuando se decida darle placas a los clientes.

---

## Tiempos

| qué | cuánto |
|---|---|
| Yahuar en contestar | 30-60 s |
| debounce después del último mensaje | 7 s |
| visión sobre la foto | 2-5 s |
| **lo que espera la tool** | `ESPERA_MAX = 90 s` |

Con la cola, tres placas pedidas a la vez son ~3 minutos para la última. No hay
forma de acelerarlo: el cuello es Yahuar, que es una persona o un bot atendiendo
un WhatsApp.

**Vale la pena decírselo al vendedor.** La tool ya lo declara en su docstring
(«TARDA 30-60 SEGUNDOS») y el área lo repite en su `DESCRIPCION`.

---

## Orden de construcción

1. **El intercepto en recepción.** Es lo que evita que la respuesta de Yahuar
   entre al agente de clientes. Va primero aunque el servicio todavía no exista:
   sin servicio el mensaje se descarta, que es mucho mejor que contestarle.
2. **El servicio, con el relay y el debounce**, sin visión. Se prueba que la ida
   y la vuelta funcionen con el texto suelto.
3. **La visión**, que es lo que convierte la foto en datos legibles.
4. **Conectar la tool** — reemplazar el `NO_DISPONIBLE` por el encolar-y-esperar.
5. **El Dockerfile y el servicio en Railway**, con su watch path `yahuar/**`.

Los pasos 1 a 4 se pueden probar sin desplegar nada, metiendo mensajes a mano en
`yahuar:entrantes` como ya se hizo con los `Turno`.

---

## Lo que hay que decidir

- **¿El servicio manda la foto al vendedor, o la manda el worker?** Hoy el
  resultado viaja por Redis y la tool lo pone en `media_pendiente`, que es el
  camino normal de las fotos de producto. Conviene mantenerlo así: el servicio
  Yahuar no debería tener credenciales de WAHA para escribirle a un vendedor.
  Con eso, `yahuar` solo habla con UN número —el de Yahuar— y esa es una frontera
  que se defiende sola.

- **¿Qué pasa si Yahuar no contesta nunca?** La tool corta a los 90 s. El
  servicio tiene que soltar la consulta y pasar a la siguiente, no quedarse
  esperando para siempre — si no, una placa que Yahuar ignora tapa la cola.

- **¿Se guarda el resultado en el historial?** El stack viejo lo hace a mano
  (`context.save_message` + `vendedores_chat.guardar`) porque el relay iba por
  fuera del flujo normal. Con la tool devolviendo el dato al orquestador esto ya
  no hace falta: el turno se guarda solo, como cualquier otro.
