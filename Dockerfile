FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_ENV=prod \
    APP_PORT=8000 \
    DAAS_HOME=/srv/app

WORKDIR /srv/app

COPY pyproject.toml README.md requirements.lock ./
COPY app ./app
COPY data ./data
# migracje control plane (DAAS_AUTO_MIGRATE) czytaja alembic/ i alembic.ini z katalogu projektu (DAAS_HOME)
COPY alembic ./alembic
COPY alembic.ini ./

# Zaleznosci wylacznie z requirements.lock (wersje + hashe SHA-256); pakiet instalowany bez rozwiazywania zaleznosci.
RUN pip install "pip==26.2.1" \
    && pip install --require-hashes --no-deps -r requirements.lock \
    && pip install --no-deps --no-build-isolation .

# Kontener nie dziala jako root.
# /data/control (control.db) i /data/runtime (dane organizacji) to punkty montowania wolumenow z deploy/compose.yml;
# wolumen nazwany dziedziczy wlasciciela (daas) z tych katalogow.
RUN useradd --system --uid 10001 --home-dir /srv/app daas \
    && mkdir -p runtime/reports runtime/uploads /data/control /data/runtime \
    && chown -R daas:daas /srv/app/runtime /data
USER daas

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# Host, port i liczba procesow (1) pochodza z konfiguracji (APP_HOST/APP_PORT w obrazie), nie ze sztywnego CMD.
# DAAS_MODE=selfhost: bez DAAS_API_KEY start jest odmawiany (host 0.0.0.0 wymaga klucza).
# DAAS_MODE=cloud: wymagany jest DAAS_SECRET_KEY (min. 32 znaki); klucze API zyja w control plane.
CMD ["python", "-m", "app.main"]
