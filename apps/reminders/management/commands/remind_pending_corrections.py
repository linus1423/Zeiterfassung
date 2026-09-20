from django.core.management.base import BaseCommand

from apps.reminders import jobs


class Command(BaseCommand):
    help = "Erinnert Gruppen-Admins an Korrekturanträge, die zu lange offen liegen."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Frist in Tagen (Vorgabe: PENDING_CORRECTION_REMINDER_DAYS).",
        )

    def handle(self, *args, **options):
        result = jobs.remind_pending_corrections(options["days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.created} Erinnerungen angelegt, {result.mailed} versandt, "
                f"{result.resolved} erledigt."
            )
        )
