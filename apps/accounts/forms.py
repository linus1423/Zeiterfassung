from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


class UserStammdatenForm(forms.ModelForm):
    """Was ein System-Admin an einem Konto pflegen darf.

    Name und E-Mail kommen aus dem Identity-Provider und bleiben deshalb
    unveränderlich. Die Personalnummer kennt nur die Personalverwaltung, und
    die Rolle Buchhaltung gilt für die ganze Installation; beides gibt es
    nirgends sonst in der Oberfläche.
    """

    class Meta:
        model = User
        fields = ["personnel_number", "is_accounting"]

    def clean_personnel_number(self):
        number = (self.cleaned_data.get("personnel_number") or "").strip()
        if not number:
            return ""
        doppelt = (
            User.objects.filter(personnel_number__iexact=number)
            .exclude(pk=self.instance.pk)
            .first()
        )
        if doppelt is not None:
            raise forms.ValidationError(f"Diese Personalnummer hat bereits {doppelt.full_name}.")
        return number


class UserSearchForm(forms.Form):
    q = forms.CharField(label="Suche", required=False, max_length=100)
