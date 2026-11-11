# LegoSearch — worker de monitorización de chollos LEGO.
#
# Imagen pensada para Railway / Docker / Fly.io. Arranca en modo worker
# (--loop): escanea, guarda el histórico y espera el siguiente intervalo.
#
#   docker build -t legosearch .
#   docker run -e BRICKSET_API_KEY=xxx -e LEGO_REVISION_MINUTOS=360 legosearch
#
# Para una ejecución única (cron externo, CI...):
#   docker run legosearch python main.py --once

FROM python:3.12-slim

# curl: el transporte alternativo para Reddit (su WAF bloquea el TLS de
#       requests). Sin curl, r/legodeals devuelve 429 y se pierden ofertas.
# tzdata: fechas y logs en hora de Madrid, no en UTC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Madrid \
    LOOP_MODE=1 \
    LEGO_REVISION_MINUTOS=360

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

# El histórico vive en /app/data: móntalo como volumen para no perder los
# suelos históricos al redesplegar.
ENV LEGOSEARCH_OUTPUT=/app/data/opportunities.json
VOLUME ["/app/data"]

# Comprobación de vida: el proceso debe seguir escuchando señales.
HEALTHCHECK --interval=5m --timeout=10s --start-period=2m --retries=3 \
    CMD pgrep -f "main.py" > /dev/null || exit 1

CMD ["python", "main.py", "--loop"]
