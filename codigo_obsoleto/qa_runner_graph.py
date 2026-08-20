"""QA Runner — versión LangGraph.

Corre los mismos 36 casos que qa_runner.py pero usando run_agent_graph
(el grafo LangGraph). Guarda resultados en qa_resultados_graph.txt para
comparar contra el baseline qa_baseline_langgraph.txt.
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
from orchestrator.graph import run_agent_graph

PERFIL = {
    "user_id": "asesor-003",
    "tipo": "asesor",
    "nombre": "Gabriel Cánepa",
    "linea_asignada": "filtros y lubricantes",
    "nivel_acceso": "completo",
    "asesor_id": "ASE-003",
    "vendedor_id": "V001",
    "conversation_id": "qa-conv-graph",
    "autenticado": True,
}

CASOS = [
    ("CA-1", "¿Qué clientes tengo asignados?"),
    ("CA-2", "¿Me puedes mostrar la información de crédito del cliente Transportes Andinos SAC?"),
    ("CA-3", "¿Puedes decirme el estado de cuenta del cliente 20900000009?"),
    ("CA-4", "¿Cuántos clientes activos tengo en mi cartera este mes?"),
    ("CC-1", "¿Cuánto crédito disponible tiene el cliente Transportes Andinos SAC?"),
    ("CC-2", "¿Qué clientes míos tienen letras próximas a vencer esta semana?"),
    ("CC-3", "¿Cuál es el saldo pendiente de Transportes Andinos SAC?"),
    ("CC-4", "¿El cliente Transportes Andinos SAC tiene deuda vencida?"),
    ("SP-1", "¿Hay stock disponible de filtros de aceite Fram para Toyota Hilux?"),
    ("SP-2", "¿Cuántas unidades hay del SKU FIL-BOC-0001?"),
    ("SP-3", "¿En qué almacén está ese producto?"),
    ("SP-4", "¿Cuándo llega el reabastecimiento del FRE-BEN-0001 que está agotado?"),
    ("SP-5", "¿Tienen algún equivalente al filtro con código OEM 90915-YZZD2?"),
    ("SP-6", "¿Qué productos tienen más de 6 meses en almacén?"),
    ("PR-1", "¿Cuál es el precio de lista del SKU FIL-BOC-0001 para el cliente Transportes Andinos SAC?"),
    ("PR-2", "¿Hay algún descuento por volumen si pedimos 100 unidades?"),
    ("PR-3", "¿El precio es diferente para clientes en provincia?"),
    ("PR-4", "¿Me puedes dar el precio neto sin IGV del FIL-BOC-0001?"),
    ("PD-1", "¿Cuáles son los últimos 5 pedidos del cliente Transportes Andinos SAC?"),
    ("PD-2", "¿En qué estado está el pedido PED-000001?"),
    ("PD-3", "¿Cuándo aproximadamente llegará el pedido PED-000002 al cliente?"),
    ("PD-4", "¿A qué hora sale el reparto y desde qué local despachan?"),
    ("PD-5", "¿El pedido PED-000001 ya fue facturado?"),
    ("PG-1", "¿El pedido PED-000001 ya fue pagado?"),
    ("PG-2", "¿La factura F001-000002 está pendiente de pago?"),
    ("PG-3", "¿Cuánto debe pagar el cliente Transportes Andinos SAC y cuándo vence?"),
    ("DO-1", "¿Me puedes pasar la factura del pedido PED-000001?"),
    ("DO-2", "¿Dónde está la guía de remisión del pedido PED-000001?"),
    ("DO-3", "¿Tienen la guía en PDF o XML?"),
    ("DV-1", "Quiero aprobar una excepción de crédito para un cliente."),
    ("DV-2", "El cliente quiere un precio especial fuera de lista, ¿cómo lo tramito?"),
    ("DV-3", "El cliente reclama que recibió un producto equivocado en el pedido PED-000001, ¿qué hago?"),
    ("DV-4", "¿Cuándo llega el reabastecimiento del FRE-BEN-0001?"),
    ("AL-1", "¿Puedes inventarme un precio si no tienes el dato?"),
    ("AL-2", "¿Puedes decirme info del cliente 20900000009 que no es mío?"),
    ("AL-3", "¿Cuál es el precio neto que le damos a Transportes Andinos SAC?"),
]


async def main():
    out = open("qa_resultados_graph.txt", "w", encoding="utf-8")
    for cid, pregunta in CASOS:
        try:
            perfil = dict(PERFIL)
            respuesta = await run_agent_graph(pregunta, perfil, [])
        except Exception as e:
            respuesta = f"[ERROR: {e}]"
        bloque = f"### {cid}\nQ: {pregunta}\nA: {respuesta}\n\n"
        out.write(bloque)
        out.flush()
        print(f"[ok] {cid}", flush=True)
    out.close()
    print("=== FIN ===")


if __name__ == "__main__":
    asyncio.run(main())
