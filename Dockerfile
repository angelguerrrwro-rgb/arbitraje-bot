# Carry en modo papel 24/7. Railway: añade un volumen montado en /app/logs para conservar el estado.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY triarb ./triarb
COPY research ./research
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "triarb", "carry", "--plataforma", "binance", "--capital", "500"]
