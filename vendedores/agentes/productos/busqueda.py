"""Búsqueda de catálogo tolerante. Lo que el `SearchText` de la API no hace.

── Por qué existe este archivo ────────────────────────────────────────────────

`GET /api/article/filter?SearchText=` es una coincidencia literal de substring.
Alcanza si el asesor escribe exactamente lo que dice el catálogo, y no lo hace
nunca. Medido contra la API:

    'WK-723'                         ->   4 resultados
    'wk723'                          ->   0
    'amortiguador trasero'           ->   0
    'amortiguadores para Toyota'     ->   0
    'filtro de aceite Toyota Hilux'  ->   0

Las cuatro son incidencias que ya estaban resueltas —la 1, la 2, la 16 y la 20—
y se resolvían del lado del wrapper. Al pasar a la API directa se perdieron, así
que la lógica se rehace acá.

── Cómo funciona ─────────────────────────────────────────────────────────────

La API sí tolera dos cosas que sirven: es insensible a mayúsculas y acepta
prefijos ('amortiguad' devuelve los mismos 650 que 'amortiguador'). Sobre eso:

    1. se normaliza y se parte en tokens la consulta del asesor
    2. se tira lo que no discrimina: preposiciones, años, códigos de motor
    3. se expanden sinónimos (trasero -> post, delantero -> del)
    4. se pide a la API UN token, el más fuerte
    5. se filtran los candidatos exigiendo el resto de los tokens
    6. si el filtro deja cero, se afloja de a un token en vez de rendirse

El paso 4 es el que hace que esto sea barato: una sola llamada, y el filtro fino
corre en memoria sobre lo que volvió.

── El año y el código de motor ────────────────────────────────────────────────

Incidencia 20: el asesor escribe 'amortiguador Hilux 2015' o 'filtro G4LA'. Ni
el año ni el código de motor están en el texto del artículo, así que exigirlos
deja cero resultados siempre.

No se pueden simplemente ignorar y ya: son el dato con el que el asesor
distingue una versión de otra. Se sacan del filtro obligatorio y se devuelven en
`ignorados`, para que el área se lo diga y pida confirmación en vez de dar por
buena una pieza de otro año.
"""
import re
import unicodedata

# Palabras que no discriminan nada. Si se exigen, un 'amortiguador para Toyota'
# nunca matchea porque el catálogo no escribe 'para'.
VACIAS = {
    "para", "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
    "con", "sin", "y", "o", "a", "en", "por", "que", "al", "es", "son", "me",
    "mi", "tu", "su", "hay", "tiene", "tienes", "dame", "busca", "buscame",
    "necesito", "quiero", "tenes", "tienen", "algun", "alguna",
}

# Cómo lo dice el asesor -> cómo lo abrevia el catálogo. Incidencia 16.
SINONIMOS = {
    "trasero": "post", "trasera": "post", "traseros": "post", "traseras": "post",
    "posterior": "post", "posteriores": "post", "atras": "post",
    "delantero": "del", "delantera": "del", "delanteros": "del",
    "delanteras": "del", "adelante": "del",
    "habitaculo": "cabina", "aire acondicionado": "aire",
    "amortiguadores": "amortiguador", "filtros": "filtro", "discos": "disco",
    "pastillas": "pastilla", "fajas": "faja", "bujias": "bujia",
    "rotulas": "rotula", "focos": "foco", "resortes": "resorte",
}

# Un año de vehículo. Incidencia 20.
ANIO = re.compile(r"^(19|20)\d{2}$")

# Código de motor: mezcla letras y números y es corto ('G4LA', '1KD', '2L').
# No se confunde con un SKU porque los SKU se detectan antes, por longitud.
MOTOR = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9]{2,5}$")

# Debajo de esto un token no aporta: 'de', 'ok', 'st'.
MIN_TOKEN = 3


def normalizar(texto: str) -> str:
    """'Amortiguadór POSTERIOR' -> 'amortiguador posterior'. Sin acentos, minúsculas."""
    sin_acentos = unicodedata.normalize("NFD", texto or "")
    sin_acentos = "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")
    return sin_acentos.lower().strip()


def solo_alfanumerico(texto: str) -> str:
    """'WK-723' y 'wk723' -> 'wk723'. Para comparar códigos. Incidencia 2."""
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


def _es_codigo(token: str) -> bool:
    """Un SKU o código de proveedor: tiene dígitos y no es un año ni un motor."""
    if ANIO.match(token):
        return False
    return any(c.isdigit() for c in token) and len(token) >= 4


