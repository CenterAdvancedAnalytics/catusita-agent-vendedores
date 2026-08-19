"""La cola de vendedores. Una sola.

    webhook ──Turno──► "vendedores" ──► worker de vendedores

── Por qué las áreas NO tienen cola ───────────────────────────────────────────

Porque el orquestador las controla. Son nodos del MISMO grafo, en el MISMO
proceso, y las invoca con `Command(goto="productos")` — un salto en memoria.

Poner Redis entre el orquestador y un área obligaría a serializar el estado y a
ejecución durable con checkpoints, solo para no bloquear un worker — cuando en
asyncio el orquestador no bloquea nada mientras espera. Se pagaría complejidad
para romper `Command(goto=...)`, que es el mecanismo con el que coordina.

── La única frontera de proceso ───────────────────────────────────────────────

Entre el webhook y este worker. El webhook tiene que contestarle a WAHA en
milisegundos y una corrida del agente tarda segundos: en línea, WAHA reintentaría
por timeout y dispararía la corrida de nuevo.
"""
MULTIAGENTE = "vendedores"

# Se declara acá y en ningún otro lado. Si estuviera también en el Dockerfile,
# el modo de fallar sería el peor posible: el webhook encolando en un nombre y
# el worker escuchando otro, sin un solo error, con los turnos apilándose.
COLA = "vendedores"

# Adonde el worker deja la respuesta para que el webhook la envíe.
COLA_RESPUESTAS = "respuestas:vendedores"

# El servicio de Railway que levanta este worker. Su watch path es
# `vendedores/** + requirements.txt`: un cambio acá lo reconstruye a él y a
# nadie más.
SERVICIO = "catusita-vendedores"


def problemas() -> list:
    """Chequeos para el arranque. Vacío = todo bien.

    Un área que todavía declare COLA quedó de cuando había una cola por área:
    nadie la lee, así que sus encargos no llegarían a ningún lado.
    """
    import importlib
    import pkgutil
    from pathlib import Path

    fallos = []
    d = Path(__file__).parent.parent / "agentes"
    for info in pkgutil.iter_modules([str(d)]):
        if not info.ispkg:
            continue
        try:
            m = importlib.import_module("vendedores.agentes." + info.name)
        except Exception as e:
            fallos.append(f"vendedores/agentes/{info.name} no importa: {e}")
            continue
        if getattr(m, "COLA", None):
            fallos.append(
                f"vendedores/agentes/{info.name} declara COLA: las áreas no tienen "
                f"cola, las invoca el orquestador en proceso"
            )
    return fallos
