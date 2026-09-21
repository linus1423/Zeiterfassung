"""Die Seiten zum Gruppenwechsel (Issue 37)."""

import pytest
from django.urls import reverse

from apps.groups import change_requests
from apps.groups.models import Group, GroupChangeRequest, GroupMembership


@pytest.fixture
def other_admin(make_user, other_group):
    user = make_user("buero-admin@example.com", first_name="Bea", last_name="Buero")
    GroupMembership.objects.create(user=user, group=other_group, role=GroupMembership.Role.ADMIN)
    return user


@pytest.fixture
def change(member, group, other_group):
    return change_requests.create_request(
        user=member, from_group=group, to_group=other_group, reason="Ich wechsle ins Büro."
    )


def test_mitglied_stellt_einen_antrag(client, member, group, other_group):
    client.force_login(member)

    response = client.post(
        reverse("groups:change_list"),
        {"from_group": group.pk, "to_group": other_group.pk, "reason": "Neues Team."},
    )

    assert response.status_code == 302
    change = GroupChangeRequest.objects.get(user=member)
    assert change.from_group == group
    assert change.to_group == other_group
    assert change.status == GroupChangeRequest.Status.PENDING_SOURCE


def test_zweiter_antrag_wird_abgewiesen(client, member, group, other_group, change):
    client.force_login(member)

    response = client.post(
        reverse("groups:change_list"),
        {"from_group": group.pk, "to_group": other_group.pk, "reason": "Noch einmal."},
    )

    assert response.status_code == 302
    assert GroupChangeRequest.objects.filter(user=member).count() == 1


def test_eigene_gruppe_steht_nicht_zur_auswahl(client, member, group, other_group):
    client.force_login(member)

    response = client.get(reverse("groups:change_list"))

    choices = response.context["form"].fields["to_group"].queryset
    assert group not in choices
    assert other_group in choices


def test_eingang_zeigt_nur_was_gerade_ansteht(client, change, group_admin, other_admin):
    client.force_login(other_admin)
    response = client.get(reverse("groups:change_inbox"))
    assert list(response.context["requests"]) == []

    client.force_login(group_admin)
    response = client.get(reverse("groups:change_inbox"))
    assert list(response.context["requests"]) == [change]


def test_admin_stimmt_zu_und_der_antrag_geht_weiter(client, change, group_admin):
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:change_decide", args=[change.pk]),
        {"action": "approve", "note": "Gern, viel Erfolg."},
    )

    assert response.status_code == 302
    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.PENDING_TARGET
    assert change.source_decided_by == group_admin


def test_zweite_zustimmung_haengt_die_mitgliedschaft_um(
    client, change, group_admin, other_admin, member, group, other_group
):
    change_requests.decide(change, group_admin, approve=True)
    client.force_login(other_admin)

    response = client.post(
        reverse("groups:change_decide", args=[change.pk]),
        {"action": "approve", "note": ""},
    )

    assert response.status_code == 302
    assert not member.is_group_member(group)
    assert member.is_group_member(other_group)


def test_ablehnung_ohne_begruendung_bleibt_wirkungslos(client, change, group_admin):
    client.force_login(group_admin)

    response = client.post(
        reverse("groups:change_decide", args=[change.pk]), {"action": "reject", "note": ""}
    )

    assert response.status_code == 200
    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.PENDING_SOURCE


def test_unbeteiligter_admin_sieht_den_antrag_nicht(client, change, make_user):
    dritte_gruppe_admin = make_user("fremd-admin@example.com", last_name="Fremd")
    fremde = Group.objects.create(name="Lager")
    GroupMembership.objects.create(
        user=dritte_gruppe_admin, group=fremde, role=GroupMembership.Role.ADMIN
    )
    client.force_login(dritte_gruppe_admin)

    response = client.get(reverse("groups:change_decide", args=[change.pk]))

    assert response.status_code == 403


def test_antragsteller_nimmt_zurueck(client, change, member):
    client.force_login(member)

    response = client.post(reverse("groups:change_withdraw", args=[change.pk]))

    assert response.status_code == 302
    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.WITHDRAWN


def test_navigation_zaehlt_offene_entscheidungen(client, change, group_admin):
    client.force_login(group_admin)

    response = client.get(reverse("tracking:clock"))

    assert response.context["nav"]["pending_group_changes"] == 1
    assert "Wechselanträge" in response.content.decode()


def test_anmeldung_noetig(client):
    response = client.get(reverse("groups:change_list"))

    assert response.status_code == 302
    assert "/konto/anmelden/" in response["Location"]
