from django.conf import settings
from django.db import models


class ExportProfile(models.Model):
    """Gespeicherte Zusammenstellung für den Export.

    Damit muss die Buchhaltung Spalten, Filter und Verdichtung nicht jeden
    Monat neu zusammenklicken.
    """

    class Grouping(models.TextChoices):
        ENTRY = "entry", "Je Zeiteintrag"
        USER_DAY = "user_day", "Je Nutzer und Tag"
        USER_MONTH = "user_month", "Je Nutzer und Monat"
        ACTIVITY = "activity", "Je Tätigkeit"

    name = models.CharField("Name", max_length=120)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Besitzer",
        on_delete=models.CASCADE,
        related_name="export_profiles",
    )
    is_shared = models.BooleanField(
        "Geteilt", default=False, help_text="Auch für andere Nutzer der Buchhaltung sichtbar."
    )
    columns = models.JSONField("Spalten", default=list)
    grouping = models.CharField(
        "Verdichtung", max_length=16, choices=Grouping.choices, default=Grouping.ENTRY
    )
    filters = models.JSONField("Filter", default=dict, blank=True)
    created_at = models.DateTimeField("Angelegt am", auto_now_add=True)
    updated_at = models.DateTimeField("Geändert am", auto_now=True)

    class Meta:
        verbose_name = "Export-Vorlage"
        verbose_name_plural = "Export-Vorlagen"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_profile_name_per_owner")
        ]

    def __str__(self) -> str:
        return self.name


class ExportScheduleQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Eigene Pläne, die zu eigenen Vorlagen, und für die Buchhaltung alle."""
        if user.sees_all_groups:
            return self
        return self.filter(models.Q(created_by=user) | models.Q(profile__owner=user))

    def active(self):
        return self.filter(is_active=True)


class ExportSchedule(models.Model):
    """Zeitplan zu einer Export-Vorlage (Issue 55).

    Der Zeitraum steht nicht als festes Datum im Plan, sondern ergibt sich
    beim Lauf aus dem Abrechnungszeitraum: "am 3. jedes Monats für den
    letzten abgeschlossenen Abrechnungszeitraum". Welcher Zyklus gilt,
    bestimmt die Gruppe unter `period_group`; ohne Gruppe der Kalendermonat.

    Ausgewertet wird immer mit den Rechten von `created_by`, nicht mit denen
    des Vorlagenbesitzers. Wer eine geteilte Vorlage der Buchhaltung einplant,
    bekommt also nur die Zeiten, die er ohnehin sehen darf.
    """

    class Timeframe(models.TextChoices):
        PREVIOUS_PERIOD = "previous_period", "Letzter abgeschlossener Abrechnungszeitraum"
        CURRENT_PERIOD = "current_period", "Laufender Abrechnungszeitraum"
        LAST_TWELVE = "last_twelve", "Die letzten zwölf Abrechnungszeiträume"

    class Format(models.TextChoices):
        XLSX = "xlsx", "Excel (.xlsx)"
        CSV = "csv", "CSV"

    profile = models.ForeignKey(
        ExportProfile,
        verbose_name="Vorlage",
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Angelegt von",
        on_delete=models.CASCADE,
        related_name="export_schedules",
        help_text="Der Export läuft mit den Leserechten dieser Person.",
    )
    day_of_month = models.PositiveSmallIntegerField(
        "Tag im Monat",
        default=3,
        help_text="1 bis 28, damit es den Tag in jedem Monat gibt.",
    )
    timeframe = models.CharField(
        "Zeitraum",
        max_length=20,
        choices=Timeframe.choices,
        default=Timeframe.PREVIOUS_PERIOD,
    )
    period_group = models.ForeignKey(
        "groups.Group",
        verbose_name="Zyklus der Gruppe",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="export_schedules",
        help_text="Bestimmt den Abrechnungszyklus. Ohne Auswahl gilt der Kalendermonat.",
    )
    export_format = models.CharField(
        "Format", max_length=8, choices=Format.choices, default=Format.XLSX
    )
    recipients = models.JSONField("Empfänger", default=list)
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField("Angelegt am", auto_now_add=True)
    updated_at = models.DateTimeField("Geändert am", auto_now=True)

    objects = ExportScheduleQuerySet.as_manager()

    class Meta:
        verbose_name = "Export-Zeitplan"
        verbose_name_plural = "Export-Zeitpläne"
        ordering = ["profile__name", "day_of_month"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "created_by", "day_of_month"],
                name="unique_schedule_per_profile_and_day",
            ),
            models.CheckConstraint(
                condition=models.Q(day_of_month__gte=1, day_of_month__lte=28),
                name="schedule_day_between_1_and_28",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile.name} am {self.day_of_month}."

    @property
    def recipient_list(self) -> list[str]:
        """Die Empfänger als saubere Liste, auch bei kaputten Altdaten."""
        value = self.recipients or []
        if isinstance(value, str):
            value = [value]
        return [str(address).strip() for address in value if str(address).strip()]

    @property
    def recipients_label(self) -> str:
        return ", ".join(self.recipient_list)


class ExportRun(models.Model):
    """Ein Versand zu einer Fälligkeit eines Zeitplans (Issue 55).

    Je Plan und Fälligkeitstag gibt es höchstens eine Zeile; die
    Eindeutigkeitsbedingung über (schedule, due_on) ist der Riegel gegen
    Doppelversand. Der Status sagt, was daraus geworden ist.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Läuft"
        SENT = "sent", "Versandt"
        FAILED = "failed", "Fehlgeschlagen"
        BLOCKED = "blocked", "Dauerhaft fehlgeschlagen"

    schedule = models.ForeignKey(
        ExportSchedule, verbose_name="Zeitplan", on_delete=models.CASCADE, related_name="runs"
    )
    due_on = models.DateField("Fällig am")
    status = models.CharField("Status", max_length=10, choices=Status.choices)
    attempts = models.PositiveIntegerField("Versuche", default=1)
    started_at = models.DateTimeField("Begonnen am")
    finished_at = models.DateTimeField("Beendet am", null=True, blank=True)
    period_start = models.DateField("Zeitraum von", null=True, blank=True)
    period_end = models.DateField("Zeitraum bis", null=True, blank=True)
    row_count = models.PositiveIntegerField("Zeilen", default=0)
    size_bytes = models.PositiveBigIntegerField("Größe der Datei", default=0)
    message = models.TextField("Meldung", blank=True)

    class Meta:
        verbose_name = "Export-Lauf"
        verbose_name_plural = "Export-Läufe"
        ordering = ["-due_on", "-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["schedule", "due_on"], name="unique_run_per_due_date")
        ]
        indexes = [models.Index(fields=["schedule", "-due_on"])]

    def __str__(self) -> str:
        return f"{self.schedule} zum {self.due_on:%d.%m.%Y}: {self.get_status_display()}"
