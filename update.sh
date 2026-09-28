#!/usr/bin/env bash
# Spielt eine neue Version der Zeiterfassung ein, ohne root.
#
# Gegenstück zu install-dnf.sh. Läuft als Dienstbenutzer, dem nach der
# Installation alles gehört, was ein Update anfasst: Quellen, Image, Units,
# Dienste. install-dnf.sh legt das Skript mit den Quellen ab, der Aufruf ist
# deshalb zum Beispiel
#
#   sudo -u zeiterfassung ~zeiterfassung/zeiterfassung/update.sh
#
# oder, als Dienstbenutzer angemeldet, einfach ~/zeiterfassung/update.sh.
# Als root aufgerufen wechselt es selbst zum Dienstbenutzer aus
# /etc/zeiterfassung/install.conf.
#
# Ablauf:
#   1. neue Version holen (git, Vorgabe aus der Installation) oder aus einem
#      angegebenen Verzeichnis nehmen
#   2. Datenbank sichern (zeiterfassung-backup.service)
#   3. Quellen ersetzen, Image bauen, das bisherige als :vorher behalten
#   4. Units aus der neuen Version übernehmen, eigene Anpassungen der
#      Installation (Port, Verzeichnisse, zusätzliche Geheimnisse) behalten
#   5. Webdienst neu starten, auf /readyz warten
#
# Was root braucht, macht dieses Skript nicht: Pakete (podman) aktualisieren,
# nginx umkonfigurieren, neue Fragen der Installation beantworten. Dafür
# weiterhin sudo ./install-dnf.sh.

set -Eeuo pipefail

readonly IMAGE="localhost/zeiterfassung:latest"
readonly IMAGE_VORHER="localhost/zeiterfassung:vorher"
readonly QUELLEN_MARKE=".von-install-dnf"
readonly GEMERKT="/etc/zeiterfassung/install.conf"
readonly STANDARD_REPO="https://github.com/linus1423/Zeiterfassung.git"
readonly STANDARD_REF="main"

# Zeilen, die install-dnf.sh je nach Konfiguration an die Units hängt. Sie
# werden aus den eingesetzten Units übernommen.
readonly ZUSATZ_MUSTER='^(Environment=(KEYCLOAK_CLIENT_SECRET|ENTRA_CLIENT_SECRET|DJANGO_EMAIL_HOST_PASSWORD)_FILE=|Secret=zeiterfassung-(keycloak-secret|entra-secret|email-password),)'

NICHT_INTERAKTIV=false
OHNE_SICHERUNG=false
ERZWINGEN=false
QUELLE=""
REF=""

# --- Ausgabe ----------------------------------------------------------------

if [ -t 1 ]; then
    FETT=$'\e[1m'
    GRUEN=$'\e[32m'
    GELB=$'\e[33m'
    ROT=$'\e[31m'
    NORMAL=$'\e[0m'
else
    FETT="" GRUEN="" GELB="" ROT="" NORMAL=""
fi

schritt() { printf '\n%s==> %s%s\n' "$FETT" "$1" "$NORMAL"; }
info() { printf '    %s\n' "$1"; }
ok() { printf '    %s✓%s %s\n' "$GRUEN" "$NORMAL" "$1"; }
warnung() { printf '    %s!%s %s\n' "$GELB" "$NORMAL" "$1" >&2; }
fehler() {
    printf '%sFehler:%s %s\n' "$ROT" "$NORMAL" "$1" >&2
    exit 1
}

# Nur in der obersten Shell melden, sonst steht jeder Abbruch doppelt da.
trap '[ "$BASH_SUBSHELL" -eq 0 ] && printf "%sAbbruch%s in Zeile %s: %s\n" "$ROT" "$NORMAL" "$LINENO" "$BASH_COMMAND" >&2' ERR

