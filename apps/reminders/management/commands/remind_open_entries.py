from django.conf import settings
from django.core.management.base import BaseCommand

from apps.reminders import jobs


class Command(BaseCommand):
    help = "Erinnert Nutzer, die ungewöhnlich lange eingestempelt sind, ans Ausstempeln."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=None,
            help="Schwelle in Stunden (Vorgabe: OPEN_ENTRY_REMINDER_HOURS).",
        )

    def handle(self, *args, **options):
        hours = options["hours"] or settings.OPEN_ENTRY_REMINDER_HOURS
        if hours >= settings.MAX_OPEN_ENTRY_HOURS:
            self.stdout.write(
                self.style.WARNING(
                    f"Die Schwelle von {hours} Stunden liegt nicht unter "
                    f"MAX_OPEN_ENTRY_HOURS ({settings.MAX_OPEN_ENTRY_HOURS}). "
                    "Der Hinweis kommt dann zu spät oder gar nicht."
                )
            )
        result = jobs.remind_open_entries(options["hours"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{result.created} Erinnerungen angelegt, {result.mailed} versandt, "
                f"{result.resolved} erledigt."
            )
        )
