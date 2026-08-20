# API del panel. Se copia UNA carpeta y adentro está todo.
#
#   panel/
#     main.py       la app y sus rutas
#     auth.py       login por contraseña -> token HMAC
#     consultas.py  el SQL, con los nombres de tabla escritos acá
#     db.py         su pool de Postgres
#
# No entra nada de los multiagentes, ni de recepción, ni de codigo_obsoleto. Si
# esta imagen construye, el panel corre.
#
# Es el único servicio de solo lectura del sistema: no escribe una fila ni puede
# correr una migración.
FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY panel/ ./panel/

CMD ["sh", "-c", "uvicorn panel.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
