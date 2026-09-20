from datetime import UTC, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def elapsed(start, end) -> timedelta:
    """Die tatsächlich vergangene Zeit zwischen zwei Zeitpunkten.

    Über UTC gerechnet, weil Python zwei Zeitpunkte mit derselben Zeitzone
    ohne Rücksicht auf die Umstellung zwischen Sommer- und Winterzeit
    voneinander abzieht (Issue 32).
    """
    return end.astimezone(UTC) - start.astimezone(UTC)


class TimeEntryQuerySet(models.QuerySet):
    def open(self):
        return self.filter(end__isnull=True)

    def closed(self):
        return self.filter(end__isnull=False)

    def overlapping(self, user, start, end=None):
        """Einträge desselben Nutzers, die sich mit [start, end) überschneiden."""
        qs = self.filter(user=user)
        if end is None:
            return qs.filter(Q(end__isnull=True) | Q(end__gt=start))
        return qs.filter(Q(start__lt=end) & (Q(end__isnull=True) | Q(end__gt=start)))


class TimeEntry(models.Model):
    """Eine Stempelung von Start bis Stop. Pausen hängen als BreakEntry daran."""

    class Source(models.TextChoices):
        CLOCK = "clock", "Gestempelt"
        CORRECTION = "correction", "Korrigiert"
        IMPORT = "import", "Importiert"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Nutzer",
        on_delete=models.PROTECT,
        related_name="time_entries",
    )
    group = models.ForeignKey(
        "groups.Group",
        verbose_name="Gruppe",
        on_delete=models.PROTECT,
        related_name="time_entries",
    )
    activity = models.ForeignKey(
        "groups.Activity",
        verbose_name="Tätigkeit",
        on_delete=models.PROTECT,
        related_name="time_entries",
        null=True,
        blank=True,
    )
    start = models.DateTimeField("Beginn")
    end = models.DateTimeField("Ende", null=True, blank=True)
    note = models.TextField("Notiz", blank=True)
    source = models.CharField(
        "Erfassungsart", max_length=12, choices=Source.choices, default=Source.CLOCK
    )
    is_incomplete = models.BooleanField(
        "Unvollständig",
        default=False,
        help_text="Automatisch beendet, weil das Ausstempeln vergessen wurde.",
    )
    created_at = models.DateTimeField("Angelegt am", auto_now_add=True)
    updated_at = models.DateTimeField("Geändert am", auto_now=True)

    objects = TimeEntryQuerySet.as_manager()

    class Meta:
        verbose_name = "Zeiteintrag"
        verbose_name_plural = "Zeiteinträge"
        ordering = ["-start"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(end__isnull=True),
                name="unique_open_time_entry_per_user",
            ),
            models.CheckConstraint(
                condition=Q(end__isnull=True) | Q(end__gt=F("start")),
                name="time_entry_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-start"]),
            models.Index(fields=["group", "-start"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} ab {timezone.localtime(self.start):%d.%m.%Y %H:%M}"

    def clean(self):
        if self.end and self.end <= self.start:
            raise ValidationError({"end": "Das Ende muss nach dem Beginn liegen."})
        if self.activity and self.activity.group_id != self.group_id:
            raise ValidationError({"activity": "Die Tätigkeit gehört zu einer anderen Gruppe."})

    @property
    def is_open(self) -> bool:
        return self.end is None

    @property
    def gross_duration(self) -> timedelta:
        """Anwesenheit inklusive Pausen."""
        return elapsed(self.start, self.end or timezone.now())

    @property
    def break_duration(self) -> timedelta:
        total = timedelta()
        for pause in self.breaks.all():
            total += pause.duration
        return total

    @property
    def duration(self) -> timedelta:
        """Arbeitszeit, also Anwesenheit abzüglich Pausen."""
        value = self.gross_duration - self.break_duration
        return value if value > timedelta() else timedelta()

    @property
    def spans_days(self) -> bool:
        """Läuft der Eintrag über Mitternacht?

        Dann zählt er anteilig zu beiden Tagen (Issue 32), und die Anzeige
        weist darauf hin, damit die Zeile und die Tagessumme zusammenpassen.
        """
        if self.end is None:
            return False
        # Eine Minute vor dem Ende: ein Eintrag, der um Punkt Mitternacht
        # endet, gehört noch ganz zum Vortag.
        last = timezone.localtime(self.end - timedelta(microseconds=1)).date()
        return timezone.localtime(self.start).date() != last

    @property
    def open_break(self):
        return self.breaks.filter(end__isnull=True).first()

    @property
    def is_on_break(self) -> bool:
        return self.breaks.filter(end__isnull=True).exists()


class BreakEntry(models.Model):
    """Pause innerhalb eines Zeiteintrags."""

    time_entry = models.ForeignKey(
        TimeEntry, verbose_name="Zeiteintrag", on_delete=models.CASCADE, related_name="breaks"
    )
    start = models.DateTimeField("Beginn")
    end = models.DateTimeField("Ende", null=True, blank=True)
    is_automatic = models.BooleanField("Automatisch", default=False)

    class Meta:
        verbose_name = "Pause"
        verbose_name_plural = "Pausen"
        ordering = ["start"]
        constraints = [
            models.UniqueConstraint(
                fields=["time_entry"],
                condition=Q(end__isnull=True),
                name="unique_open_break_per_entry",
            ),
            models.CheckConstraint(
                condition=Q(end__isnull=True) | Q(end__gt=F("start")),
                name="break_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"Pause ab {timezone.localtime(self.start):%H:%M}"

    @property
    def is_open(self) -> bool:
        return self.end is None

    @property
    def duration(self) -> timedelta:
        return elapsed(self.start, self.end or timezone.now())
