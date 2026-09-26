FROM docker.io/library/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# libpq5 braucht psycopg. pg_dump und psql brauchen "manage.py
# backup_database" und "manage.py restore_database" (siehe
# docs/podman-quadlet.md). Sie kommen aus dem PGDG-Repo, weil Debian bookworm
# nur Version 15 mitbringt und pg_dump einen neueren Server ablehnt;
# POSTGRES_CLIENT_VERSION muss deshalb mindestens so hoch sein wie die
# Version der Datenbank (Vorgabe hier wie dort: 16).
ARG POSTGRES_CLIENT_VERSION=16
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libpq5 ca-certificates gnupg; \
    python -c "import sys, urllib.request; sys.stdout.buffer.write(urllib.request.urlopen('https://www.postgresql.org/media/keys/ACCC4CF8.asc').read())" > /tmp/pgdg.asc; \
    gpg --dearmor -o /usr/share/keyrings/pgdg.gpg < /tmp/pgdg.asc; \
    rm /tmp/pgdg.asc; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends "postgresql-client-${POSTGRES_CLIENT_VERSION}"; \
    apt-get purge -y gnupg; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Feste UID und GID. Rootless Podman bildet sie über den Subuid-Bereich des
# Hosts ab; das Image läuft dadurch unverändert unter Docker (als UID 10001)
# und rootless unter Podman.
RUN groupadd --gid 10001 app \
    && useradd --create-home --uid 10001 --gid 10001 app

COPY . .

# Statische Dateien einsammeln; der Schlüssel wird dafür nicht gebraucht.
RUN DJANGO_DEBUG=true python manage.py collectstatic --noinput

# Erst danach die Rechte setzen, damit auch staticfiles/ dem Benutzer gehört.
RUN chown -R app:app /app
USER app

EXPOSE 8000

# Gilt für Docker und Podman gleichermaßen. Der Scheduler benutzt dasselbe
# Image ohne Webserver und schaltet den Healthcheck deshalb ab.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "zeiterfassung.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
