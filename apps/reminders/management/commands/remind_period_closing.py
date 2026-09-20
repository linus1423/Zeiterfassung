from django.core.management.base import BaseCommand

from apps.reminders import jobs


class Command(BaseCommand):
    help = "Erinnert Gruppen-Admins an abgelaufene Zeiträume, die noch offen sind."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Frist in Tagen nach Ende des Zeitraums (Vorgabe: PERIOD_CLOSING_REMINDER_DAYS).",
        )

    def handle(self, *args, **options):
        result = jobs.remind_period_closing(options["days"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.created} Erinnerungen angelegt, {result.mailed} versandt, "
                f"{result.resolved} erledigt."
            )
        )
