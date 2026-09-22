from django import forms
from django.forms import modelformset_factory
from django.utils import timezone

from .logo import ERLAUBTE_FORMATE, MAX_LOGO_BYTES, erkenne_bildtyp
from .models import FooterLink, SiteSettings


class SiteSettingsForm(forms.ModelForm):
    """Kopf- und Fußzeile, wie ein System-Admin sie pflegt (Issue 77)."""

    logo = forms.FileField(
        label="Logo",
        required=False,
        help_text=(
            f"{ERLAUBTE_FORMATE}, höchstens {MAX_LOGO_BYTES // 1024} KB. "
            "Leer lassen behält das bisherige Logo."
        ),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/png,image/jpeg,image/gif,image/webp"}
        ),
    )
    logo_entfernen = forms.BooleanField(label="Logo entfernen", required=False)

    class Meta:
        model = SiteSettings
        fields = [
            "site_title",
            "company_name",
            "logo_alt_text",
            "footer_text",
            "imprint_text",
            "imprint_url",
            "privacy_text",
            "privacy_url",
        ]
        widgets = {
            "footer_text": forms.Textarea(attrs={"rows": 4}),
            "imprint_text": forms.Textarea(attrs={"rows": 8}),
            "privacy_text": forms.Textarea(attrs={"rows": 8}),
        }

    field_order = [
        "site_title",
        "company_name",
        "logo",
        "logo_entfernen",
        "logo_alt_text",
        "footer_text",
        "imprint_text",
        "imprint_url",
        "privacy_text",
        "privacy_url",
    ]

    def clean_logo(self):
        hochgeladen = self.cleaned_data.get("logo")
        if not hochgeladen:
            return None

        if hochgeladen.size > MAX_LOGO_BYTES:
            raise forms.ValidationError(
                f"Das Bild ist {hochgeladen.size // 1024} KB groß, "
                f"erlaubt sind {MAX_LOGO_BYTES // 1024} KB."
            )

        daten = hochgeladen.read()
        content_type = erkenne_bildtyp(daten)
        if content_type is None:
            # Der Medientyp aus dem Browser wird absichtlich nicht geglaubt:
            # er kommt vom Absender, der Dateiinhalt entscheidet.
            raise forms.ValidationError(
                f"Das ist kein Bild in einem erlaubten Format ({ERLAUBTE_FORMATE})."
            )

        return {"daten": daten, "content_type": content_type, "name": hochgeladen.name[:120]}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("logo") and cleaned.get("logo_entfernen"):
            raise forms.ValidationError(
                "Entweder ein neues Logo hochladen oder das bisherige entfernen, nicht beides."
            )
        return cleaned

    def save(self, commit=True):
        site = super().save(commit=False)
        neues_logo = self.cleaned_data.get("logo")
        if neues_logo:
            site.logo_data = neues_logo["daten"]
            site.logo_content_type = neues_logo["content_type"]
            site.logo_filename = neues_logo["name"]
            site.logo_updated_at = timezone.now()
        elif self.cleaned_data.get("logo_entfernen"):
            site.logo_data = b""
            site.logo_content_type = ""
            site.logo_filename = ""
            site.logo_updated_at = None
        if commit:
            site.save()
        return site


class FooterLinkForm(forms.ModelForm):
    """Eine Zeile der Verweisliste."""

    class Meta:
        model = FooterLink
        fields = ["label", "url", "position"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ohne Angabe steht der Verweis vorne. Eine Pflichtangabe wäre hier
        # nur eine Hürde: die Reihenfolge interessiert erst, wenn mehrere
        # Verweise nebeneinander stehen.
        self.fields["position"].required = False

    def clean_position(self):
        return self.cleaned_data.get("position") or 0


FooterLinkFormSet = modelformset_factory(
    FooterLink,
    form=FooterLinkForm,
    extra=2,
    can_delete=True,
)
