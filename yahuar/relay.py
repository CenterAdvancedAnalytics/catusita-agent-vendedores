"""Hablar con Yahuar. Todo lo que sabe de ESA conversación y nada más.

Yahuar es un número de WhatsApp (`51977504279`) al que se le manda una placa y
contesta con los datos del vehículo y la foto de la tarjeta de identificación
vehicular.

── Lo que hace raro y hay que saber ───────────────────────────────────────────

**No contesta un mensaje: contesta tres o cuatro.** Un saludo, después los
datos, después la foto — cada uno como un mensaje suelto de WhatsApp. Por eso no
se puede procesar el primero que llega: hay que juntarlos.

**Arranca preguntando.** «Soy Yahuar, ¿en qué te puedo ayudar?», «¿qué
información buscas?». Eso no es una respuesta, es su saludo, y si se le reenvía
al vendedor lo confunde (incidencia 21). Se le contesta «Placa vehicular» y se
sigue esperando.

**Los datos vienen en una foto.** La tarjeta llega como imagen, no como texto,
así que marca/modelo/año/VIN hay que sacarlos con visión.
"""
import os

# ── Identidad ─────────────────────────────────────────────────────────────────
NUMERO = os.getenv("YAHUAR_NUMBER", "51977504279")
CHAT_ID = f"{NUMERO}@c.us"

# ── Tiempos ───────────────────────────────────────────────────────────────────
#
# Cuánto silencio hace falta para dar por terminada su respuesta. Son 7 y no 3
# porque entre el texto y la foto Yahuar se toma su tiempo: con menos, se
# procesa el texto y la foto llega cuando ya se cerró todo.
DEBOUNCE = float(os.getenv("YAHUAR_DEBOUNCE", "7"))

# Cuánto se espera a que conteste ALGO. La tool corta a los 90, así que acá se
# corta antes: mejor devolver "no contestó" que dejar que la tool expire sin
# saber por qué.
ESPERA_MAX = float(os.getenv("YAHUAR_ESPERA_MAX", "75"))

# ── Sus frases ────────────────────────────────────────────────────────────────
#
# Vienen del stack viejo (`webhooks/whatsapp.py`), donde se fueron ampliando a
# medida que Yahuar sorprendía. Se copian enteras a propósito: cada una está acá
# porque un día se le coló a un vendedor.

# Saluda o pide aclaración. No es una respuesta: se le contesta y se sigue.
ACLARACIONES = (
    "necesito entender", "¿es un", "cuéntame más", "qué consulta",
    "necesito saber", "puedes aclarar", "me puedes decir",
    "qué tipo de", "para poder ayudarte",
    "qué información buscas", "que informacion buscas", "veo que me pasas",
    "soy yahuar", "en qué te puedo ayudar", "en que te puedo ayudar",
    "¿qué deseas", "puedo ayudarte",
)

# No encontró la placa. Es una respuesta final, pero negativa.
ERRORES = (
    "creo que escribiste", "no encontré", "no pude", "no existe",
    "inválida", "incorrecta", "no reconozco", "no tengo datos",
)

# Lo que se le manda para que no pida aclaración: la placa con contexto.
def pedido(placa: str) -> str:
    return f"Placa vehicular: {placa.upper()}"


RESPUESTA_A_ACLARACION = "Placa vehicular"

# ── La foto ───────────────────────────────────────────────────────────────────
#
# Qué se le pide al modelo con visión. Pide TODO lo legible y sin comentarios:
# el orquestador arma el mensaje final, acá solo se vuelca la tarjeta a texto.
INSTRUCCION_TARJETA = (
    "Esta es la foto de una Tarjeta de Identificación Vehicular peruana. "
    "Extrae y devuelve EN TEXTO, como lista clave: valor, todos los datos legibles: "
    "placa, marca, modelo, año de fabricación, color, número de serie/VIN, "
    "número de motor, categoría, combustible y propietario(s) si aparecen. "
    "Solo los datos, sin comentarios ni explicaciones."
)


def es_aclaracion(texto: str) -> bool:
    bajo = (texto or "").lower()
    return any(f in bajo for f in ACLARACIONES)


def es_error(texto: str) -> bool:
    bajo = (texto or "").lower()
    return any(f in bajo for f in ERRORES)


def normalizar_placa(placa: str) -> str:
    """'f9n-562' -> 'F9N562'. La clave del resultado se arma con esto."""
    return "".join(c for c in (placa or "").upper() if c.isalnum())
