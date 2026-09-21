from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from .periods import MAX_MONTH_START_DAY, Period, group_period


class Group(models.Model):
    """Organisationseinheit, zum Beispiel eine Abteilung oder ein Standort."""

    name = models.CharField("Name", max_length=120, unique=True)
    slug = models.SlugField("Kurzname", max_length=140, unique=True, blank=True)
    cost_center = models.CharField("Kostenstelle", max_length=50, blank=True)
    month_start_day = models.PositiveSmallIntegerField(
        "Beginn des Abrechnungszeitraums",
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_MONTH_START_DAY)],
        help_text=(
            "1 bedeutet Kalendermonat. Bei 15 läuft ein Zeitraum vom 15. bis zum 14. "
            "des Folgemonats. Höchstens 28, damit es den Tag in jedem Monat gibt."
        ),
    )
    idp_identifier = models.CharField(
        "Bezeichnung beim Identity-Provider",
        max_length=200,
        blank=True,
        help_text=(
            "Name der Gruppe im Token des Identity-Providers. Leer bedeutet: es gilt der Kurzname."
        ),
    )
    is_active = models.BooleanField("Aktiv", default=True)
    created_at = models.DateTimeField("Angelegt am", auto_now_add=True)

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="GroupMembership",
        related_name="work_groups",
        verbose_name="Mitglieder",
    )

    class Meta:
        verbose_name = "Gruppe"
        verbose_name_plural = "Gruppen"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("groups:detail", args=[self.pk])

    def admins(self):
        return self.memberships.filter(role=GroupMembership.Role.ADMIN).select_related("user")

    def admin_emails(self) -> list[str]:
        return [
            membership.user.email
            for membership in self.admins()
            if membership.user.is_active and membership.user.email
        ]

    @property
    def idp_names(self) -> set[str]:
        """Bezeichnungen, unter denen der Provider diese Gruppe liefern kann."""
        names = {self.slug, self.name}
        if self.idp_identifier:
            names.add(self.idp_identifier)
        return {name.strip().casefold() for name in names if name}

    def current_period(self, day=None) -> Period:
        return group_period(self, day)


class GroupMembership(models.Model):
    """Zuordnung eines Nutzers zu einer Gruppe, samt Rolle in dieser Gruppe."""

    class Role(models.TextChoices):
        MEMBER = "member", "Mitglied"
        ADMIN = "admin", "Gruppen-Admin"

    class Source(models.TextChoices):
        MANUAL = "manual", "Im Tool zugeordnet"
        IDP = "idp", "Aus dem Identity-Provider"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Nutzer",
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    group = models.ForeignKey(
        Group, verbose_name="Gruppe", on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField("Rolle", max_length=10, choices=Role.choices, default=Role.MEMBER)
    source = models.CharField(
        "Herkunft",
        max_length=10,
        choices=Source.choices,
        default=Source.MANUAL,
        help_text="Nur Mitgliedschaften aus dem Identity-Provider werden dort auch entzogen.",
    )
    joined_at = models.DateTimeField("Mitglied seit", auto_now_add=True)

    class Meta:
        verbose_name = "Mitgliedschaft"
        verbose_name_plural = "Mitgliedschaften"
        ordering = ["group__name", "user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["user", "group"], name="unique_membership_per_group")
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.group} ({self.get_role_display()})"

    @property
    def is_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    @property
    def from_idp(self) -> bool:
        return self.source == self.Source.IDP


class Activity(models.Model):
    """Tätigkeit, auf die gestempelt werden kann. Gehört genau einer Gruppe."""

    group = models.ForeignKey(
        Group, verbose_name="Gruppe", on_delete=models.CASCADE, related_name="activities"
    )
    name = models.CharField("Name", max_length=120)
    description = models.TextField("Beschreibung", blank=True)
    is_active = models.BooleanField(
        "Aktiv",
        default=True,
        help_text="Deaktivierte Tätigkeiten sind nicht mehr wählbar, "
        "alte Zeiteinträge bleiben erhalten.",
    )
    sort_order = models.PositiveIntegerField("Reihenfolge", default=100)

    class Meta:
        verbose_name = "Tätigkeit"
        verbose_name_plural = "Tätigkeiten"
        ordering = ["group__name", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["group", "name"], name="unique_activity_per_group")
        ]

    def __str__(self) -> str:
        return self.name


