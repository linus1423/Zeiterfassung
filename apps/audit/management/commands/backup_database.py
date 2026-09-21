"""Sicherung der Datenbank (Issue 53).

Gedacht für einen systemd-Timer oder einen Cronjob: bei einem Fehler endet
das Kommando mit einem Rückgabewert ungleich null, damit der Ausfall auffällt
und nicht still im Journal verschwindet.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.audit import backup


class Command(BaseCommand):
    help = (
        "Schreibt eine Sicherung der Datenbank mit Zeitstempel im Dateinamen "
        "und räumt alte Sicherungen auf."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            dest="directory",
            default=None,
            help="Zielverzeichnis (Vorgabe: BACKUP_DIR).",
        )
        parser.add_argument(
            "--keep",
            type=int,
            default=None,
            help="So viele Sicherungen behalten, 0 für unbegrenzt (Vorgabe: BACKUP_KEEP).",
        )
        parser.add_argument(
            "--keep-days",
            type=int,
            default=None,
            help=(
                "Sicherungen entfernen, die älter sind als so viele Tage; "
                "0 schaltet die Altersgrenze ab (Vorgabe: BACKUP_KEEP_DAYS)."
            ),
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Welche Datenbank aus DATABASES gesichert wird.",
        )

    def handle(self, *args, **options):
        alias = options["database"]
        try:
            config = backup.database_config(alias)
            result = backup.create_backup(alias=alias, directory=options["directory"])
        except backup.BackupError as error:
            raise CommandError(str(error)) from error
        except OSError as error:
            raise CommandError(f"Sicherung fehlgeschlagen: {error}") from error

        self.stdout.write(self.style.SUCCESS(f"Sicherung geschrieben: {result.path}"))
        self.stdout.write(f"  Datenbank: {backup.describe(config)}")
        self.stdout.write(f"  Größe: {backup.format_size(result.size)}")
        self.stdout.write(f"  Dauer: {backup.format_seconds(result.seconds)}")

        try:
            removed = backup.cleanup(
                result.path.parent,
                keep=options["keep"],
                keep_days=options["keep_days"],
            )
        except OSError as error:
            raise CommandError(f"Aufräumen fehlgeschlagen: {error}") from error
        if removed:
            self.stdout.write(f"  Aufgeräumt: {backup.format_count(len(removed))} entfernt.")
