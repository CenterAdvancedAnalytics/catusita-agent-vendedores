# código obsoleto

El stack anterior a la arquitectura vertical. **No se borró todavía porque tres
archivos de acá siguen vivos** — ver más abajo.

Nada de esto atiende WhatsApp. El corte del webhook (20/08/2026) mandó todo el
tráfico a `recepcion/`, y desde entonces `webhooks/whatsapp.py` no recibe un
solo mensaje.

## Qué hay

| carpeta | qué era | estado |
|---|---|---|
| `webhooks/` | el webhook viejo: recibía de WAHA y corría el agente | muerto |
| `orchestrator/` | el grafo de un solo agente con todas las tools sueltas | muerto salvo `context.py` |
| `agents/` | las funciones de negocio del monolito (stock, precios, pedidos…) | muerto |
| `shared/` | `sap_client` (el wrapper), `kapso`, `waha`, `llm`, `yahuar` | muerto salvo `auth.py` |
| `descargas_sueltas/` | un PDF y un xlsx que estaban en una carpeta llamada `migraciones/` sin ser migraciones | basura |
| `qa_runner_graph.py` | script de pruebas del grafo viejo | muerto |

## Lo que TODAVÍA se usa — no tocar

```
dashboard/panel.py  ──►  codigo_obsoleto/orchestrator/context.py
                    ──►  codigo_obsoleto/shared/auth.py
main.py             ──►  codigo_obsoleto/webhooks/whatsapp.py   (solo para montar el router)
```

`catusita-agent` sirve `/api/panel/*`, que es **lo único que alimenta al
dashboard** (`catu-panel`, el front en React, le pega ahí — la URL está clavada
en su bundle).

O sea: de las ~3.900 líneas de esta carpeta, **tres archivos están vivos**. El
resto es peso muerto que no se puede sacar sin desacoplar el panel primero.

## Para poder borrar todo esto

1. Mover `context.py` y `auth.py` a donde los necesite el panel — o mejor, que
   el panel lea el historial de `chat_messages_vendedores` (Postgres) en vez de
   Redis, que es donde ya está persistido.
2. Sacar el router de `webhooks` de `main.py`.
3. Ahí sí: borrar esta carpeta entera y apagar `catusita-agent`.

## Servicios de Railway que dependen de esto

- `catusita-agent` — el único que construye este código. Sigue prendido **solo
  por el panel**.
- `tools-agente-catusita` — el wrapper que usaba `shared/sap_client.py`. El
  stack nuevo ya no lo llama: las áreas hablan directo con
  `api.catusita.com:8092`. Se puede apagar.
- `mock-sap-catusita` — el mock de la etapa anterior. Nadie le pega.
- `evolution-api` — el canal previo a WAHA. Borrarlo necesita permisos de
  Carlos Gamero.
