from django.core.validators import URLValidator
from django.db import models
from django.urls import reverse


class WebURLField(models.URLField):
    """Adresse eines Verweises in der Oberfläche.

    Nur http und https. Die Vorgabe von Django erlaubt auch ftp; ein Verweis in
    der Fußzeile, der nicht im Browser aufgeht, hilft niemandem, und andere
    Schemata (etwa javascript:) haben in einem Link erst recht nichts zu
    suchen. Eine Eingabe ohne Schema wird als https gelesen.
    """

    default_validators = [URLValidator(schemes=["http", "https"])]

    def formfield(self, **kwargs):
        return super().formfield(**{"assume_scheme": "https", **kwargs})


class SiteSettings(models.Model):
    """Was auf jeder Seite in Kopf- und Fußzeile steht (Issue 77).

    Genau eine Zeile, angelegt beim ersten Speichern. Gepflegt wird sie von
    System-Admins in der Oberfläche, damit für Firmenname, Logo, Impressum und
    Datenschutz weder der Code noch die `.env` angefasst werden muss.
    """

    SINGLETON_PK = 1

    site_title = models.CharField(
        "Name der Anwendung",
        max_length=60,
        default="Zeiterfassung",
        help_text="Steht in der Kopfzeile und im Titel des Browserfensters.",
    )
    company_name = models.CharField(
        "Firma",
        max_length=120,
        blank=True,
        help_text="Erscheint in der Kopfzeile neben dem Namen und in der Fußzeile.",
    )
    # Das Logo liegt in der Datenbank und nicht im Dateisystem. Der Betrieb
    # läuft im Container (siehe docs/podman-quadlet.md) und kennt bisher kein
    # Verzeichnis für hochgeladene Dateien: ein solches Verzeichnis wäre ein
    # zusätzliches Volume, das beim Neubau leerlaufen kann und in keiner
    # Sicherung steckt. Ein Logo ist klein und selten geändert, in der
    # Datenbank ist es von "manage.py backup_database" mit erfasst.
    logo_data = models.BinaryField("Logo", blank=True, default=b"", editable=False)
    logo_content_type = models.CharField("Art des Logos", max_length=40, blank=True, editable=False)
    logo_filename = models.CharField("Dateiname", max_length=120, blank=True, editable=False)
    logo_updated_at = models.DateTimeField(
        "Logo geändert am", null=True, blank=True, editable=False
    )
    logo_alt_text = models.CharField(
        "Beschreibung des Logos",
        max_length=120,
        blank=True,
        help_text="Wird vorgelesen und angezeigt, solange das Bild fehlt. Leer: der Firmenname.",
    )
    footer_text = models.TextField(
        "Freitext in der Fußzeile",
        blank=True,
        help_text="Zum Beispiel Anschrift oder Ansprechpartner. Zeilenumbrüche bleiben erhalten.",
    )
    imprint_text = models.TextField(
        "Impressum",
        blank=True,
        help_text="Steht als eigene Seite im Tool. Leer und ohne Adresse: kein Verweis.",
    )
    imprint_url = WebURLField(
        "Impressum: Adresse",
        max_length=300,
        blank=True,
        help_text="Verweis auf eine bestehende Seite. Gesetzt, gilt sie statt des Textes.",
    )
    privacy_text = models.TextField(
        "Datenschutz",
        blank=True,
        help_text="Steht als eigene Seite im Tool. Leer und ohne Adresse: kein Verweis.",
    )
    privacy_url = WebURLField(
        "Datenschutz: Adresse",
        max_length=300,
        blank=True,
        help_text="Verweis auf eine bestehende Seite. Gesetzt, gilt sie statt des Textes.",
    )
    updated_at = models.DateTimeField("Geändert am", auto_now=True)

    class Meta:
        verbose_name = "Darstellung"
        verbose_name_plural = "Darstellung"

    def __str__(self) -> str:
        return self.site_title

    def save(self, *args, **kwargs):
        # Es gibt genau eine Zeile; ein zweiter Datensatz wäre ein stiller
        # Fehler, der sich erst in der Oberfläche zeigt.
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def current(cls) -> "SiteSettings":
        """Die gespeicherten Werte, sonst die Vorgaben.

        Bewusst ohne get_or_create: gelesen wird beim Aufbau jeder Seite, auch
        von nicht angemeldeten Besuchern, und dabei soll nichts geschrieben
        werden. Die Zeile entsteht erst, wenn ein System-Admin speichert.
        """
        return cls.objects.filter(pk=cls.SINGLETON_PK).first() or cls()

    @property
    def has_logo(self) -> bool:
        return bool(self.logo_data)

    @property
    def logo_version(self) -> str:
        """Kennung des Logos für die Adresse, damit der Browser ein neues Bild holt."""
        if self.logo_updated_at is None:
            return ""
        return str(int(self.logo_updated_at.timestamp()))

    @property
    def logo_description(self) -> str:
        return self.logo_alt_text or self.company_name or self.site_title

    @property
    def imprint_link(self) -> str:
        """Adresse des Impressums, leer wenn es keines gibt."""
        return self._link(self.imprint_url, self.imprint_text, "siteconfig:imprint")

    @property
    def privacy_link(self) -> str:
        return self._link(self.privacy_url, self.privacy_text, "siteconfig:privacy")

    @staticmethod
    def _link(url: str, text: str, view_name: str) -> str:
        if url:
            return url
        if text.strip():
            return reverse(view_name)
        return ""


class FooterLink(models.Model):
    """Weitere Verweise in der Fußzeile: Kontakt, Barrierefreiheit, Handbuch, ...

    Impressum und Datenschutz haben eigene Felder, weil sie zu einem Text im
    Tool gehören können. Alles andere ist ein Name und eine Adresse.
    """

    label = models.CharField("Beschriftung", max_length=60)
    url = WebURLField("Adresse", max_length=300)
    position = models.PositiveSmallIntegerField(
        "Reihenfolge", default=0, help_text="Kleinere Zahlen stehen weiter vorne."
    )

    class Meta:
        verbose_name = "Verweis in der Fußzeile"
        verbose_name_plural = "Verweise in der Fußzeile"
        ordering = ["position", "label"]

    def __str__(self) -> str:
        return self.label
