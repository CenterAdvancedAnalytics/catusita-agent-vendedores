# Servicio de placas. Dueño único de la conversación con Yahuar.
#
#   yahuar/
#     servicio.py   el loop: una placa a la vez
#     relay.py      todo lo que sabe de Yahuar (sus frases, sus tiempos)
#     waha.py       lo único que puede hacer: escribirle a Yahuar
#     vision.py     la foto de la tarjeta -> texto
#     redis.py      su pool
#
# Sin puerto: se habla con los workers por Redis y con nadie más.
#
# No entra nada de los multiagentes ni de recepción. Lo único compartido con
# ellos son los nombres de las colas, que son un contrato de strings.
FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yahuar/ ./yahuar/

CMD ["python", "-m", "yahuar.servicio"]
