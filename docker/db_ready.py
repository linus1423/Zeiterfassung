"""Beendet sich mit 0, sobald die konfigurierte Datenbank Verbindungen annimmt.

Wird vom Einstiegspunkt in einer Schleife aufgerufen. Compose kann mit
`depends_on: service_healthy` warten, systemd kann das nicht: eine Unit gilt als
gestartet, sobald der Container läuft, nicht sobald PostgreSQL antwortet.
Deshalb wartet der Container selbst, und beide Wege verhalten sich gleich.
"""

import os
import sys
from pathlib import Path


def main() -> int:
    # Das Skript liegt in /app/docker, das Projekt in /app.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zeiterfassung.settings")

    import django
    from django.db import connections

    django.setup()
    try:
        connections["default"].ensure_connection()
    except Exception as exc:
        print(f"Datenbank noch nicht erreichbar: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
