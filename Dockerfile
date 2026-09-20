FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Feste UID und GID. Rootless Podman bildet sie ueber den Subuid-Bereich des
# Hosts ab; das Image laeuft dadurch unveraendert unter Docker (als UID 10001)
# und rootless unter Podman.
RUN groupadd --gid 10001 app \
    && useradd --create-home --uid 10001 --gid 10001 app

COPY . .

# Statische Dateien einsammeln; der Schlüssel wird dafür nicht gebraucht.
RUN DJANGO_DEBUG=true python manage.py collectstatic --noinput

# Erst danach die Rechte setzen, damit auch staticfiles/ dem Benutzer gehoert.
RUN chown -R app:app /app
USER app

EXPOSE 8000

# Gilt fuer Docker und Podman gleichermassen. Der Scheduler benutzt dasselbe
# Image ohne Webserver und schaltet den Healthcheck deshalb ab.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "zeiterfassung.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
