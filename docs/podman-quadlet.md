# Betrieb mit Podman und Quadlet

Die Zeiterfassung laeuft mit Docker Compose (`docker compose up --build`) und
mit rootless Podman unter systemd. Beide Wege benutzen dasselbe Image und
denselben Einstiegspunkt, es gibt also keine zweite Variante des Codes.

Die Unit-Dateien liegen in [`deploy/quadlet/`](../deploy/quadlet).

## Was Quadlet anders macht als Compose

| Compose | Quadlet |
|---|---|
| `depends_on: service_healthy` | gibt es nicht; der Einstiegspunkt im Container wartet selbst auf die Datenbank |
| Dienst `scheduler` mit `sleep`-Schleife | systemd-Timer, zwischen zwei Laeufen laeuft kein Container |
| `${VAR}` aus der `.env` wird eingesetzt | keine Ersetzung; zusammengesetzte Werte wie `DATABASE_URL` werden fertig hinterlegt |
| `restart: unless-stopped` | `Restart=always` in der Unit, Start beim Login ueber `[Install]` |
| Passwoerter als Umgebungsvariable | `podman secret`, als Datei in den Container gehaengt |
| laeuft als root (Docker-Daemon) | laeuft rootless als gewoehnlicher Benutzer |

## Voraussetzungen

* Podman 4.7 oder neuer (Quadlet mit `Secret=`), getestet gegen die 5er-Reihe.
* Ein eigener Benutzer fuer den Dienst, mit Subuid- und Subgid-Bereich
  (`/etc/subuid`, `/etc/subgid`; bei den ueblichen Distributionen legt
  `useradd` das selbst an).
* Eingeschaltetes Lingering, sonst beendet systemd die Dienste, sobald sich der
  Benutzer abmeldet:

  ```bash
  sudo loginctl enable-linger zeiterfassung
  ```

Alle folgenden Befehle laufen als dieser Benutzer, ohne `sudo`.

## Einrichten

### 1. Image bauen

```bash
git clone https://github.com/linus1423/Zeiterfassung.git
cd Zeiterfassung
podman build -t localhost/zeiterfassung:latest .
```

### 2. Geheimnisse anlegen

```bash
DB_PASSWORT="$(openssl rand -base64 33)"

printf '%s' "$DB_PASSWORT" | podman secret create zeiterfassung-db-password -
printf 'postgres://zeiterfassung:%s@zeiterfassung-db:5432/zeiterfassung' "$DB_PASSWORT" \
    | podman secret create zeiterfassung-database-url -
openssl rand -base64 48 | podman secret create zeiterfassung-django-secret-key -
```

`zeiterfassung-db` ist der Containername der Datenbank; die Namensaufloesung im
Podman-Netz macht daraus die richtige Adresse.

Django liest diese drei Werte aus Dateien, nicht aus der Umgebung
(`DJANGO_SECRET_KEY_FILE`, `DATABASE_URL_FILE`). Der Grund: eine
Umgebungsvariable steht in `podman inspect`, im Journal eines fehlgeschlagenen
Starts und in der Umgebung jedes Kindprozesses. Unterstuetzt wird das fuer
`DJANGO_SECRET_KEY`, `DATABASE_URL`, `DJANGO_EMAIL_HOST_PASSWORD`,
`KEYCLOAK_CLIENT_SECRET` und `ENTRA_CLIENT_SECRET`; eine gesetzte
Umgebungsvariable hat weiterhin Vorrang, damit der Compose-Weg unveraendert
funktioniert.

Fuer ein weiteres Geheimnis, zum Beispiel das Mailpasswort:

```bash
printf '%s' 'geheim' | podman secret create zeiterfassung-email-password -
```

und in der Unit `zeiterfassung-web.container` ergaenzen:

```ini
Environment=DJANGO_EMAIL_HOST_PASSWORD_FILE=/run/secrets/email-password
Secret=zeiterfassung-email-password,type=mount,target=email-password
```

### 3. Konfiguration hinterlegen

