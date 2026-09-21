from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
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


class GroupChangeRequest(models.Model):
    """Antrag eines Nutzers, in eine andere Gruppe zu wechseln (Issue 37).

    Ein Wechsel betrifft zwei Gruppen, deshalb entscheiden beide nacheinander:
    erst der Admin der bisherigen Gruppe, der die Person abgibt, dann der Admin
    der neuen Gruppe, der sie aufnimmt. Erst nach beiden Zustimmungen wird die
    Mitgliedschaft umgehängt.

    Bereits erfasste Zeiten bleiben bei der alten Gruppe. Sie gehören zu deren
    Auswertung und Abschluss; nachträglich umzuhängen würde abgeschlossene
    Zeiträume verändern.
    """

    class Status(models.TextChoices):
        PENDING_SOURCE = "pending_source", "Wartet auf die bisherige Gruppe"
        PENDING_TARGET = "pending_target", "Wartet auf die neue Gruppe"
        APPROVED = "approved", "Genehmigt"
        REJECTED = "rejected", "Abgelehnt"
        WITHDRAWN = "withdrawn", "Zurückgezogen"

    OPEN_STATUSES = (Status.PENDING_SOURCE, Status.PENDING_TARGET)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Antragsteller",
        on_delete=models.CASCADE,
        related_name="group_change_requests",
    )
    from_group = models.ForeignKey(
        Group,
        verbose_name="Bisherige Gruppe",
        on_delete=models.PROTECT,
        related_name="change_requests_out",
    )
    to_group = models.ForeignKey(
        Group,
        verbose_name="Neue Gruppe",
        on_delete=models.PROTECT,
        related_name="change_requests_in",
    )
    reason = models.TextField("Begründung")
    status = models.CharField(
        "Status", max_length=20, choices=Status.choices, default=Status.PENDING_SOURCE
    )
    source_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Entschieden von (bisherige Gruppe)",
        on_delete=models.SET_NULL,
        related_name="decided_group_changes_out",
        null=True,
        blank=True,
    )
    source_decided_at = models.DateTimeField(
        "Entschieden am (bisherige Gruppe)", null=True, blank=True
    )
    source_note = models.TextField("Bemerkung der bisherigen Gruppe", blank=True)
    target_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Entschieden von (neue Gruppe)",
        on_delete=models.SET_NULL,
        related_name="decided_group_changes_in",
        null=True,
        blank=True,
    )
    target_decided_at = models.DateTimeField("Entschieden am (neue Gruppe)", null=True, blank=True)
    target_note = models.TextField("Bemerkung der neuen Gruppe", blank=True)
    decision_seen_at = models.DateTimeField(
        "Entscheidung gesehen am",
        null=True,
        blank=True,
        help_text="Solange leer, zählt die Entscheidung in der Navigation als neu.",
    )
    created_at = models.DateTimeField("Gestellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Antrag auf Gruppenwechsel"
        verbose_name_plural = "Anträge auf Gruppenwechsel"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(from_group=models.F("to_group")),
                name="group_change_between_two_groups",
            ),
            # Zwei offene Anträge derselben Person liefen auf zwei Wechsel
            # hinaus, von denen der zweite die Mitgliedschaft nicht mehr
            # vorfindet. Einer reicht.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status__in=("pending_source", "pending_target")),
                name="one_open_group_change_per_user",
            ),
        ]
        indexes = [models.Index(fields=["status", "to_group"])]

    def __str__(self) -> str:
        return f"{self.user} von {self.from_group} nach {self.to_group}"

    @property
    def is_pending(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def is_decided(self) -> bool:
        return self.status in (self.Status.APPROVED, self.Status.REJECTED)

    @property
    def deciding_group(self) -> Group | None:
        """Die Gruppe, die als Nächstes entscheidet."""
        if self.status == self.Status.PENDING_SOURCE:
            return self.from_group
        if self.status == self.Status.PENDING_TARGET:
            return self.to_group
        return None

    @property
    def decision_note(self) -> str:
        """Die Bemerkung zur letzten Entscheidung, für die Anzeige."""
        return self.target_note or self.source_note
