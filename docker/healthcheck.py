"""Healthcheck des Webcontainers: fragt den eigenen Prozess einmal an.

Absichtlich ohne zusätzliches Paket im Image (kein curl) und absichtlich gegen
/healthz statt /readyz: eine kurze Störung der Datenbank soll den Webdienst
nicht als kaputt melden und damit neu starten lassen.
"""

import os
import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000/healthz"

# Die Anfrage geht an den eigenen Prozess und darf deshalb nie über einen
# Proxy laufen, auch wenn http_proxy in der Umgebung steht.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def main() -> int:
    url = os.environ.get("HEALTHCHECK_URL") or DEFAULT_URL
    try:
        with _opener.open(url, timeout=4) as response:
            if response.status != 200:
                print(f"Healthcheck: Status {response.status}", file=sys.stderr)
                return 1
    except OSError as exc:
        print(f"Healthcheck fehlgeschlagen: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