class PeriodLock(models.Model):
    """Abschluss eines Abrechnungszeitraums einer Gruppe.

    Nach dem Abschluss lehnt das System Korrekturen für diesen Zeitraum ab,
    damit ein bereits exportierter Monat nicht nachträglich abweicht
    (Issue 5). Aufheben darf das nur ein System-Admin.
    """

    group = models.ForeignKey(
        Group, verbose_name="Gruppe", on_delete=models.CASCADE, related_name="period_locks"
    )
    period_start = models.DateField("Beginn des Zeitraums")
    period_end = models.DateField("Ende des Zeitraums")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Abgeschlossen von",
        on_delete=models.SET_NULL,
        related_name="closed_periods",
        null=True,
        blank=True,
    )
    closed_at = models.DateTimeField("Abgeschlossen am", auto_now_add=True)
    note = models.TextField("Bemerkung", blank=True)

    class Meta:
        verbose_name = "Abschluss"
        verbose_name_plural = "Abschlüsse"
        ordering = ["-period_start", "group__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "period_start"], name="unique_period_lock_per_group"
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="period_lock_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["group", "period_start", "period_end"])]

    def __str__(self) -> str:
        return f"{self.group.name}: {self.period_start:%d.%m.%Y} bis {self.period_end:%d.%m.%Y}"

    @property
    def period(self) -> Period:
        return Period(
            start=self.period_start,
            end=self.period_end,
            start_day=self.period_start.day,
        )

    def contains(self, day) -> bool:
        return self.period_start <= day <= self.period_end


class PeriodConfirmation(models.Model):
    """Bestätigung des eigenen Abrechnungszeitraums durch das Mitglied (Issue 50).

    Bisher erfuhr ein Mitarbeitender vom Abschluss seines Zeitraums erst
    dadurch, dass er keinen Korrekturantrag mehr stellen konnte. Hier sagt er
    vorher selbst, dass seine Tage stimmen. Der Abschluss durch den Admin
    bleibt davon unberührt: die Bestätigung ist ein Hinweis, keine Sperre.

    Wann eine Bestätigung verfällt:
    Bestätigt wird nicht der Zeitraum als solcher, sondern der Stand der
    eigenen Zeiten, den die Person gesehen hat. Dieser Stand steht als zwei
    Zahlen in der Zeile: wie viele Zeiteinträge den Zeitraum berühren
    (entry_count) und wann davon zuletzt einer gespeichert wurde
    (last_change_at, aus TimeEntry.updated_at). Weicht später eines von
    beiden ab, ist die Bestätigung verfallen, und die Person bestätigt neu.
    So greift die Regel von allein: eine genehmigte Korrektur und eine
    Änderung durch den Admin speichern den Eintrag und heben damit
    last_change_at, ein Nachtrag und eine Löschung verändern die Anzahl.
    Keine andere Stelle im Code muss daran denken, eine Bestätigung zu
    entwerten, und im Zweifel verfällt sie eher zu oft als zu selten.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Nutzer",
        on_delete=models.CASCADE,
        related_name="period_confirmations",
    )
    group = models.ForeignKey(
        Group, verbose_name="Gruppe", on_delete=models.CASCADE, related_name="period_confirmations"
    )
    period_start = models.DateField("Beginn des Zeitraums")
    period_end = models.DateField("Ende des Zeitraums")
    confirmed_at = models.DateTimeField("Bestätigt am", default=timezone.now)
    entry_count = models.PositiveIntegerField(
        "Bestätigte Anzahl Zeiteinträge",
        default=0,
        help_text="Teil des bestätigten Stands: weicht die Anzahl später ab, "
        "ist die Bestätigung verfallen.",
    )
    last_change_at = models.DateTimeField(
        "Jüngste Änderung im Zeitraum",
        null=True,
        blank=True,
        help_text="Teil des bestätigten Stands: wird danach noch eine Zeit "
        "geändert, ist die Bestätigung verfallen.",
    )
    total_minutes = models.PositiveIntegerField(
        "Bestätigte Summe in Minuten",
        default=0,
        help_text="Die Summe, die beim Bestätigen auf dem Bildschirm stand.",
    )

    class Meta:
        verbose_name = "Bestätigung des Zeitraums"
        verbose_name_plural = "Bestätigungen des Zeitraums"
        ordering = ["-period_start", "user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group", "period_start"],
                name="unique_period_confirmation_per_user",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="period_confirmation_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["group", "period_start"])]

    def __str__(self) -> str:
        return f"{self.user} bestätigt {self.period_start:%d.%m.%Y} bis {self.period_end:%d.%m.%Y}"

    @property
    def period(self) -> Period:
        return Period(
            start=self.period_start,
            end=self.period_end,
            start_day=self.period_start.day,
        )

    @property
    def total(self) -> timedelta:
        """Die bestätigte Summe als Zeitspanne, für die Anzeige."""
        return timedelta(minutes=self.total_minutes)
