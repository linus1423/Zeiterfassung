#!/bin/sh
# Gemeinsamer Einstiegspunkt für Docker Compose und Podman Quadlet.
#
# Wartet auf die Datenbank, wendet auf Wunsch die Migrationen an und startet
# danach das eigentliche Kommando. Damit braucht der Quadlet-Aufbau keine
# Nachbildung von "depends_on: service_healthy", und beide Wege starten gleich.
set -eu

log() {
    printf '%s entrypoint: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >&2
}

if [ "${WAIT_FOR_DATABASE:-true}" = "true" ]; then
    wait_seconds="${DATABASE_WAIT_SECONDS:-60}"
    deadline=$(($(date +%s) + wait_seconds))
    until python /app/docker/db_ready.py; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            log "Datenbank nach ${wait_seconds}s nicht erreichbar, Abbruch."
            exit 1
        fi
        log "Datenbank noch nicht erreichbar, neuer Versuch in 2s."
        sleep 2
    done
fi

# Nur der Webdienst migriert; der Scheduler würde sonst parallel dieselben
# Migrationen anwenden wollen.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    log "Migrationen werden angewendet."
    python /app/manage.py migrate --noinput
fi

exec "$@"