hilfe() {
    cat <<'EOF'
Aufruf als Dienstbenutzer: ~/zeiterfassung/update.sh [Optionen]

  --quelle DIR       neue Version aus DIR nehmen (z. B. ein eigener Klon)
                     statt sie mit git zu holen
  --ref REF          Branch oder Tag, der geholt wird
                     (Vorgabe: wie bei der Installation, sonst main)
  --ohne-sicherung   die Datenbank vorher nicht sichern
  --erzwingen        auch bauen, wenn die Version schon läuft
  --ja               keine Rückfrage
  --hilfe            diese Hilfe

Die Quelle für git steht in ~/.config/zeiterfassung/update.conf
(REPO_URL, REF); install-dnf.sh trägt dort den Klon ein, aus dem installiert
wurde.
EOF
}

argumente() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --quelle)
                [ $# -ge 2 ] || fehler "--quelle braucht ein Verzeichnis."
                QUELLE="$2"
                shift
                ;;
            --ref)
                [ $# -ge 2 ] || fehler "--ref braucht einen Branch oder Tag."
                REF="$2"
                shift
                ;;
            --ohne-sicherung) OHNE_SICHERUNG=true ;;
            --erzwingen) ERZWINGEN=true ;;
            --ja | -y) NICHT_INTERAKTIV=true ;;
            --hilfe | -h | --help)
                hilfe
                exit 0
                ;;
            *) fehler "Unbekanntes Argument: $1 (siehe --hilfe)" ;;
        esac
        shift
    done
}

# wert_aus DATEI KEY [VORGABE] – der letzte Wert KEY=... aus DATEI.
wert_aus() {
    local zeile=""
    [ -r "$1" ] && zeile="$(grep -E "^${2}=" "$1" | tail -n 1 || true)"
    if [ -n "$zeile" ]; then
        printf '%s' "${zeile#*=}"
    else
        printf '%s' "${3:-}"
    fi
}

# --- Als root aufgerufen: zum Dienstbenutzer wechseln ------------------------

als_dienstbenutzer_neu_starten() {
    local benutzer
    benutzer="$(wert_aus "$GEMERKT" BENUTZER)"
    [ -n "$benutzer" ] || fehler "Als root aufgerufen, aber $GEMERKT nennt keinen Dienstbenutzer. Bitte als Dienstbenutzer aufrufen."
    id "$benutzer" >/dev/null 2>&1 || fehler "Dienstbenutzer $benutzer gibt es nicht."
    local uid home skript
    uid="$(id -u "$benutzer")"
    [ "$uid" -ne 0 ] || fehler "Der Dienstbenutzer darf nicht UID 0 haben."
    home="$(getent passwd "$benutzer" | cut -d: -f6)"
    # Das eingesetzte Skript des Dienstbenutzers, nicht dieses: eine Datei,
    # die der Dienstbenutzer nicht ändern kann, würde nichts schützen, und
    # eine, die root gehört, soll er nicht ausführen müssen.
    skript="$(wert_aus "$GEMERKT" ZE_QUELLEN "$home/zeiterfassung")/update.sh"
    [ -f "$skript" ] || fehler "$skript fehlt. Erst mit sudo ./install-dnf.sh installieren oder aktualisieren."
    info "Wechsle zu $benutzer."
    cd "$home"
    exec runuser -u "$benutzer" -- env -i \
        PATH=/usr/local/bin:/usr/bin:/usr/local/sbin:/usr/sbin \
        LANG="${LANG:-C.UTF-8}" \
        TERM="${TERM:-dumb}" \
        USER="$benutzer" \
        LOGNAME="$benutzer" \
        HOME="$home" \
        XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
        ${https_proxy:+https_proxy="$https_proxy"} \
        ${HTTPS_PROXY:+HTTPS_PROXY="$HTTPS_PROXY"} \
        ${no_proxy:+no_proxy="$no_proxy"} \
        bash "$skript" "$@"
}

# --- Ablauf -----------------------------------------------------------------

