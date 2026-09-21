"""Rückspielen einer Sicherung (Issue 53).

Gefragt wird vorher, weil der vorhandene Datenbestand dabei verloren geht.
Für Skripte gibt es --noinput; dann läuft es ohne Rückfrage durch.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.audit import backup


class Command(BaseCommand):
    help = (
        "Spielt eine Sicherung zurück und überschreibt dabei den vorhandenen "
        "Datenbestand. Ohne --file wird die neueste Sicherung genommen."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            dest="file",
            default=None,
            help="Die zurückzuspielende Datei (Vorgabe: die neueste im Verzeichnis).",
        )
        parser.add_argument(
            "--dir",
            dest="directory",
            default=None,
            help="Verzeichnis, in dem nach der neuesten Sicherung gesucht wird.",
        )
        parser.add_argument(
            "--noinput",
            "--no-input",
            dest="interactive",
            action="store_false",
            help="Ohne Rückfrage zurückspielen, für Skripte und Timer.",
        )
        parser.add_argument(
            "--database",
            default="default",
            help="In welche Datenbank aus DATABASES zurückgespielt wird.",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        try:
            config = backup.database_config(alias)
            source = self._source(options)
        except backup.BackupError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(f"Sicherung: {source}")
        self.stdout.write(f"  Größe: {backup.format_size(source.stat().st_size)}")
        self.stdout.write(f"  Ziel: {backup.describe(config)}")
        self.stdout.write(
            self.style.WARNING("Der vorhandene Datenbestand wird dabei überschrieben.")
        )

        if options["interactive"] and not self._confirmed():
            self.stdout.write(self.style.WARNING("Abgebrochen, nichts geändert."))
            return

        try:
            seconds = backup.restore(source, alias=alias)
        except backup.BackupError as error:
            raise CommandError(str(error)) from error
        except OSError as error:
            raise CommandError(f"Rückspielen fehlgeschlagen: {error}") from error

        self.stdout.write(
            self.style.SUCCESS(f"Zurückgespielt in {backup.format_seconds(seconds)}.")
        )
        self.stdout.write(
            "Danach 'python manage.py migrate' ausführen, falls die Sicherung "
            "aus einer älteren Version stammt."
        )

    def _source(self, options) -> Path:
        if options["file"]:
            source = Path(options["file"]).expanduser().resolve()
            if not source.is_file():
                raise backup.BackupError(f"Die Datei gibt es nicht: {source}")
            return source
        directory = backup.backup_directory(options["directory"])
        found = backup.latest_backup(directory)
        if not found:
            raise backup.BackupError(f"In {directory} liegt keine Sicherung.")
        return found

    def _confirmed(self) -> bool:
        answer = input('Wirklich zurückspielen? Zum Bestätigen "ja" eingeben: ')
        return answer.strip().lower() == "ja"
