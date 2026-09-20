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