voraussetzungen() {
    schritt "Voraussetzungen prüfen"
    # "sudo -u" setzt die Laufzeitumgebung nicht, ohne sie finden weder
    # "systemctl --user" noch rootless Podman ihren Zustand.
    : "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
    : "${DBUS_SESSION_BUS_ADDRESS:=unix:path=$XDG_RUNTIME_DIR/bus}"
    export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
    [ -S "$XDG_RUNTIME_DIR/bus" ] ||
        fehler "systemd --user läuft nicht für $(id -un). Ist Lingering an (loginctl enable-linger)?"

    QUADLET_DIR="$HOME/.config/containers/systemd"
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    ENV_DATEI="$HOME/.config/zeiterfassung/zeiterfassung.env"
    UPDATE_CONF="$HOME/.config/zeiterfassung/update.conf"
    [ -f "$QUADLET_DIR/zeiterfassung-web.container" ] ||
        fehler "Keine Installation für $(id -un) gefunden ($QUADLET_DIR). Erst sudo ./install-dnf.sh."

    local benoetigt
    for benoetigt in podman curl tar systemctl; do
        command -v "$benoetigt" >/dev/null || fehler "$benoetigt fehlt."
    done
    [ -x /usr/libexec/podman/quadlet ] || fehler "Quadlet fehlt (/usr/libexec/podman/quadlet)."

    # Das Quellverzeichnis ist das, in dem dieses Skript liegt – install-dnf.sh
    # legt es dort ab. Geleert wird es nur, wenn es die Marke trägt.
    ZE_QUELLEN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
    [ -f "$ZE_QUELLEN/$QUELLEN_MARKE" ] ||
        fehler "$ZE_QUELLEN stammt nicht von install-dnf.sh. Bitte das eingesetzte Skript aufrufen (~/zeiterfassung/update.sh)."
    AKTUELL="$(head -n 1 "$ZE_QUELLEN/$QUELLEN_MARKE")"
    ok "Installation von $(id -un) in $ZE_QUELLEN, Version ${AKTUELL:-unbekannt}"
}

