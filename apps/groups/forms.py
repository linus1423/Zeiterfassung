from django import forms
from django.contrib.auth import get_user_model
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.siteconfig.models import MailDomain
from apps.tracking.forms import DateTimeLocalInput

from .models import Activity, Group, GroupMembership
from .periods import MAX_MONTH_START_DAY

User = get_user_model()


class ActivityForm(forms.ModelForm):
    class Meta:
        model = Activity
        fields = ["name", "description", "sort_order", "is_active"]


class EmailWithDomainWidget(forms.MultiWidget):
    """Eingabefeld für den Teil vor dem @, daneben die Auswahl der Domäne."""

    template_name = "groups/widgets/email_domain.html"

    def __init__(self, domains, attrs=None):
        choices = [(domain, domain) for domain in domains]
        super().__init__(
            [
                forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
                forms.Select(choices=choices, attrs={"aria-label": "Domäne"}),
            ],
            attrs,
        )
        self.domains = list(domains)

    def decompress(self, value):
        if value and "@" in value:
            local, domain = value.rsplit("@", 1)
            if domain in self.domains:
                return [local, domain]
        return [value, None]


class EmailWithDomainField(forms.MultiValueField):
    """Mail-Adresse aus dem Teil vor dem @ und einer hinterlegten Domäne.

    Enthält die Eingabe selbst ein @, gilt sie als ganze Adresse und die
    Auswahl wird übergangen. So lassen sich auch Konten mit einer Adresse
    außerhalb der hinterlegten Domänen zuordnen.
    """

    def __init__(self, domains, **kwargs):
        domains = list(domains)
        super().__init__(
            fields=[
                forms.CharField(max_length=254),
                forms.ChoiceField(choices=[(d, d) for d in domains], required=False),
            ],
            widget=EmailWithDomainWidget(domains),
            require_all_fields=False,
            **kwargs,
        )

    def compress(self, data_list):
        if not data_list:
            return ""
        local, domain = (data_list + [None, None])[:2]
        local = (local or "").strip()
        if not local:
            return ""
        if "@" in local:
            address = local
        elif domain:
            address = f"{local}@{domain}"
        else:
            raise forms.ValidationError("Bitte eine Domäne auswählen.")
        validate_email(address)
        return address


class MembershipForm(forms.Form):
    """Nutzer per E-Mail zu einer Gruppe hinzufügen.

    Es werden nur bestehende Konten zugeordnet. Konten entstehen beim ersten
    Login über den Identity-Provider.
    """

    email = forms.EmailField(label="E-Mail-Adresse des Nutzers")
    role = forms.ChoiceField(
        label="Rolle", choices=GroupMembership.Role.choices, initial=GroupMembership.Role.MEMBER
    )

    def __init__(self, group, *args, **kwargs):
        self.group = group
        super().__init__(*args, **kwargs)
        domains = list(MailDomain.objects.values_list("domain", flat=True))
        if domains:
            self.fields["email"] = EmailWithDomainField(
                domains,
                label="E-Mail-Adresse des Nutzers",
                help_text=(
                    "Nur den Teil vor dem @ eintragen und die Domäne auswählen. "
                    "Für eine andere Domäne die ganze Adresse eintragen."
                ),
            )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist as exc:
            raise forms.ValidationError(
                "Es gibt kein aktives Konto mit dieser Adresse. "
                "Der Nutzer muss sich einmal anmelden, danach kann er zugeordnet werden."
            ) from exc
        if GroupMembership.objects.filter(group=self.group, user=user).exists():
            raise forms.ValidationError("Dieser Nutzer ist bereits in der Gruppe.")
        self.cleaned_user = user
        return email


class GroupForm(forms.ModelForm):
    """Neue Gruppe anlegen. Nur System-Admins dürfen das."""

    class Meta:
        model = Group
        fields = ["name", "cost_center", "month_start_day", "idp_identifier"]
        widgets = {
            "month_start_day": forms.NumberInput(
                attrs={"min": 1, "max": MAX_MONTH_START_DAY, "step": 1}
            )
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if Group.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("Eine Gruppe mit diesem Namen gibt es schon.")
        # Der Kurzname wird aus dem Namen erzeugt und muss ebenfalls eindeutig
        # sein; "Werkstatt" und "werk statt" ergäben denselben.
        if Group.objects.filter(slug=slugify(name)[:140]).exists():
            raise forms.ValidationError(
                "Aus diesem Namen entsteht derselbe Kurzname wie bei einer bestehenden Gruppe."
            )
        return name


class GroupSettingsForm(forms.ModelForm):
    """Einstellungen, die ein Admin der Gruppe selbst pflegen darf."""

    class Meta:
        model = Group
        fields = ["month_start_day", "cost_center", "idp_identifier"]
        widgets = {
            "month_start_day": forms.NumberInput(
                attrs={"min": 1, "max": MAX_MONTH_START_DAY, "step": 1}
            )
        }


class ClosePeriodForm(forms.Form):
    """Abschluss eines Zeitraums, identifiziert über seinen ersten Tag."""

    period_start = forms.DateField(widget=forms.HiddenInput())
    note = forms.CharField(
        label="Bemerkung", required=False, widget=forms.Textarea(attrs={"rows": 2})
    )


class AdminEntryForm(forms.Form):
    """Zeiteintrag eines Mitglieds direkt ändern oder nachtragen (Issue 31).

    Beim Ändern steht die Person schon fest, deshalb fällt das Feld dann weg.
    Die Begründung ist Pflicht, weil sie im Protokoll die einzige Auskunft
    darüber ist, warum jemand anderes an einer fremden Zeit gearbeitet hat.
    """

    user = forms.ModelChoiceField(
        queryset=User.objects.none(), label="Person", empty_label=None, required=False
    )
    activity = forms.ModelChoiceField(queryset=Activity.objects.none(), label="Tätigkeit")
    start = forms.DateTimeField(label="Beginn", widget=DateTimeLocalInput())
    end = forms.DateTimeField(label="Ende", widget=DateTimeLocalInput())
    note = forms.CharField(label="Notiz", required=False, max_length=500)
    reason = forms.CharField(label="Begründung", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, group, *args, entry=None, **kwargs):
        self.group = group
        self.entry = entry
        super().__init__(*args, **kwargs)
        # Eine deaktivierte Tätigkeit bleibt an alten Einträgen stehen. Ohne
        # sie in der Liste würde sie beim Speichern stillschweigend entfallen.
        choosable = Q(is_active=True)
        if entry is not None and entry.activity_id:
            choosable |= Q(pk=entry.activity_id)
        self.fields["activity"].queryset = Activity.objects.filter(
            Q(group=group) & choosable
        ).order_by("sort_order", "name")

        if entry is None:
            self.fields["user"].required = True
            self.fields["user"].queryset = User.objects.filter(
                group_memberships__group=group, is_active=True
            ).distinct()
        else:
            del self.fields["user"]
            if not self.is_bound:
                self.initial.update(
                    {
                        "activity": entry.activity_id,
                        "start": entry.start,
                        "end": entry.end,
                        "note": entry.note,
                    }
                )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end <= start:
            self.add_error("end", "Das Ende muss nach dem Beginn liegen.")
        if start and start > timezone.now():
            self.add_error("start", "Ein Beginn in der Zukunft ist nicht möglich.")
        return cleaned
