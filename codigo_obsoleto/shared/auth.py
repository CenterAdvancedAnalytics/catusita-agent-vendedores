import os
from db import models

USE_MOCK = os.getenv("USE_AUTH_MOCK", "true").lower() == "true"

# SellerId real de Catusita que se usa como cartera de PRUEBA mientras no exista el
# mapeo definitivo número_WhatsApp -> SellerId (ver docs/vendedores_directorio.md).
# Default "2" = "Tarazona Davila Jefer" (85 clientes). NUNCA usar "1" (bucket Gerencia,
# devuelve los 3579 clientes de toda la empresa).
SELLER_ID_DEMO = os.getenv("SELLER_ID_DEMO", "2")

# Si es true, cualquier número no registrado se autentica como el vendedor demo
# (modo prueba abierto). Por defecto FALSE: a un número no registrado NO se le da
# acceso ni datos — se le pide que se registre. Ver incidencia #11.
ALLOW_SANDBOX_AUTH = os.getenv("ALLOW_SANDBOX_AUTH", "false").lower() == "true"

# Vendedores reales de prueba: cada número de WhatsApp mapeado a su SellerId real
# de Catusita (ver docs/vendedores_directorio.md). El vendedor_id es el SellerId
# que se pasa a /vendedor/{id}/clientes para traer SU cartera.
_MOCK_ASESORES = {
    "51958133722": {
        "user_id": "asesor-28",
        "tipo": "asesor",
        "nombre": "Saavedra Cicirello Jose Domingo",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0051",
        "vendedor_id": "28",   # 294 clientes
        "autenticado": True,
    },
    "51956646145": {
        "user_id": "asesor-28",
        "tipo": "asesor",
        "nombre": "Saavedra Cicirello Jose Domingo",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0051",
        "vendedor_id": "28",   # 294 clientes (2do número de Saavedra)
        "autenticado": True,
    },
    "51948985984": {
        "user_id": "asesor-50",
        "tipo": "asesor",
        "nombre": "Revolledo Humala Paulo",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0037",
        "vendedor_id": "50",   # 274 clientes
        "autenticado": True,
    },
    "51917842636": {
        "user_id": "asesor-78",
        "tipo": "asesor",
        "nombre": "Lupuche Morante Patricia Veronica",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0021",
        "vendedor_id": "78",   # 216 clientes
        "autenticado": True,
    },
    "51998321666": {
        "user_id": "asesor-53",
        "tipo": "asesor",
        "nombre": "Quispe Tasa William",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0009",
        "vendedor_id": "53",   # 186 clientes
        "autenticado": True,
    },
    "51960189568": {
        "user_id": "asesor-22",
        "tipo": "asesor",
        "nombre": "Peña Alva Mariluz Milagros",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0043",
        "vendedor_id": "22",   # 184 clientes
        "autenticado": True,
    },
    "51940351180": {
        "user_id": "asesor-58",
        "tipo": "asesor",
        "nombre": "Osorio Echevarria Roger Alcides",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0074",
        "vendedor_id": "58",   # 171 clientes
        "autenticado": True,
    },
    "51979405331": {
        "user_id": "asesor-29",
        "tipo": "asesor",
        "nombre": "Escobar Herrera Stephanny",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0052",
        "vendedor_id": "29",   # 163 clientes
        "autenticado": True,
    },
    # ── Lote 2: pares número↔vendedor confirmados con el directorio real ──────
    "51960660141": {
        "user_id": "asesor-56",
        "tipo": "asesor",
        "nombre": "Aparco Aparco Stefany Katherin",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0068",
        "vendedor_id": "56",   # 26 clientes
        "autenticado": True,
    },
    "51993819074": {
        "user_id": "asesor-60",
        "tipo": "asesor",
        "nombre": "Pinedo Peña Luz",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0012",
        "vendedor_id": "60",   # 67 clientes
        "autenticado": True,
    },
    "51932724126": {
        "user_id": "asesor-78",
        "tipo": "asesor",
        "nombre": "Lupuche Morante Patricia Veronica",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0021",
        "vendedor_id": "78",   # 216 clientes
        "autenticado": True,
    },
    "51922553756": {
        "user_id": "asesor-16",
        "tipo": "asesor",
        "nombre": "Chavez Ariste Fernando",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0032",
        "vendedor_id": "16",   # 144 clientes
        "autenticado": True,
    },
    "51986159395": {
        "user_id": "asesor-76",
        "tipo": "asesor",
        "nombre": "Vega Ganoza Jose Carlos",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0030",
        "vendedor_id": "76",   # 102 clientes
        "autenticado": True,
    },
    "51925240250": {
        "user_id": "asesor-66",
        "tipo": "asesor",
        "nombre": "Cardoza Lara Tamara Beatriz",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0022",
        "vendedor_id": "66",   # 134 clientes
        "autenticado": True,
    },
    # 'Alonso Salgado' no existe en el directorio de Catusita. Se le asigna esta
    # cartera porque el cliente que consultó (Contratistas Generales Cáceres) es
    # justamente de ella — así su consulta real funciona. Reasignar cuando se
    # confirme su código de vendedor verdadero.
    "51912263095": {
        "user_id": "asesor-52",
        "tipo": "asesor",
        "nombre": "Lupaca Castañeda Gisela Yanet",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0055",
        "vendedor_id": "52",   # 72 clientes
        "autenticado": True,
    },

    "51937142737": {
        "user_id": "asesor-84",
        "tipo": "asesor",
        "nombre": "Pacheco Quintana Gionvanna Sofia",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0031",
        "vendedor_id": "84",   # 123 clientes
        "autenticado": True,
    },
    "51940428503": {   # 2do número de William Quispe
        "user_id": "asesor-53",
        "tipo": "asesor",
        "nombre": "Quispe Tasa William",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0009",
        "vendedor_id": "53",   # 188 clientes
        "autenticado": True,
    },
    "51922102217": {   # 2do número de Steffany Escobar
        "user_id": "asesor-29",
        "tipo": "asesor",
        "nombre": "Escobar Herrera Stephanny",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0052",
        "vendedor_id": "29",   # 165 clientes
        "autenticado": True,
    },
    "51931074829": {
        "user_id": "asesor-82",
        "tipo": "asesor",
        "nombre": "Salome Santamaria Jessenia Del Rosario",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0076",
        "vendedor_id": "82",   # 141 clientes
        "autenticado": True,
    },

    # ── Alta masiva desde 'contactos para abrindar acceso al agente.xlsx' ──
    # Mapeo código C.V. -> SellerId confirmado contra el ERP.
    "51998326983": {
        "user_id": "asesor-2",
        "tipo": "asesor",
        "nombre": "Tarazona Davila Jefer",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0001",
        "vendedor_id": "2",
        "autenticado": True,
    },
    "51913223685": {
        "user_id": "asesor-40",
        "tipo": "asesor",
        "nombre": "Torres Quiñones Luis Alberto",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0003",
        "vendedor_id": "40",
        "autenticado": True,
    },
    "51999928349": {
        "user_id": "asesor-5",
        "tipo": "asesor",
        "nombre": "Bayona Paiva Myriam",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0004",
        "vendedor_id": "5",
        "autenticado": True,
    },
    "51950463048": {
        "user_id": "asesor-6",
        "tipo": "asesor",
        "nombre": "Huaman Jara Daniel",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0005",
        "vendedor_id": "6",
        "autenticado": True,
    },
    "51936805011": {
        "user_id": "asesor-45",
        "tipo": "asesor",
        "nombre": "Castro Elias Harol Armando",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0006",
        "vendedor_id": "45",
        "autenticado": True,
    },
    "51989099466": {
        "user_id": "asesor-44",
        "tipo": "asesor",
        "nombre": "Ruiz Revolledo Jose Luis",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0007",
        "vendedor_id": "44",
        "autenticado": True,
    },
    "51998314012": {
        "user_id": "asesor-46",
        "tipo": "asesor",
        "nombre": "Nora De La Fuente Gutierrez",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0008",
        "vendedor_id": "46",
        "autenticado": True,
    },
    "51944438526": {
        "user_id": "asesor-8",
        "tipo": "asesor",
        "nombre": "Bravo Crisanto David Esteban",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0011",
        "vendedor_id": "8",
        "autenticado": True,
    },
    "51947130392": {
        "user_id": "asesor-80",
        "tipo": "asesor",
        "nombre": "Ponce Galindo Lady Skarlet",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0018",
        "vendedor_id": "80",
        "autenticado": True,
    },
    "51944668606": {
        "user_id": "asesor-72",
        "tipo": "asesor",
        "nombre": "Alarcon Altamirano Pepe",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0020",
        "vendedor_id": "72",
        "autenticado": True,
    },
    "51995281370": {
        "user_id": "asesor-14",
        "tipo": "asesor",
        "nombre": "Gil Zuñiga Climaco",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0025",
        "vendedor_id": "14",
        "autenticado": True,
    },
    "51998328525": {
        "user_id": "asesor-15",
        "tipo": "asesor",
        "nombre": "Purihuaman De La Cruz Carlos",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0028",
        "vendedor_id": "15",
        "autenticado": True,
    },
    "51923044466": {
        "user_id": "asesor-84",
        "tipo": "asesor",
        "nombre": "Pacheco Quintana Gionvanna Sofia",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0031",
        "vendedor_id": "84",
        "autenticado": True,
    },
    "51997824502": {
        "user_id": "asesor-50",
        "tipo": "asesor",
        "nombre": "Revolledo Humala Paulo",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0037",
        "vendedor_id": "50",
        "autenticado": True,
    },
    "51928078321": {
        "user_id": "asesor-21",
        "tipo": "asesor",
        "nombre": "Alarcon Altamirano Mayco",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0042",
        "vendedor_id": "21",
        "autenticado": True,
    },
    "51991885707": {
        "user_id": "asesor-22",
        "tipo": "asesor",
        "nombre": "Peña Alva Mariluz Milagros",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0043",
        "vendedor_id": "22",
        "autenticado": True,
    },
    "51951665369": {
        "user_id": "asesor-61",
        "tipo": "asesor",
        "nombre": "Rojas Vasquez Abner",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0045",
        "vendedor_id": "61",
        "autenticado": True,
    },
    "51990325151": {
        "user_id": "asesor-25",
        "tipo": "asesor",
        "nombre": "Escobar Salinas Miguel",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0046",
        "vendedor_id": "25",
        "autenticado": True,
    },
    "51998326469": {
        "user_id": "asesor-26",
        "tipo": "asesor",
        "nombre": "Velarde Konja Roberto",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0048",
        "vendedor_id": "26",
        "autenticado": True,
    },
    "51933571581": {
        "user_id": "asesor-27",
        "tipo": "asesor",
        "nombre": "Valle Atahuaman Toribio",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0050",
        "vendedor_id": "27",
        "autenticado": True,
    },
    "51995280824": {
        "user_id": "asesor-28",
        "tipo": "asesor",
        "nombre": "Saavedra Cicirello Jose Doming",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0051",
        "vendedor_id": "28",
        "autenticado": True,
    },
    "51958856158": {
        "user_id": "asesor-52",
        "tipo": "asesor",
        "nombre": "Lupaca Castañeda Gisela Yanet",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0055",
        "vendedor_id": "52",
        "autenticado": True,
    },
    "51973672908": {
        "user_id": "asesor-31",
        "tipo": "asesor",
        "nombre": "Mansilla Dias Jimmy Alfonso",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0063",
        "vendedor_id": "31",
        "autenticado": True,
    },
    "51978328967": {
        "user_id": "asesor-55",
        "tipo": "asesor",
        "nombre": "Fernandez Perez Genesis Victoria",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0064",
        "vendedor_id": "55",
        "autenticado": True,
    },
    "51968168072": {
        "user_id": "asesor-57",
        "tipo": "asesor",
        "nombre": "Omar Velarde Durand",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0070",
        "vendedor_id": "57",
        "autenticado": True,
    },
    "51954076802": {
        "user_id": "asesor-73",
        "tipo": "asesor",
        "nombre": "Ponce Galindo Lady Rubi",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0072",
        "vendedor_id": "73",
        "autenticado": True,
    },
    "51922948231": {
        "user_id": "asesor-58",
        "tipo": "asesor",
        "nombre": "Osorio Echevarria Roger Alcides",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0074",
        "vendedor_id": "58",
        "autenticado": True,
    },
    "51998278016": {
        "user_id": "asesor-34",
        "tipo": "asesor",
        "nombre": "Meza Ramirez Julio",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0083",
        "vendedor_id": "34",
        "autenticado": True,
    },

    # ── Habilitados SIN cartera (no figuran en el ERP): acceso a features
    #    generales (stock, precio, catalogo, placa); cartera vacia via 99999 ──
    "51997004283": {
        "user_id": "asesor-sincartera-51997004283",
        "tipo": "asesor",
        "nombre": "Vega Ampuero, Luis Carlos",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0016",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51904031197": {
        "user_id": "asesor-sincartera-51904031197",
        "tipo": "asesor",
        "nombre": "Luis Romero",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51950306818": {
        "user_id": "asesor-sincartera-51950306818",
        "tipo": "asesor",
        "nombre": "Bruno Cavassa",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51987829716": {
        "user_id": "asesor-sincartera-51987829716",
        "tipo": "asesor",
        "nombre": "Enrique Rivera",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51934812235": {
        "user_id": "asesor-sincartera-51934812235",
        "tipo": "asesor",
        "nombre": "Martin Inga",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51946769823": {
        "user_id": "asesor-sincartera-51946769823",
        "tipo": "asesor",
        "nombre": "David Caceres",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51994546446": {
        "user_id": "asesor-sincartera-51994546446",
        "tipo": "asesor",
        "nombre": "Milagros Negrillo",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51998322145": {
        "user_id": "asesor-sincartera-51998322145",
        "tipo": "asesor",
        "nombre": "Jose Marquez",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51998074449": {
        "user_id": "asesor-sincartera-51998074449",
        "tipo": "asesor",
        "nombre": "Feliciano Murga",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },
    "51997333858": {
        "user_id": "asesor-sincartera-51997333858",
        "tipo": "asesor",
        "nombre": "Edwin Ampuero",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "SIN-CODIGO",
        "vendedor_id": "99999",   # sin SellerId en el ERP -> cartera vacia
        "autenticado": True,
    },

    # ── LIDs ──────────────────────────────────────────────────────────────────
    # WAHA entrega para algunos contactos el LID interno de Meta ('<lid>@lid') en
    # vez del teléfono. Ese LID es estable por cuenta, así que se mapea igual que
    # un número. Se captura de los logs: [WAHA] from='<lid>@lid'.
    "111621938671767": {   # LID de Gabriel (tel 51940351180) → Osorio
        "user_id": "asesor-58",
        "tipo": "asesor",
        "nombre": "Osorio Echevarria Roger Alcides",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "0074",
        "vendedor_id": "58",   # 171 clientes
        "autenticado": True,
    },
}

def _asesor_sandbox(numero_whatsapp: str) -> dict:
    """Perfil de asesor por defecto para el sandbox: en Kapso quien escribe en el
    canal de vendedores ES un asesor de ventas, aunque su celular no esté en la
    lista mock. Se mapea a V001 (la cartera con datos de QA)."""
    return {
        "user_id": f"asesor-sandbox-{numero_whatsapp}",
        "tipo": "asesor",
        "nombre": "Asesor Catusita",
        "linea_asignada": "",
        "nivel_acceso": "completo",
        "asesor_id": "ASE-DEMO",
        "vendedor_id": SELLER_ID_DEMO,
        "whatsapp_number": numero_whatsapp,
        "autenticado": True,
    }


async def get_user_profile(numero_whatsapp: str, agente_tipo: str) -> dict:
    if USE_MOCK:
        if agente_tipo == "vendedor":
            # Número conocido → su perfil.
            perfil = _MOCK_ASESORES.get(numero_whatsapp)
            if perfil:
                return perfil
            # No registrado: solo en modo prueba abierto se le da la cartera demo.
            if ALLOW_SANDBOX_AUTH:
                return _asesor_sandbox(numero_whatsapp)
            # Por defecto: no autenticar ni dar datos a un número desconocido.
            return {
                "autenticado": False,
                "tipo": "asesor",
                "numero_whatsapp": numero_whatsapp,
                "mensaje": ("Tu número no está registrado en el sistema. "
                            "Por favor contacta a tu supervisor para que te den de alta."),
            }
        else:
            # Para clientes en mock, aceptar cualquier número con estado no-autenticado
            return {
                "autenticado": False,
                "tipo": "cliente",
                "numero_whatsapp": numero_whatsapp,
                "mensaje": "Bienvenido a Catusita. Para continuar, indícame tu RUC o número de pedido.",
            }

    # Producción: consultar base de datos
    if agente_tipo == "vendedor":
        user = None  # la tabla `users` se retiró; el registro vive en _MOCK_ASESORES
        if not user:
            return {"autenticado": False, "tipo": "asesor",
                    "mensaje": "Tu número no está registrado. Contacta a tu supervisor."}
        return {**user, "autenticado": True, "asesor_id": str(user["id"])}
    else:
        return {
            "autenticado": False,
            "tipo": "cliente",
            "numero_whatsapp": numero_whatsapp,
            "mensaje": "Bienvenido. Indícame tu RUC o número de pedido.",
        }