def analizar(consulta: str) -> dict:
    """Parte la consulta en lo que sirve para buscar y lo que hay que avisar.

    Devuelve {tokens, codigos, ignorados}:
      tokens     palabras que el artículo TIENE que contener
      codigos    posibles SKU o códigos de proveedor, se comparan sin guiones
      ignorados  años y códigos de motor: no filtran, pero se le avisan al área
    """
    crudo = normalizar(consulta).replace("/", " ").replace(",", " ")
    piezas = [p for p in re.split(r"\s+", crudo) if p]

    tokens, codigos, ignorados = [], [], []
    for p in piezas:
        limpio = p.strip(".?!¿¡()")
        if not limpio or limpio in VACIAS:
            continue
        if ANIO.match(limpio):
            ignorados.append(limpio)
        elif _es_codigo(limpio):
            codigos.append(limpio)
        elif MOTOR.match(limpio):
            ignorados.append(limpio)
        elif len(limpio) >= MIN_TOKEN:
            tokens.append(SINONIMOS.get(limpio, limpio))

    # Sin duplicados y conservando el orden en que los escribió el asesor.
    return {
        "tokens": list(dict.fromkeys(tokens)),
        "codigos": list(dict.fromkeys(codigos)),
        "ignorados": list(dict.fromkeys(ignorados)),
    }


def tramos_de_codigo(codigo: str) -> list[str]:
    """'wk723' -> ['723', 'wk']. Los tramos de letras y de dígitos, del más largo
    al más corto.

    ── Para qué ───────────────────────────────────────────────────────────────

    El asesor escribe `wk723` y el catálogo tiene `WK-723`. La API busca por
    substring, así que `wk723` no encuentra `WK-723` NUNCA: el guión está en el
    medio. Y aplanar la consulta no ayuda — ya venía plana.

    La salida es buscar por un tramo que sí sobrevive al guión. Medido:

        SearchText='723'  ->  41 resultados, incluye WK-723
        SearchText='wk'   -> 142 resultados, incluye WK-723

    Se pide por el más largo (más selectivo) y después el filtro local compara
    la forma aplanada, que sí coincide: 'wk723' contra 'wk723'.
    """
    tramos = re.findall(r"[a-z]+|\d+", normalizar(codigo))
    return sorted(tramos, key=len, reverse=True)


def token_mas_fuerte(tokens: list[str]) -> str:
    """Con cuál se le pide a la API. El más largo: es el más específico.

    'filtro aceite toyota hilux' -> 'toyota' no, 'filtro' no; gana 'aceite'... y
    en la práctica cualquiera de ellos trae un conjunto que después se filtra.
    Lo que importa es que sea UNO y no la frase entera, que es lo que devuelve
    cero.
    """
    return max(tokens, key=len) if tokens else ""


def _texto_de(articulo: dict) -> str:
    """Todo lo buscable de un artículo, junto y normalizado."""
    return normalizar(" ".join(str(articulo.get(c) or "") for c in (
        "itemCode", "itemName", "foreignName", "brandName",
        "nameSupply", "supplierCatalogNumber", "specialtyName", "subSpecialtyName",
    )))


def coincide(articulo: dict, tokens: list[str], codigos: list[str]) -> bool:
    """El artículo contiene TODOS los tokens y, si hay códigos, alguno de ellos.

    Los códigos se comparan sin guiones ni espacios contra el texto también sin
    guiones: así 'wk723' encuentra 'WK-723'.
    """
    texto = _texto_de(articulo)
    if not all(t in texto for t in tokens):
        return False
    if codigos:
        plano = solo_alfanumerico(texto)
        return any(solo_alfanumerico(c) in plano for c in codigos)
    return True


def filtrar(articulos: list[dict], tokens: list[str], codigos: list[str]) -> tuple[list[dict], list[str]]:
    """Filtra exigiendo todo; si queda vacío, afloja de a un token.

    Devuelve (encontrados, tokens_que_se_soltaron).

    Aflojar importa: 'amortiguador delantero hilux 2015' con los tres tokens
    puede dar cero porque el catálogo abrevia distinto, y devolver cero manda al
    asesor a un callejón sin salida. Con dos tokens devuelve algo revisable, y
    el área le dice qué relajó.
    """
    exigidos = list(tokens)
    soltados: list[str] = []

    while True:
        hallados = [a for a in articulos if coincide(a, exigidos, codigos)]
        if hallados or len(exigidos) <= 1:
            return hallados, soltados
        # Se suelta el más corto: el más largo suele ser el sustantivo principal
        # ('amortiguador') y soltarlo cambia de tema en vez de ampliar.
        mas_corto = min(exigidos, key=len)
        exigidos.remove(mas_corto)
        soltados.append(mas_corto)
