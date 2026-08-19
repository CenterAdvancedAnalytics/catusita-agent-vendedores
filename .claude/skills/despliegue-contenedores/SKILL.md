---
name: despliegue-contenedores
description: Despliega y diagnostica los contenedores del agente Catusita en Railway (recepción y los tres workers). Crea servicios, sincroniza variables, verifica deployment triggers, revisa builds y logs, y hace el corte del webhook de WAHA. Usar cuando el usuario pida "desplegar", "crear el servicio", "subir a Railway", "levantar el agente", "cambiar el webhook", "por qué no arranca el contenedor", "por qué no se actualizó" o invoque /despliegue-contenedores.
---

# Skill — Despliegue de contenedores

## El mapa

Un repo, una rama, **cuatro imágenes**. Los cuatro servicios se despliegan del
MISMO commit; lo único que cambia es qué Dockerfile construyen.

```
repo    gabrielcz6/catusita-agent-vendedores
rama    arquitectura-vertical
```

| servicio | Dockerfile | variables | qué hace |
|---|---|---|---|
| `catusita-recepcion` | `recepcion/Dockerfile` | COMUNES + WAHA | uvicorn, **con** puerto. Recibe de WAHA, rutea por el padrón, acumula y responde |
| `catusita-vendedores` | `vendedores/Dockerfile` | COMUNES + OPENAI | worker: cola `vendedores` |
| `catusita-clientes` | `clientes/Dockerfile` | COMUNES + OPENAI | worker: cola `clientes` |
| `catusita-supervisores` | `supervisores/Dockerfile` | COMUNES + OPENAI | worker: cola `supervisores` |

```
COMUNES   DATABASE_URL  REDIS_URL  ANTHROPIC_API_KEY  SAP_BASE_URL  SAP_API_KEY
WAHA      WAHA_BASE_URL  WAHA_API_KEY  WAHA_SESSION  WAHA_WEBHOOK_TOKEN
OPENAI    OPENAI_API_KEY
```

Las variables NO son las mismas para todos, **y eso es la arquitectura**:
recepción lleva `WAHA_*` y no lleva `OPENAI`; los workers al revés. Un worker
con `WAHA_API_KEY` significa que alguien le hizo mandar un mensaje directo, y
ahí se rompe la única frontera de proceso del sistema.

Ninguno de los workers tiene puerto. Se hablan con recepción por Redis:

```
recepcion ──LPUSH "vendedores"──► Redis ──BRPOP──► worker vendedores
    ▲                                                    │
    └──────── BRPOP "respuestas:vendedores" ◄────────────┘
```

## El deployment trigger — leer esto antes de debuggear nada

**Vive en Railway, no en el repo.** No hay archivo que lo contenga: es una
suscripción al webhook de GitHub, del lado de ellos. El repo no puede crearla.

`railway add --repo --branch` a veces lo crea y a veces no. Cuando no lo crea,
el síntoma es el peor posible: **el servicio se despliega UNA vez —la del
alta— y después los push no hacen nada. Parece que anduvo.**

Ya nos mordió: dos servicios quedaron corriendo un commit viejo con un bug de
Redis mientras el arreglo estaba en GitHub sin que Railway se enterara.

**Verificarlo SIEMPRE que algo "no se actualizó":**

```bash
railway api 'query { project(id: "75ff9cea-3865-4381-907a-3adbc4e17c94") { deploymentTriggers { edges { node { branch repository serviceId } } } } }'
```

Tiene que haber uno por servicio, con `branch: arquitectura-vertical` y
`repository: gabrielcz6/catusita-agent-vendedores`.

Si falta:

```bash
railway api 'mutation { deploymentTriggerCreate(input: { branch: "arquitectura-vertical", repository: "gabrielcz6/catusita-agent-vendedores", provider: "github", projectId: "<PROJ>", environmentId: "<ENV>", serviceId: "<SVC>" }) { id } }'
```

Si dice `Only a single deployment trigger is allowed`, **ya existe** — no es un
error, es que `railway add` se adelantó.

Si dice `no one in the project has access to it`, la GitHub App de Railway no
está autorizada sobre ese repo. Por eso el repo se mudó de la organización
`CenterAdvancedAnalytics` a `gabrielcz6`: en la cuenta personal se autoriza con
un click, en la organización hace falta un admin.

## Crear un servicio

```bash
railway add --service catusita-clientes \
  --repo gabrielcz6/catusita-agent-vendedores \
  --branch arquitectura-vertical \
  -v "RAILWAY_DOCKERFILE_PATH=clientes/Dockerfile" \
  -v "DATABASE_URL=..." -v "REDIS_URL=..." ...
```

Las variables se copian de `catusita-agent`, que las tiene todas:

```bash
railway variables -s catusita-agent --json
```

Excepto `OPENAI_API_KEY`, que se agregó después y sale del `.env` local.

**Root Directory va VACÍO.** Lo que se configura es el Dockerfile Path. Los
Dockerfiles copian `requirements.txt` y su carpeta con rutas desde la raíz del
repo; con `vendedores/` como root, el build falla.

