"""Genera docs/tipos_de_pregunta.xlsx a partir de los chats REALES de producción.

No inventa preguntas: lee `chat_messages` (rol='user') y las clasifica con reglas
de palabra clave. Lo que no matchea queda como `sin_clasificar` a propósito —
es la señal de que la regla está incompleta, no algo para esconder.

    python docs/gen_tipos_pregunta.py

El Excel tiene tres hojas:
    Tipos            un renglón por tipo de pregunta, con columnas para probar
    Preguntas        las 302 reales, con el tipo que le asigné (auditable)
    Fuera del RAG    lo que se pide pero NO se puede hacer
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import unicodedata

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["DATABASE_URL"] = json.loads(subprocess.run(
    ["railway", "variables", "-s", "Postgres", "--json"],
    capture_output=True, text=True, shell=True).stdout)["DATABASE_PUBLIC_URL"]

import asyncpg  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "tipos_de_pregunta.xlsx")

VEHICULOS = ("toyota", "hilux", "corolla", "yaris", "kia", "picanto", "sorento",
             "rio", "suzuki", "swift", "nissan", "sentra", "subaru", "legacy",
             "hino", "volvo", "vw", "hyundai", "mitsubishi", "chevrolet",
             "1gd", "1kd", "b16", "500", "envidia")

# Nombres de pieza. Sirven para reconocer una búsqueda de producto aunque no
# nombre el vehículo: "Radiador", "disco de freno", "carboceramica".
PIEZAS = ("filtro", "amortiguador", "radiador", "disco", "pastilla", "llanta",
          "aceite", "lubricante", "tambor", "palier", "foco", "plato", "kit",
          "embrague", "trapecio", "parachoque", "brida", "bocamaza", "silicona",
          "hidrolina", "freno", "engine oil", "carboceramica", "bujia",
          "rodaje", "reten", "faja", "bomba", "valvula", "grasa", "refriger")

# Saludos: se comparan CONTRA EL TEXTO COMPLETO, no como subcadena.
# Al principio esto era subcadena y fue un desastre: la clave "no" hacía que
# "PORRAS RAMOS ISIDORO MARINO" fuera un saludo, y "1" se comía todos los SKU
# con dígitos (Bd1607, 31210-0k040, Hl-1446).
SALUDOS = {"hola", "holas", "hola!", "buenos dias", "buenas", "buenas tardes",
           "buenas noches", "como estas", "que tal", "gracias", "buen dia"}

# Un código suelto: el vendedor lo tira solo, continuando la consulta anterior.
CODIGO = re.compile(r"^[a-z]{0,4}[\s-]?\d{2,6}[a-z0-9\-]{0,6}$")

# El orden IMPORTA: se evalúa de arriba hacia abajo y gana el primero.
# Lo específico va antes que lo genérico (queja antes que saludo, guía antes
# que factura, equivalencia antes que producto).
REGLAS = [
    ("queja", ("no me sirve", "no ayudas", "no me ayudaste", "no me estas ayudando",
               "no me estás ayudando", "no te encuentro utilidad", "no me sirves",
               "no me entiendes", "mira bien", "aver")),
    ("capacidades", ("en que me puedes ayudar", "que me puedes ayudar",
                     "cuales son las funciones", "que funciones", "quien soy",
                     "capacidad de enviar fotos", "ya tienes la capacidad",
                     "necesito la funcionalidad", "puedes ayudarme")),
    ("guia_remision", ("guia", "guía")),
    ("credito_estado_cuenta", ("credito", "crédito", "estado de cuenta", "debe?",
                               "cuanto debe", "deuda", "vencid", "tarde en sus pagos",
                               "limite de credito", "límite de crédito")),
    ("ventas_metas", ("acumulado", "meta", "facturado", "mas vendido", "más vendido",
                      "no le he vendido", "dejado de comprar", "han facturado")),
    ("oferta", ("oferta",)),
    ("equivalencia", ("equivalente", "equivalencia", " en sakura", " en aisin",
                      "oem", "en la marca")),
    ("placa_vin", ("placa", "vin ")),
    ("historial_compras", ("ha comprado", "ah comprado", "que compro", "qué compro",
                           "compras mas frecuentes", "compras más frecuentes",
                           "ultima compra", "última compra", "se le vendio",
                           "se le vendió", "ultimo precio", "último precio",
                           "me compra", "compró", "a comprado", "que productos  compro",
                           "en que fechas se le", "ultima vez que se le")),
    ("cartera", ("cartera", "cuantos clientes", "cuántos clientes", "cuan tos clientes",
                 "mis clientes", "numero de clientes", "número de clientes",
                 "clientes tiene", "clientes tengo")),
    ("contacto_cliente", ("telefono", "teléfono", "direccion", "dirección",
                          "contacto", "correo", "perfil cliente", "esta registrado",
                          "está registrado", "ruc ")),
    ("factura_nc", ("factura", "nota de credito", "nota de crédito",
                    "detalle de factura", "en pdf", "pdf")),
    ("pedido_despacho", ("pedido", "despach", "entrego", "entregó", "se entrego",
                         "orden", "rechazo", "rechazó", "llevaron", "pidio", "pidió")),
    ("info_tecnica", ("medidas", "para que", "para qué", "aplicacion", "aplicación",
                      "hasta que año", "que significa", "qué significa",
                      "seguro interno", "grados", "que tipo", "qué tipo",
                      "es bueno", "se aplican", "le hace", "nomenclatura")),
    # El vehículo manda sobre "stock/precio": "tienes filtros para Toyota 1gd" es
    # una búsqueda por vehículo, no una consulta de stock por código.
    ("producto_por_vehiculo", VEHICULOS),
    ("stock_precio_foto", ("stock", "precio", "foto", "tenemos", "tienes", "hay ")),
    ("producto_suelto", PIEZAS),
]

# Cada tipo: (área que lo resuelve, se puede hoy, qué habría que verificar)
FICHA = {
    "producto_por_vehiculo": ("productos", "SI",
                              "Busqueda flexible: nombre + marca/modelo. Ver que no exija SKU."),
    "stock_precio_foto": ("productos", "SI",
                          "Stock, precio de LISTA y foto por codigo. Nunca precio neto."),
    "equivalencia": ("productos", "SI",
                     "Cross-reference por codigo OEM o de proveedor entre marcas."),
    "info_tecnica": ("productos", "PARCIAL",
                     "`foreignName` trae la aplicacion. NO hay ficha tecnica ni medidas."),
    "placa_vin": ("vehiculos", "SI",
                  "Placa -> marca/modelo/año/motor. TARDA 30-60s, hay que avisar."),
    "cartera": ("clientes", "SI",
                "Cartera del asesor. Debe salir SIEMPRE con razon social + RUC."),
    "contacto_cliente": ("clientes", "SI",
                         "Direccion, telefono, correo del cliente de su cartera."),
    "pedido_despacho": ("pedidos", "SI",
                        "Estado del pedido y si se entrego. Acepta nombre o RUC."),
    "historial_compras": ("pedidos", "SI",
                          "`consultar_compras`: lee el detalle de los XML. Ojo con la MONEDA."),
    "factura_nc": ("facturacion", "SI",
                   "PDF de factura y nota de credito. El numero viene con prefijo 'FA/ '."),
    "guia_remision": ("pedidos", "PARCIAL",
                      "El NUMERO si (dispatch-status: deliveryGuide). El PDF NO esta en la API."),
    "credito_estado_cuenta": ("-", "NO",
                              "Ningun endpoint expone linea, deuda ni saldo. NO va al RAG."),
    "ventas_metas": ("-", "NO",
                     "La API no lista por vendedor ni filtra por fecha. NO va al RAG."),
    "oferta": ("-", "NO",
               "No hay endpoint de ofertas ni de campañas. NO va al RAG."),
    "capacidades": ("orquestador", "SI",
                    "Que sepa enumerar lo que hace. Sale del prompt, no del RAG."),
    "producto_suelto": ("productos", "SI",
                        "Pieza sin vehiculo: 'Radiador', 'disco de freno'. Debe repreguntar."),
    "codigo_suelto": ("productos", "SI",
                      "Un codigo tirado solo, continuando la consulta anterior."),
    "continuacion": ("-", "-", "Segunda mitad de una consulta anterior. No es un tipo."),
    "queja": ("-", "-", "Señal de dolor: mirar la conversacion completa alrededor."),
    "saludo_ruido": ("-", "-", "Saludo. No es un tipo de consulta."),
    "sin_clasificar": ("-", "?", "La regla no lo agarro. Revisar a mano."),
}

ORDEN = ["producto_por_vehiculo", "stock_precio_foto", "producto_suelto",
         "codigo_suelto", "equivalencia", "info_tecnica",
         "placa_vin", "cartera", "contacto_cliente", "pedido_despacho",
         "historial_compras", "factura_nc", "guia_remision",
         "credito_estado_cuenta", "ventas_metas", "oferta",
         "capacidades", "queja", "continuacion", "saludo_ruido", "sin_clasificar"]


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def clasificar(texto: str) -> str:
    t = normalizar(texto).strip()
    if not t:
        return "saludo_ruido"
    if t in SALUDOS:                       # comparación EXACTA, ver SALUDOS
        return "saludo_ruido"
    # Placa peruana suelta: 3 letras + 3 dígitos, en un mensaje corto.
    if re.search(r"\b[a-z]{3}[\s-]?\d{3}\b", t) and len(t) < 30:
        return "placa_vin"

    for nombre, claves in REGLAS:
        for k in claves:
            if normalizar(k) in t:
                return nombre

    # Lo que queda y es corto: o es un código suelto, o es una continuación
    # ("ese", "la 2da", "si porfavor"). Ninguna de las dos es un TIPO de
    # consulta — son la segunda mitad de una que ya está clasificada arriba.
    if CODIGO.match(t.replace(" ", "")):
        return "codigo_suelto"
    if len(t.split()) <= 4:
        return "continuacion"
    return "sin_clasificar"


TITULO = Font(bold=True, color="FFFFFF", size=11)
FONDO = PatternFill("solid", fgColor="2F5597")
FONDO_NO = PatternFill("solid", fgColor="F4CCCC")
FONDO_PARCIAL = PatternFill("solid", fgColor="FFF2CC")
FONDO_SI = PatternFill("solid", fgColor="D9EAD3")
ARRIBA = Alignment(vertical="top", wrap_text=True)


def encabezar(ws, cols):
    ws.append([c for c, _ in cols])
    for i, (_, ancho) in enumerate(cols, start=1):
        ws.cell(row=1, column=i).font = TITULO
        ws.cell(row=1, column=i).fill = FONDO
        ws.column_dimensions[get_column_letter(i)].width = ancho
    ws.freeze_panes = "A2"


async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"])
    filas = await c.fetch(
        "SELECT vendedor_nombre, contenido, created_at, canal "
        "FROM chat_messages WHERE rol='user' ORDER BY created_at")
    await c.close()

    preguntas = []
    for f in filas:
        texto = " ".join((f["contenido"] or "").split())
        preguntas.append({
            "tipo": clasificar(texto),
            "vendedor": f["vendedor_nombre"] or "?",
            "texto": texto,
            "fecha": f["created_at"].strftime("%Y-%m-%d %H:%M"),
        })

    cuenta = {}
    for p in preguntas:
        cuenta[p["tipo"]] = cuenta.get(p["tipo"], 0) + 1

    wb = Workbook()

    # ── Hoja 1: los tipos, uno por renglón, para ir probando ──────────────────
    ws = wb.active
    ws.title = "Tipos"
    encabezar(ws, [("N°", 5), ("Tipo de pregunta", 26), ("Veces", 7),
                   ("Ejemplos reales de los chats", 62), ("Área", 13),
                   ("¿Se puede hoy?", 14), ("Qué hay que verificar", 46),
                   ("PROBADO", 10), ("Respuesta que dio", 46),
                   ("VEREDICTO", 14), ("¿Va al RAG?", 12), ("Notas de Gabriel", 34)])

    n = 0
    for tipo in ORDEN:
        if not cuenta.get(tipo):
            continue
        n += 1
        area, puede, verificar = FICHA[tipo]
        muestras = [p["texto"] for p in preguntas if p["tipo"] == tipo][:4]
        ws.append([n, tipo, cuenta[tipo],
                   "\n".join(f"· {m[:78]}" for m in muestras),
                   area, puede, verificar, "", "", "", "", ""])
        r = ws.max_row
        for col in range(1, 13):
            ws.cell(row=r, column=col).alignment = ARRIBA
        color = {"SI": FONDO_SI, "PARCIAL": FONDO_PARCIAL, "NO": FONDO_NO}.get(puede)
        if color:  # openpyxl no acepta fill=None
            ws.cell(row=r, column=6).fill = color
        ws.row_dimensions[r].height = 62

    # ── Hoja 2: las 302 crudas, para auditar la clasificación ────────────────
    ws2 = wb.create_sheet("Preguntas reales")
    encabezar(ws2, [("Fecha", 17), ("Vendedor", 30), ("Pregunta", 88),
                    ("Tipo asignado", 24), ("¿Mal clasificada?", 18)])
    for p in preguntas:
        ws2.append([p["fecha"], p["vendedor"], p["texto"], p["tipo"], ""])
        ws2.cell(row=ws2.max_row, column=3).alignment = ARRIBA

    # ── Hoja 3: lo que se pide y NO se puede ─────────────────────────────────
    ws3 = wb.create_sheet("Fuera del RAG")
    encabezar(ws3, [("Tipo", 26), ("Veces", 7), ("Por qué no se puede", 66),
                    ("Pregunta real", 70)])
    for tipo in ORDEN:
        if FICHA[tipo][1] != "NO" or not cuenta.get(tipo):
            continue
        for p in [x for x in preguntas if x["tipo"] == tipo]:
            ws3.append([tipo, cuenta[tipo], FICHA[tipo][2], p["texto"]])

    wb.save(SALIDA)
    print(f"{len(preguntas)} preguntas -> {SALIDA}\n")
    for tipo in ORDEN:
        if cuenta.get(tipo):
            print(f"  {cuenta[tipo]:4}  {tipo:26} {FICHA[tipo][1]}")


if __name__ == "__main__":
    asyncio.run(main())
