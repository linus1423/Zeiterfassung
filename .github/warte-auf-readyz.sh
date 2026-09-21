#!/bin/sh
# Wartet, bis der Webcontainer der CI /readyz mit 200 beantwortet.
#
# /readyz statt /healthz, weil erst das belegt, dass der Container auch die
# Datenbank erreicht. Scheitert der Start, landen die Ausgaben des Containers
# im Protokoll des Laufs; ohne sie wäre nur "Zeit abgelaufen" zu sehen.
set -eu

container="${1:-zeiterfassung-ci-web}"
url="${2:-http://127.0.0.1:8000/readyz}"
sekunden="${3:-120}"

versuche=$((sekunden / 2))
i=0
while [ "$i" -lt "$versuche" ]; do
    if curl --fail --silent "$url"; then
        exit 0
    fi
    i=$((i + 1))
    sleep 2
done

echo "Container ist nach ${sekunden}s nicht bereit." >&2
docker logs "$container" >&2 || true
exit 1
