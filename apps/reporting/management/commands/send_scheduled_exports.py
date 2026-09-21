from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.reporting import schedules


class Command(BaseCommand):
    help = "Verschickt die fälligen geplanten Exporte an ihre Empfänger."

    def add_arguments(self, parser):
        parser.add_argument(
            "--today",
            default=None,
            help="Stichtag im Format JJJJ-MM-TT (Vorgabe: heute). Nur zum Nachstellen gedacht.",
        )

    def handle(self, *args, **options):
        stichtag = None
        if options["today"]:
            try:
                stichtag = date.fromisoformat(options["today"])
            except ValueError as exc:
                raise CommandError("--today erwartet ein Datum wie 2026-03-03.") from exc

        if not schedules.emails_enabled():
            self.stdout.write(
                self.style.WARNING(
                    "EXPORT_EMAILS_ENABLED ist aus: fällige Pläne werden als fehlgeschlagen "
                    "protokolliert und beim nächsten Lauf erneut versucht."
                )
            )

        result = schedules.send_due_exports(stichtag)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.sent} Exporte versandt, {result.failed} fehlgeschlagen, "
                f"{result.skipped} übersprungen."
            )
        )