```bash
install -Dm600 deploy/quadlet/zeiterfassung.env.example \
    ~/.config/zeiterfassung/zeiterfassung.env
$EDITOR ~/.config/zeiterfassung/zeiterfassung.env
```

Mindestens `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` und
`SITE_BASE_URL` anpassen.

### 4. Units einsetzen

```bash
install -Dm644 -t ~/.config/containers/systemd/ \
    deploy/quadlet/zeiterfassung.network \
    deploy/quadlet/zeiterfassung-pgdata.volume \
    deploy/quadlet/zeiterfassung-db.container \
    deploy/quadlet/zeiterfassung-web.container \
    deploy/quadlet/zeiterfassung-scheduler.container

install -Dm644 -t ~/.config/systemd/user/ \
    deploy/quadlet/zeiterfassung-scheduler.timer

systemctl --user daemon-reload
```

Der Timer gehoert nicht in das Quadlet-Verzeichnis: Quadlet erzeugt aus
`zeiterfassung-scheduler.container` die Unit `zeiterfassung-scheduler.service`,
und der Timer ist eine gewoehnliche systemd-Unit, die diesen Dienst startet.

Ob die Units fehlerfrei uebersetzt wurden, zeigt:

```bash
/usr/libexec/podman/quadlet -user -dryrun
```

### 5. Starten

```bash
systemctl --user start zeiterfassung-web.service
systemctl --user enable --now zeiterfassung-scheduler.timer

systemctl --user status zeiterfassung-web.service
podman ps
```

`zeiterfassung-db.service` wird durch `Requires=` mitgestartet. Der erste Start
dauert laenger: der Webcontainer wartet auf die Datenbank und wendet die
Migrationen an (`journalctl --user -u zeiterfassung-web -f`).

### 6. Ersten Zugang einrichten

```bash
podman exec -it zeiterfassung-web python manage.py createsuperuser
```

Danach wie im [README](../README.md) beschrieben unter `/admin/` anmelden und
die Anmeldung ueber Keycloak oder Entra ID einrichten.

## Punkte, die rootless eine Rolle spielen

**Benutzer im Container.** Das Image legt Benutzer und Gruppe `app` mit UID und
GID 10001 an und laeuft nicht als root. Rootless bildet Podman diese UID auf den
Subuid-Bereich des Hostbenutzers ab; im Container heisst sie weiterhin 10001,
auf dem Host gehoeren die Dateien einer hohen, sonst unbenutzten UID. Das ist
gewollt und braucht keine Anpassung, solange keine Hostverzeichnisse eingehaengt
werden.

**Volumes.** Die Datenbank liegt in einem benannten Volume
(`zeiterfassung-pgdata.volume`, tatsaechlich unter
`~/.local/share/containers/storage/volumes/`). Ein Bind-Mount auf ein
Hostverzeichnis braeuchte rootless erst `podman unshare chown 999:999 <pfad>`,
damit der PostgreSQL-Benutzer im Container schreiben darf, und unter SELinux
zusaetzlich die Option `:Z`:

```ini
Volume=/srv/zeiterfassung/pgdata:/var/lib/postgresql/data:Z
```

Ohne `:Z` verbietet SELinux dem Container den Zugriff (`Permission denied` trotz
passender Dateirechte). Bei benannten Volumes setzt Podman das Label selbst.

**Ports.** Rootless duerfen keine Ports unter 1024 belegt werden. Die Unit
veroeffentlicht deshalb `127.0.0.1:8000`; davor gehoert ein Reverse Proxy, der
HTTPS beendet und auf diesen Port weiterleitet. Mit
`DJANGO_BEHIND_PROXY=true` erkennt Django die Verbindung dann als HTTPS.

**Rechte im Container.** Web und Scheduler laufen mit `ReadOnly=true`,
`NoNewPrivileges=true` und `DropCapability=ALL`. Der Anwendungscode aendert sich
zur Laufzeit nicht, statische Dateien entstehen beim Bauen des Images, und
gunicorn braucht nur ein beschreibbares `/tmp`, das Podman bei `--read-only`
selbst als tmpfs einhaengt. Die Datenbank laeuft ohne diese Einschraenkungen,
weil der PostgreSQL-Einstiegspunkt beim ersten Start das Datenverzeichnis
anlegen und die Rechte setzen muss.