# Holt die neue Version nach $NEU (ein Verzeichnis mit den Quellen).
holen() {
    schritt "Neue Version holen"
    ARBEIT="$(mktemp -d "${TMPDIR:-/tmp}/zeiterfassung-update.XXXXXX")"
    trap 'rm -rf "$ARBEIT"' EXIT

    if [ -n "$QUELLE" ]; then
        QUELLE="$(cd "$QUELLE" && pwd -P)" || fehler "--quelle: Verzeichnis nicht lesbar."
        if [ ! -f "$QUELLE/Dockerfile" ] || [ ! -d "$QUELLE/deploy/quadlet" ]; then
            fehler "$QUELLE ist kein Verzeichnis mit den Quellen der Zeiterfassung."
        fi
        NEU="$QUELLE"
        NEU_VERSION="$(git -c safe.directory="$QUELLE" -C "$QUELLE" rev-parse HEAD 2>/dev/null || echo unbekannt)"
        ok "aus $QUELLE"
    else
        command -v git >/dev/null || fehler "git fehlt: einmal 'sudo dnf install git' oder --quelle angeben."
        local repo
        repo="$(wert_aus "$UPDATE_CONF" REPO_URL "$STANDARD_REPO")"
        [ -n "$REF" ] || REF="$(wert_aus "$UPDATE_CONF" REF "$STANDARD_REF")"
        [[ "$repo" =~ ^[^[:space:]\"\'\\]+$ ]] || fehler "REPO_URL in $UPDATE_CONF ist ungültig."
        [[ "$REF" =~ ^[A-Za-z0-9._/-]+$ && "$REF" != -* ]] || fehler "Ungültiger Branch oder Tag: $REF"
        info "$repo ($REF)"
        git clone --quiet --depth 1 --branch "$REF" -- "$repo" "$ARBEIT/quellen"
        NEU="$ARBEIT/quellen"
        NEU_VERSION="$(git -C "$NEU" rev-parse HEAD)"
        ok "geholt"
    fi
    info "läuft:  ${AKTUELL:-unbekannt}"
    info "neu:    $NEU_VERSION"
    if [ "$NEU_VERSION" = "$AKTUELL" ] && [ "$NEU_VERSION" != unbekannt ] && ! $ERZWINGEN; then
        ok "Diese Version läuft schon, nichts zu tun (--erzwingen baut trotzdem)."
        exit 0
    fi
    if ! $NICHT_INTERAKTIV; then
        local antwort
        read -r -p "    Jetzt aktualisieren? [J/n]: " antwort || fehler "Eingabe abgebrochen."
        case "${antwort,,}" in
            "" | j | ja | y | yes) ;;
            *) fehler "Abgebrochen, nichts verändert." ;;
        esac
    fi
}

# --- Units ------------------------------------------------------------------

# zeile_aus DATEI PRAEFIX – die erste Zeile, die mit PRAEFIX beginnt.
zeile_aus() { grep -m 1 "^$2" "$1" || true; }

# ersetze_zeile DATEI PRAEFIX NEUE_ZEILE
ersetze_zeile() {
    local datei="$1" praefix="$2" neu="$3" tmp
    grep -q "^${praefix}" "$datei" || fehler "In der neuen $datei fehlt eine Zeile '${praefix}…'; bitte sudo ./install-dnf.sh."
    tmp="$(mktemp)"
    P="$praefix" N="$neu" awk '
        index($0, ENVIRON["P"]) == 1 { print ENVIRON["N"]; next }
        { print }
    ' "$datei" >"$tmp"
    cat "$tmp" >"$datei"
    rm -f "$tmp"
}

# zusatz_uebernehmen ALT NEU – hängt die Zusatzzeilen der eingesetzten Unit
# ALT, die NEU noch nicht hat, hinter die DATABASE_URL-Zeile von NEU.
zusatz_uebernehmen() {
    local alt="$1" neu="$2" zeilen="" zeile tmp
    [ -f "$alt" ] || return 0
    while IFS= read -r zeile; do
        grep -qxF -- "$zeile" "$neu" || zeilen+="$zeile"$'\n'
    done < <(grep -E "$ZUSATZ_MUSTER" "$alt" || true)
    [ -n "$zeilen" ] || return 0
    grep -q '^Secret=zeiterfassung-database-url' "$neu" ||
        fehler "In der neuen $(basename "$neu") fehlt die Zeile Secret=zeiterfassung-database-url; bitte sudo ./install-dnf.sh."
    tmp="$(mktemp)"
    Z="$zeilen" awk '
        { print }
        !fertig && index($0, "Secret=zeiterfassung-database-url") == 1 { printf "%s", ENVIRON["Z"]; fertig = 1 }
    ' "$neu" >"$tmp"
    cat "$tmp" >"$neu"
    rm -f "$tmp"
}

# Units der neuen Version vorbereiten, mit den Anpassungen der eingesetzten.
units_vorbereiten() {
    schritt "Units vorbereiten"
    UNITS="$ARBEIT/units"
    mkdir -p "$UNITS"
    cp "$NEU"/deploy/quadlet/*.container "$NEU"/deploy/quadlet/*.network \
        "$NEU"/deploy/quadlet/*.volume "$NEU"/deploy/quadlet/*.timer "$UNITS/"

    local port sicherung datenbank
    port="$(zeile_aus "$QUADLET_DIR/zeiterfassung-web.container" PublishPort=)"
    sicherung="$(zeile_aus "$QUADLET_DIR/zeiterfassung-backup.container" Volume=)"
    datenbank="$(zeile_aus "$QUADLET_DIR/zeiterfassung-db.container" Volume=)"
    [ -n "$port" ] || fehler "Die eingesetzte zeiterfassung-web.container hat keine PublishPort-Zeile."

    ersetze_zeile "$UNITS/zeiterfassung-web.container" PublishPort= "$port"
    [ -n "$sicherung" ] && ersetze_zeile "$UNITS/zeiterfassung-backup.container" Volume= "$sicherung"
    if [ -n "$datenbank" ]; then
        ersetze_zeile "$UNITS/zeiterfassung-db.container" Volume= "$datenbank"
        # Eigenes Datenverzeichnis statt Volume: die Volume-Unit wird nicht
        # gebraucht, genau wie bei der Installation.
        case "$datenbank" in
            Volume=zeiterfassung-pgdata.volume:*) ;;
            *) rm -f "$UNITS/zeiterfassung-pgdata.volume" ;;
        esac
    fi
    local unit
    for unit in zeiterfassung-web.container zeiterfassung-scheduler.container zeiterfassung-backup.container; do
        zusatz_uebernehmen "$QUADLET_DIR/$unit" "$UNITS/$unit"
    done

    # Erst prüfen, dann einsetzen: kaputte Units sollen die laufenden nicht
    # ersetzen.
    local pruef="$ARBEIT/pruef"
    mkdir -p "$pruef"
    cp "$UNITS"/*.container "$UNITS"/*.network "$pruef/"
    cp "$UNITS"/*.volume "$pruef/" 2>/dev/null || true
    if ! QUADLET_UNIT_DIRS="$pruef" /usr/libexec/podman/quadlet -user -dryrun >/dev/null 2>&1; then
        QUADLET_UNIT_DIRS="$pruef" /usr/libexec/podman/quadlet -user -dryrun || true
        fehler "Quadlet kann die neuen Units nicht übersetzen, siehe oben. Nichts verändert."
    fi

    PORT="${port#PublishPort=}"
    PORT="${PORT#*:}"
    PORT="${PORT%%:*}"
    [[ "$PORT" =~ ^[0-9]+$ ]] || fehler "Port aus '$port' nicht lesbar."
    ok "geprüft, Port $PORT"
}

units_einsetzen() {
    local datei ziel geaendert=0
    for datei in "$UNITS"/*; do
        case "$datei" in
            *.timer) ziel="$SYSTEMD_DIR/$(basename "$datei")" ;;
            *) ziel="$QUADLET_DIR/$(basename "$datei")" ;;
        esac
        if ! cmp -s "$datei" "$ziel"; then
            install -m 644 "$datei" "$ziel"
            info "neu: $(basename "$datei")"
            geaendert=$((geaendert + 1))
        fi
    done
    systemctl --user daemon-reload
    if [ "$geaendert" -eq 0 ]; then
        ok "Units unverändert"
    else
        ok "$geaendert Unit(s) übernommen"
    fi
}

# --- Schritte ---------------------------------------------------------------

sichern() {
    $OHNE_SICHERUNG && { warnung "Ohne Sicherung, wie gewünscht."; return 0; }
    schritt "Datenbank sichern"
    # Migrationen lassen sich nicht zurücknehmen; ohne frische Sicherung gäbe
    # es nach einem missglückten Update keinen Weg zurück.
    systemctl --user start zeiterfassung-backup.service ||
        fehler "Die Sicherung ist fehlgeschlagen (journalctl --user -u zeiterfassung-backup). Nichts verändert."
    ok "gesichert"
}

quellen_ersetzen() {
    schritt "Quellen ersetzen"
    if [ "$(cd "$NEU" && pwd -P)" = "$ZE_QUELLEN" ]; then
        fehler "--quelle darf nicht das Installationsverzeichnis selbst sein."
    fi
    # Wie bei der Installation: lokale Daten wie .git, .env oder eine
    # SQLite-Datenbank kommen nicht mit. Dieses Skript liest sich selbst
    # weiter aus der alten, schon gelöschten Datei; bash hält sie offen.
    tar -C "$NEU" -cf - \
        --exclude=./.git --exclude=./.env --exclude=./.venv --exclude=./venv \
        --exclude='./*.sqlite3' --exclude=./backups --exclude=./staticfiles . |
        (find "$ZE_QUELLEN" -mindepth 1 -delete && tar -C "$ZE_QUELLEN" --no-same-owner -xf -)
    # Die Version kommt erst am Ende in die Marke. Bricht das Update vorher ab,
    # hält ein neuer Lauf die Version nicht für schon eingespielt.
    printf 'unfertig\n' >"$ZE_QUELLEN/$QUELLEN_MARKE"
    ok "$ZE_QUELLEN"
}

bauen() {
    schritt "Image bauen"
    if podman image exists "$IMAGE"; then
        podman tag "$IMAGE" "$IMAGE_VORHER"
        info "bisheriges Image bleibt als $IMAGE_VORHER"
    fi
    podman build --pull=newer -t "$IMAGE" "$ZE_QUELLEN"
    podman pull -q docker.io/library/postgres:16 >/dev/null
    ok "gebaut"
}

neu_starten() {
    schritt "Webdienst neu starten"
    # Die Datenbank läuft weiter; Scheduler und Sicherung nehmen das neue
    # Image beim nächsten Lauf ihres Timers.
    systemctl --user start zeiterfassung-db.service
    systemctl --user restart zeiterfassung-web.service
    info "Warte auf den Webdienst (Migrationen) …"
    local _
    for _ in $(seq 1 150); do
        if curl --fail --silent --output /dev/null "http://127.0.0.1:${PORT}/readyz"; then
            ok "bereit auf 127.0.0.1:$PORT"
            return 0
        fi
        sleep 2
    done
    journalctl --user -u zeiterfassung-web --no-pager -n 40 >&2 || true
    warnung "Zurück zur vorigen Version, falls die Migrationen die Datenbank noch"
    warnung "nicht verändert haben:"
    warnung "  podman tag $IMAGE_VORHER $IMAGE"
    warnung "  systemctl --user restart zeiterfassung-web.service"
    warnung "Sonst zusätzlich die Sicherung von eben zurückspielen, siehe"
    warnung "docs/podman-quadlet.md, Abschnitt \"Zurückspielen\"."
    fehler "Der Webdienst ist nach 5 Minuten nicht bereit, siehe Protokoll oben."
}

neue_einstellungen() {
    # Einstellungen, die die neue Version kennt, die env-Datei aber noch nicht:
    # es gilt ihre Vorgabe. Nur ein Hinweis, geschrieben wird nichts.
    local beispiel="$ZE_QUELLEN/deploy/quadlet/zeiterfassung.env.example" key neu=""
    if [ ! -f "$beispiel" ] || [ ! -f "$ENV_DATEI" ]; then
        return 0
    fi
    while IFS= read -r key; do
        grep -q "^${key}=" "$ENV_DATEI" || neu+=" $key"
    done < <(grep -oE '^[A-Z][A-Z0-9_]*=' "$beispiel" | tr -d '=' | sort -u)
    if [ -n "$neu" ]; then
        info "Neue Einstellungen mit Vorgabe (bei Bedarf in $ENV_DATEI setzen):"
        info " $neu"
    fi
}

abschluss() {
    printf '%s\n' "$NEU_VERSION" >"$ZE_QUELLEN/$QUELLEN_MARKE"
    podman image prune -f >/dev/null 2>&1 || true
    schritt "Fertig"
    info "Version $NEU_VERSION läuft."
    neue_einstellungen
}

main() {
    argumente "$@"
    if [ "$(id -u)" -eq 0 ]; then
        als_dienstbenutzer_neu_starten "$@"
    fi
    voraussetzungen
    holen
    units_vorbereiten
    sichern
    quellen_ersetzen
    bauen
    units_einsetzen
    neu_starten
    abschluss
}

# Auf einer Zeile mit main: bash liest das Skript stückweise, und die Quellen
# samt dieser Datei werden unterwegs ersetzt.
main "$@"; exit $?
