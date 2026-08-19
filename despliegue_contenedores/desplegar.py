"""Crea y sincroniza los contenedores en Railway.

    python -m despliegue_contenedores.desplegar          # muestra qué haría
    python -m despliegue_contenedores.desplegar --aplicar

Sin `--aplicar` no toca nada: imprime el plan y sale. Es a propósito — esto
crea infraestructura que cuesta plata y toca un proyecto con clientes escribiendo.

── Qué NO hace ────────────────────────────────────────────────────────────────

No cambia el webhook de WAHA. Crear los servicios y hacer el corte son dos
cosas distintas, y mezclarlas convierte un `--aplicar` distraído en una caída de
producción.

Los servicios nuevos arrancan, se conectan a su cola y esperan. Nadie les manda
nada hasta que se cambie `WHATSAPP_HOOK_URL` a mano, mirando lo que se hace.

── Es idempotente ─────────────────────────────────────────────────────────────

Correrlo dos veces no duplica nada: si el servicio existe, sincroniza sus
variables; si no, lo crea. Se puede correr cada vez que se agrega una variable.
"""
import argparse
import json
import os
import subprocess
import sys

from dotenv import dotenv_values

from despliegue_contenedores.servicios import (
    FUENTE, MUERTOS, RAMA, REPO, SERVICIOS, desplegables,
)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _railway(args: list) -> tuple[int, str]:
    r = subprocess.run(["railway", *args], capture_output=True, text=True, shell=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _variables_de(servicio: str) -> dict:
    codigo, salida = _railway(["variables", "-s", servicio, "--json"])
    try:
        return json.loads(salida)
    except Exception:
        return {}


def _proyecto() -> dict:
    codigo, salida = _railway(["status", "--json"])
    try:
        return json.loads(salida)
    except Exception:
        sys.exit("No se pudo leer el proyecto. ¿Estás logueado? -> railway login")


def _ids() -> dict:
    """{nombre: id}. Los ids hacen falta para la API de triggers."""
    d = _proyecto()
    return {e["node"]["name"]: e["node"]["id"]
            for e in d.get("services", {}).get("edges", [])}


def _api(consulta: str) -> dict:
    codigo, salida = _railway(["api", consulta])
    try:
        # El CLI a veces imprime una línea extra después del JSON.
        return json.loads(salida[:salida.rindex("}") + 1])
    except Exception:
        return {"errors": [{"message": salida.strip()[:200]}]}


def _con_trigger(proyecto: dict) -> dict:
    """{serviceId: rama} de los que ya tienen disparador.

    ── Por qué esto es su propio paso ─────────────────────────────────────────

    `railway add --repo --branch` crea el servicio y lo conecta, pero NO crea el
    deployment trigger. El síntoma es de los peores: el servicio se despliega
    UNA vez —la del alta— y después los push no hacen nada. Parece que anduvo.

    Nos pasó: dos servicios quedaron corriendo un commit viejo y el arreglo
    estaba en GitHub sin que Railway se enterara.
    """
    d = _api('query { project(id: "%s") { deploymentTriggers { edges { node '
             '{ serviceId branch repository } } } } }' % proyecto["id"])
    try:
        edges = d["data"]["project"]["deploymentTriggers"]["edges"]
    except Exception:
        return {}
    return {e["node"]["serviceId"]: e["node"]["branch"] for e in edges}


def _crear_trigger(proyecto_id: str, entorno_id: str, servicio_id: str) -> str:
    d = _api(
        'mutation { deploymentTriggerCreate(input: { branch: "%s", repository: "%s", '
        'provider: "github", projectId: "%s", environmentId: "%s", serviceId: "%s" }) '
        '{ id } }' % (RAMA, REPO, proyecto_id, entorno_id, servicio_id)
    )
    if d.get("errors"):
        return "ERROR: " + d["errors"][0].get("message", "")[:150]
    return "creado"


def _existentes() -> set:
    return set(_ids())


def _valores() -> dict:
    """De dónde sale cada secreto.

    Casi todos se copian del servicio viejo, que los tiene todos y sigue en
    producción. `OPENAI_API_KEY` es la excepción: se agregó después de que ese
    servicio se configurara, así que sale del .env local.
    """
    base = _variables_de(FUENTE)
    local = dotenv_values(os.path.join(RAIZ, ".env"))
    for k, v in local.items():
        if v and not base.get(k):
            base[k] = v
    return base


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--aplicar", action="store_true",
                   help="crea y sincroniza de verdad. Sin esto solo muestra el plan.")
    p.add_argument("--servicio", help="solo este. Por defecto, todos los que estén listos.")
    args = p.parse_args()

    valores = _valores()
    if not valores:
        sys.exit(f"No se pudieron leer las variables de {FUENTE}.")

    proyecto = _proyecto()
    ids = _ids()
    hay = set(ids)
    triggers = _con_trigger(proyecto)
    entorno = next(
        (e["node"]["id"] for e in proyecto.get("environments", {}).get("edges", [])
         if e["node"]["name"] == "production"), None)
    plan = desplegables()
    if args.servicio:
        if args.servicio not in SERVICIOS:
            sys.exit(f"No conozco {args.servicio!r}. Están: {list(SERVICIOS)}")
        plan = {args.servicio: SERVICIOS[args.servicio]}

    print(f"repo {REPO} · rama {RAMA}")
    print(f"{'APLICANDO' if args.aplicar else 'PLAN (nada se toca, usá --aplicar)'}\n")

    for nombre, spec in plan.items():
        faltan = [k for k in spec["variables"] if not valores.get(k)]
        estado = "existe" if nombre in hay else "SE CREA"
        print(f"== {nombre}  [{estado}]")
        print(f"   {spec['rol']}")
        print(f"   dockerfile : {spec['dockerfile']}")
        print(f"   watch      : {spec['watch']}")
        print(f"   variables  : {len(spec['variables']) - len(faltan)}/{len(spec['variables'])}"
              + (f"   FALTAN: {faltan}" if faltan else ""))

        # Sin trigger el servicio se despliega UNA vez y después los push no
        # hacen nada. Es la falla que más caro sale porque parece que anduvo.
        rama_trigger = triggers.get(ids.get(nombre, ""))
        if rama_trigger == RAMA:
            print(f"   trigger    : ok, escucha {RAMA}")
        else:
            print(f"   trigger    : {'apunta a ' + rama_trigger if rama_trigger else 'NO EXISTE'}"
                  f"  -> los push NO lo redespliegan")

        if faltan:
            # Un servicio a medias arranca y falla en el primer turno, que es la
            # peor forma de enterarse.
            print(f"   -> se saltea: conseguí esas variables primero\n")
            continue

        if not args.aplicar:
            print()
            continue

        pares = [f"{k}={valores[k]}" for k in spec["variables"]]
        pares.append(f"RAILWAY_DOCKERFILE_PATH={spec['dockerfile']}")

        if nombre not in hay:
            cmd = ["add", "--service", nombre, "--repo", REPO, "--branch", RAMA]
            for v in pares:
                cmd += ["-v", v]
            _railway(cmd)
            ids.update(_ids())     # el id recién existe ahora
            print(f"   creado")
        else:
            for v in pares:
                _railway(["variables", "-s", nombre, "--skip-deploys", "--set", v])
            print(f"   variables sincronizadas ({len(pares)})")

        sid = ids.get(nombre)
        if sid and rama_trigger != RAMA:
            # `serviceConnect` re-apunta el origen; el trigger es aparte y es el
            # que engancha el webhook de GitHub.
            _api('mutation { serviceConnect(id: "%s", input: { repo: "%s", branch: "%s" }) '
                 '{ id } }' % (sid, REPO, RAMA))
            print(f"   trigger    : {_crear_trigger(proyecto['id'], entorno, sid)}")
        print()

    saltados = {n: s for n, s in SERVICIOS.items() if not s.get("listo", True)}
    if saltados and not args.servicio:
        print("Todavía no se despliegan:")
        for n in saltados:
            print(f"   {n}  — su código no está listo (ver servicios.py)")

    encendidos = [m for m in MUERTOS if m in hay]
    if encendidos:
        print(f"\nSiguen encendidos y no los usa nadie: {encendidos}")

    if not args.aplicar:
        print("\nPara aplicarlo:  python -m despliegue_contenedores.desplegar --aplicar")


if __name__ == "__main__":
    main()