**Healthchecks.** Das Image bringt einen Healthcheck mit, den Docker und Podman
gleichermassen benutzen. Er fragt `/healthz` an, einen Endpunkt, der nur sagt,
dass der Prozess antwortet. `/readyz` prueft zusaetzlich die Datenbank und ist
das, was ein Loadbalancer fragen sollte; als Healthcheck des Containers waere es
falsch, weil eine kurze Stoerung der Datenbank sonst den Webdienst neu startet.
Beide Endpunkte werden von einer Middleware beantwortet, bevor Django den Host
prueft, sonst wuerde eine Anfrage an `127.0.0.1` an `DJANGO_ALLOWED_HOSTS`
scheitern. Der Scheduler-Container hat keinen Webserver und schaltet den
Healthcheck mit `HealthCmd=none` ab.

**Start beim Hochfahren.** Rootless-Dienste starten nicht beim Systemstart,
sondern beim ersten Login des Benutzers, es sei denn, Lingering ist
eingeschaltet (siehe Voraussetzungen).

## Betrieb

```bash
# Protokolle
journalctl --user -u zeiterfassung-web -f
journalctl --user -u zeiterfassung-scheduler --since today

# Naechster Lauf des Timers
systemctl --user list-timers zeiterfassung-scheduler.timer

# Lauf von Hand ausloesen
systemctl --user start zeiterfassung-scheduler.service

# Zustand der Healthchecks
podman healthcheck run zeiterfassung-web
podman inspect --format '{{.State.Health.Status}}' zeiterfassung-db
```

**Neue Version einspielen**

```bash
git pull
podman build -t localhost/zeiterfassung:latest .
systemctl --user restart zeiterfassung-web.service
```

Die Migrationen laufen beim Start des Webcontainers.

**Sicherung**

```bash
podman exec zeiterfassung-db pg_dump -U zeiterfassung zeiterfassung \
    | gzip > zeiterfassung-$(date +%F).sql.gz
```

**Geheimnis wechseln.** Podman-Secrets lassen sich nicht aendern, nur ersetzen:

```bash
systemctl --user stop zeiterfassung-web.service
podman secret rm zeiterfassung-django-secret-key
openssl rand -base64 48 | podman secret create zeiterfassung-django-secret-key -
systemctl --user start zeiterfassung-web.service
```

Ein neuer `DJANGO_SECRET_KEY` macht alle Sitzungen ungueltig, alle Nutzer
muessen sich neu anmelden.

## Statt rootless als root

Die Units funktionieren auch systemweit. Dann gehoeren die Container-Units nach
`/etc/containers/systemd/`, der Timer nach `/etc/systemd/system/`, die Befehle
laufen mit `sudo` und ohne `--user`, und `%h` in `EnvironmentFile=` zeigt auf
`/root`. Sinnvoller ist in dem Fall ein fester Pfad:

```ini
EnvironmentFile=/etc/zeiterfassung/zeiterfassung.env
```

Lingering wird dann nicht gebraucht, Ports unter 1024 sind moeglich.

## Bekannte Grenzen

* Die Units gehen von einem lokal gebauten Image aus
  (`localhost/zeiterfassung:latest`). Wer aus einer Registry zieht, traegt dort
  den vollen Namen ein und kann mit `AutoUpdate=registry` und
  `podman-auto-update.timer` automatisch aktualisieren.
* Es gibt keinen Reverse Proxy in diesem Aufbau. TLS, HTTP/2 und die
  Weiterleitung auf `127.0.0.1:8000` macht nginx, Caddy oder Traefik auf dem
  Host.
* Mit Podman 5 kann `Notify=healthy` in `zeiterfassung-db.container` die Unit
  erst als gestartet melden, wenn der Healthcheck gruen ist. Die Zeile fehlt
  absichtlich, weil sie bei aelteren Podman-Versionen die Unit gar nicht erst
  erzeugt; noetig ist sie nicht, weil der Webcontainer selbst auf die Datenbank
  wartet.
