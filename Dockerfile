# LegoSearch — monitorización de chollos LEGO.
#
# Imagen para Railway / Docker / Fly.io. Dos modos:
#
#   Worker persistente (--loop): escanea, guarda y espera el siguiente ciclo.
#   Ejecución única (--once): escanea, guarda y sale.
#
# OJO: el MOTOR PRINCIPAL es GitHub Actions (workflow monitor.yml). El
# servicio de Railway no se usa para escanear de forma programada: su cron
# se quedaba colgado sin publicar en runtime-data.
#
#   docker build -t legosearch .
#   docker run -e BRICKSET_API_KEY=xxx -e LEGO_REVISION_MINUTOS=1440 legosearch
#
# Para una ejecución única (cron externo, CI...):
#   docker run legosearch python main.py --once

FROM python:3.12-slim

# curl: el transporte alternativo para Reddit (su WAF bloquea el TLS de
#       requests). Sin curl, r/legodeals devuelve 429 y se pierden ofertas.
# tzdata: fechas y logs en hora de Madrid, no en UTC.
# procps: aporta pgrep, que usa el HEALTHCHECK de abajo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata procps \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Madrid \
    LOOP_MODE=1 \
    LEGO_REVISION_MINUTOS=1440

WORKDIR /app

# Las dependencias se cachean: solo se reinstalan si cambia requirements.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usuario sin privilegios: si alguien escapa del proceso, no es root.
RUN useradd --create-home --uid 1000 legosearch \
    && mkdir -p /app/data /app/reports \
    && chown -R legosearch:legosearch /app
USER legosearch

# El histórico vive en /app/data. Railway NO acepta la instrucción docker
# VOLUME (rechaza el build); se crea el volumen desde su panel o con la CLI.
# En Docker local, móntalo con `-v mi_volumen:/app/data`.
ENV LEGOSEARCH_OUTPUT=/app/data/opportunities.json

# Comprobación de vida: el proceso debe seguir escuchando señales.
HEALTHCHECK --interval=5m --timeout=10s --start-period=2m --retries=3 \
    CMD pgrep -f "main.py" > /dev/null || exit 1

CMD ["python", "main.py", "--loop"]
