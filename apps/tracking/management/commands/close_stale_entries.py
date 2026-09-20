from django.core.management.base import BaseCommand

from apps.tracking.services import close_stale_entries


class Command(BaseCommand):
    help = "Beendet vergessene Zeiteinträge und markiert sie als unvollständig."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-hours",
            type=int,
            default=None,
            help="Höchstdauer in Stunden (Vorgabe: MAX_OPEN_ENTRY_HOURS).",
        )

    def handle(self, *args, **options):
        closed = close_stale_entries(options["max_hours"])
        self.stdout.write(self.style.SUCCESS(f"{closed} Einträge automatisch beendet."))
