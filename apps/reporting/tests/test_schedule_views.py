"""Zeitpläne einrichten und sehen: wer darf was (Issue 55)."""

import pytest
from django.urls import reverse

from apps.groups.models import GroupMembership
from apps.reporting.models import ExportProfile, ExportSchedule


@pytest.fixture
def profil(group_admin):
    return ExportProfile.objects.create(
        name="Monatsabrechnung", owner=group_admin, columns=["full_name", "hours"], filters={}
    )


@pytest.fixture
def geteiltes_profil(accountant, group):
    """Vorlage einer Person, die Buchhalterin und Gruppen-Admin zugleich ist."""
    GroupMembership.objects.create(user=accountant, group=group, role=GroupMembership.Role.ADMIN)
    return ExportProfile.objects.create(
        name="Alle Gruppen",
        owner=accountant,
        share_with_group_admins=True,
        share_with_accounting=True,
        columns=["full_name", "hours"],
        filters={},
    )


def _formular(profil, **kwargs):
    daten = {
        "profile": profil.pk,
        "day_of_month": 3,
        "timeframe": ExportSchedule.Timeframe.PREVIOUS_PERIOD,
        "export_format": ExportSchedule.Format.XLSX,
        "recipients": "buchhaltung@example.com",
        "is_active": "on",
    }
    daten.update(kwargs)
    return daten


def test_mitglied_kommt_nicht_an_die_plaene(client, member):
    client.force_login(member)

    assert client.get(reverse("reporting:schedules")).status_code == 403


def test_gruppen_admin_legt_einen_plan_an(client, group_admin, profil):
    client.force_login(group_admin)

    antwort = client.post(reverse("reporting:schedules"), _formular(profil), follow=True)

    assert antwort.status_code == 200
    plan = ExportSchedule.objects.get()
    assert plan.created_by == group_admin
    assert plan.recipients == ["buchhaltung@example.com"]
    assert plan.day_of_month == 3


def test_mehrere_adressen_werden_getrennt(client, group_admin, profil):
    client.force_login(group_admin)

    client.post(
        reverse("reporting:schedules"),
        _formular(profil, recipients="eine@example.com, zwei@example.com\ndrei@example.com"),
    )

    assert ExportSchedule.objects.get().recipients == [
        "eine@example.com",
        "zwei@example.com",
        "drei@example.com",
    ]


def test_kaputte_adresse_wird_abgewiesen(client, group_admin, profil):
    client.force_login(group_admin)

    antwort = client.post(reverse("reporting:schedules"), _formular(profil, recipients="kein-mail"))

    assert antwort.status_code == 200
    assert not ExportSchedule.objects.exists()
    assert "keine gültige E-Mail-Adresse" in antwort.content.decode()


def test_tag_ausserhalb_von_1_bis_28_wird_abgewiesen(client, group_admin, profil):
    client.force_login(group_admin)

    antwort = client.post(reverse("reporting:schedules"), _formular(profil, day_of_month=31))

    assert not ExportSchedule.objects.exists()
    assert "zwischen 1 und 28" in antwort.content.decode()


def test_derselbe_tag_wird_nicht_zweimal_geplant(client, group_admin, profil):
    client.force_login(group_admin)
    client.post(reverse("reporting:schedules"), _formular(profil))

    antwort = client.post(reverse("reporting:schedules"), _formular(profil))

    assert ExportSchedule.objects.count() == 1
    assert "schon einen Zeitplan" in antwort.content.decode()


def test_fremde_vorlage_laesst_sich_nicht_einplanen(client, group_admin, accountant):
    privat = ExportProfile.objects.create(
        name="Nur intern", owner=accountant, columns=["full_name"], filters={}
    )
    client.force_login(group_admin)

    antwort = client.post(reverse("reporting:schedules"), _formular(privat))

    assert antwort.status_code == 200
    assert not ExportSchedule.objects.exists()


def test_geteilte_vorlage_der_buchhaltung_darf_geplant_werden(
    client, group_admin, geteiltes_profil
):
    client.force_login(group_admin)

    client.post(reverse("reporting:schedules"), _formular(geteiltes_profil))

    plan = ExportSchedule.objects.get()
    assert plan.created_by == group_admin
    assert plan.profile == geteiltes_profil


def test_fremder_plan_ist_nicht_sichtbar(client, make_user, group, profil, group_admin):
    ExportSchedule.objects.create(
        profile=profil, created_by=group_admin, day_of_month=3, recipients=["a@example.com"]
    )
    anderer = make_user("admin2@example.com", first_name="Bea", last_name="Berg")
    GroupMembership.objects.create(user=anderer, group=group, role=GroupMembership.Role.ADMIN)
    client.force_login(anderer)

    inhalt = client.get(reverse("reporting:schedules")).content.decode()

    assert "Monatsabrechnung" not in inhalt


def test_buchhaltung_sieht_alle_plaene(client, accountant, profil, group_admin):
    ExportSchedule.objects.create(
        profile=profil, created_by=group_admin, day_of_month=3, recipients=["a@example.com"]
    )
    client.force_login(accountant)

    inhalt = client.get(reverse("reporting:schedules")).content.decode()

    assert "Monatsabrechnung" in inhalt


def test_fremder_plan_laesst_sich_nicht_loeschen(client, make_user, group, profil, group_admin):
    plan = ExportSchedule.objects.create(
        profile=profil, created_by=group_admin, day_of_month=3, recipients=["a@example.com"]
    )
    anderer = make_user("admin3@example.com", first_name="Cem", last_name="Cerny")
    GroupMembership.objects.create(user=anderer, group=group, role=GroupMembership.Role.ADMIN)
    client.force_login(anderer)

    antwort = client.post(reverse("reporting:schedule_delete", args=[plan.pk]))

    assert antwort.status_code == 403
    assert ExportSchedule.objects.filter(pk=plan.pk).exists()


def test_eigener_plan_laesst_sich_loeschen(client, group_admin, profil):
    plan = ExportSchedule.objects.create(
        profile=profil, created_by=group_admin, day_of_month=3, recipients=["a@example.com"]
    )
    client.force_login(group_admin)

    client.post(reverse("reporting:schedule_delete", args=[plan.pk]))

    assert not ExportSchedule.objects.filter(pk=plan.pk).exists()