## Verificar que arrancó

```bash
railway deployment list -s catusita-vendedores --json    # status + commit
railway logs -s catusita-vendedores                      # el proceso
```

El worker imprime su inventario al arrancar, y hay que leerlo con atención:

```
worker de clientes · cola=clientes · 2 de 5 áreas atienden
    productos        ...
    vehiculos        ...   (sin subgrafo, no se delega)
```

`2 de 5` no es un error: hay 5 carpetas y 2 con subgrafo compilado. El registro
solo ofrece áreas con NODO, así que el orquestador ni ve las otras.

En recepción:

```bash
curl https://<dominio>/health
{"status":"ok","padron":{"vendedores":38},"sesiones":{...}}
```

Un multiagente con `padron` en cero es un contenedor que no arrancó — y sus
usuarios están entrando como `clientes` en ese momento.

## Cuando un deploy falla sin dejar logs

Pasa. El mismo commit puede fallar en un servicio y buildear en los otros tres.
Reintentar antes de investigar:

```bash
railway api 'mutation { serviceInstanceRedeploy(environmentId: "<ENV>", serviceId: "<SVC>") }'
```

(`railway deployment redeploy` no sirve si el último falló: dice
`cannot be redeployed`.)

Mientras tanto el deployment anterior sigue vivo — Railway no lo baja hasta que
el nuevo tenga éxito. Un `FAILED` no es una caída.

## Antes de tocar el servicio `waha`

Verificar que tenga volumen. Sin volumen persistente, un reinicio pierde la
sesión de WhatsApp y hay que re-escanear el QR:

```bash
railway variables -s waha --json      # tiene que estar RAILWAY_VOLUME_MOUNT_PATH
```

Hoy está en `/app/.sessions` y por eso los reinicios son seguros.

## El corte del webhook

**Es un paso aparte y nunca se hace junto con otra cosa.** Solo cuando el worker
ya se probó, y avisando al usuario:

```bash
railway variables -s waha --set "WHATSAPP_HOOK_URL=https://<recepcion>/webhook/waha"
```

Para volver atrás: la misma variable apuntando a `catusita-agent`. Es una
variable, se revierte en un minuto.

Después del corte, confirmar con un WhatsApp real y mirar los logs de
recepción. Si aparece `401 token inválido`, revisar que
`WHATSAPP_HOOK_CUSTOM_HEADERS` en `waha` (formato `X-Api-Key:<token>`) coincida
con `WAHA_WEBHOOK_TOKEN` en recepción.

**El orden importa al rotar ese token**: primero WAHA empieza a mandarlo,
después el servicio lo exige. Al revés hay una ventana donde todo mensaje se
pierde con 401.

## Probar un worker SIN tocar producción

Metiendo un Turno a mano en su cola y leyendo la respuesta:

```python
from vendedores.plataforma_vendedores.contrato import Turno, empacar, desempacar
# LPUSH "vendedores" con empacar(Turno(...))
# BRPOP "respuestas:vendedores" -> desempacar
```

Si eso funciona, al worker solo le falta tráfico real.

## Las migraciones NO se despliegan

Nadie las corre al arrancar. Se aplican a mano, desde desarrollo, antes de
desplegar. Hay una sola base y un solo esquema para los tres multiagentes; la
tabla `migraciones` registra cuáles ya corrieron.

Razón: los tres workers arrancan juntos en cada deploy, y tres procesos
haciendo `ALTER TABLE` sobre la misma base es la carrera que aparece una vez
cada veinte deploys. Además, el que atiende WhatsApp no debería poder borrar
una tabla.

## Watch paths

Puestos. Cada servicio se reconstruye solo si cambió lo suyo:

```
catusita-recepcion      recepcion/**       + requirements.txt
catusita-vendedores     vendedores/**      + requirements.txt
catusita-clientes       clientes/**        + requirements.txt
catusita-supervisores   supervisores/**    + requirements.txt
```

`requirements.txt` va en los cuatro a propósito: es lo único compartido, y sin
él agregar una librería no dispararía ningún build — los contenedores seguirían
con la imagen vieja sin que nadie lo note.

Un push que no toca la carpeta de un servicio le deja el deployment en
`SKIPPED`, que es Railway diciendo «vi el push, no es mío». No es un error.

Para cambiarlos:

```bash
railway api 'mutation { serviceInstanceUpdate(serviceId: "<SVC>", environmentId: "<ENV>", input: { watchPatterns: ["vendedores/**", "requirements.txt"] }) }'
```

## Servicios muertos que siguen encendidos

`evolution-api` (el canal anterior a WAHA) y `mock-sap-catusita` (hoy
`SAP_BASE_URL` apunta a `tools-agente-catusita`, el real). Los dos gastan plata.
Borrar `evolution-api` necesita permisos de Carlos Gamero.

## IDs que se usan seguido

```
proyecto      75ff9cea-3865-4381-907a-3adbc4e17c94   (agent-catu)
environment   8d9ac4cc-7dbf-4da5-8ed8-ebc0d09dc382   (production)
```

Los serviceId se sacan de `railway status --json`.
