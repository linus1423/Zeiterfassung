from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class Group(models.Model):
    """Organisationseinheit, zum Beispiel eine Abteilung oder ein Standort."""

    name = models.CharField("Name", max_length=120, unique=True)
    slug = models.SlugField("Kurzname", max_length=140, unique=True, blank=True)
    cost_center = models.CharField("Kostenstelle", max_length=50, blank=True)
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


class GroupMembership(models.Model):
    """Zuordnung eines Nutzers zu einer Gruppe, samt Rolle in dieser Gruppe."""

    class Role(models.TextChoices):
        MEMBER = "member", "Mitglied"
        ADMIN = "admin", "Gruppen-Admin"

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


class Activity(models.Model):
    """Taetigkeit, auf die gestempelt werden kann. Gehoert genau einer Gruppe."""

    group = models.ForeignKey(
        Group, verbose_name="Gruppe", on_delete=models.CASCADE, related_name="activities"
    )
    name = models.CharField("Name", max_length=120)
    description = models.TextField("Beschreibung", blank=True)
    is_active = models.BooleanField(
        "Aktiv",
        default=True,
        help_text="Deaktivierte Taetigkeiten sind nicht mehr waehlbar, "
        "alte Zeiteintraege bleiben erhalten.",
    )
    sort_order = models.PositiveIntegerField("Reihenfolge", default=100)

    class Meta:
        verbose_name = "Taetigkeit"
        verbose_name_plural = "Taetigkeiten"
        ordering = ["group__name", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["group", "name"], name="unique_activity_per_group")
        ]

    def __str__(self) -> str:
        return self.name
