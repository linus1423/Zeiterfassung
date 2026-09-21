"""Gruppenwechsel mit zwei Zustimmungen (Issue 37)."""

from datetime import datetime, time, timedelta

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups import change_requests
from apps.groups.models import GroupChangeRequest, GroupMembership
from apps.tracking.models import TimeEntry


@pytest.fixture
def other_admin(make_user, other_group):
    user = make_user("buero-admin@example.com", first_name="Bea", last_name="Buero")
    GroupMembership.objects.create(user=user, group=other_group, role=GroupMembership.Role.ADMIN)
    return user


@pytest.fixture
def change(member, group, other_group):
    return change_requests.create_request(
        user=member,
        from_group=group,
        to_group=other_group,
        reason="Ich arbeite ab Oktober im Büro.",
    )


def _entry(user, group, days_ago=1):
    """Ein abgeschlossener Eintrag an einer festen Uhrzeit, damit kein Lauf
    nachts über Mitternacht rutscht."""
    day = timezone.localdate() - timedelta(days=days_ago)
    start = timezone.make_aware(datetime.combine(day, time(8, 0)))
    return TimeEntry.objects.create(
        user=user, group=group, start=start, end=start + timedelta(hours=8)
    )


def test_wechsel_braucht_beide_zustimmungen(change, group_admin, other_admin, member, other_group):
    assert change.status == GroupChangeRequest.Status.PENDING_SOURCE
    assert change.deciding_group == change.from_group

    change_requests.decide(change, group_admin, approve=True)
    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.PENDING_TARGET
    # Nach der ersten Zustimmung ist noch nichts umgehängt.
    assert member.is_group_member(change.from_group)
    assert not member.is_group_member(other_group)

    change_requests.decide(change, other_admin, approve=True)
    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.APPROVED
    assert not member.is_group_member(change.from_group)
    assert member.is_group_member(other_group)
    membership = GroupMembership.objects.get(user=member, group=other_group)
    assert membership.role == GroupMembership.Role.MEMBER
    assert membership.source == GroupMembership.Source.MANUAL


def test_erfasste_zeiten_bleiben_bei_der_alten_gruppe(
    change, group, group_admin, other_admin, member
):
    entry = _entry(member, group)

    change_requests.decide(change, group_admin, approve=True)
    change_requests.decide(change, other_admin, approve=True)

    entry.refresh_from_db()
    assert entry.group == group


def test_die_bisherige_gruppe_kann_ablehnen(change, group_admin, member, other_group):
    change_requests.decide(change, group_admin, approve=False, note="Wir brauchen Mia hier.")

    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.REJECTED
    assert change.source_note == "Wir brauchen Mia hier."
    assert change.target_decided_at is None
    assert member.is_group_member(change.from_group)
    assert not member.is_group_member(other_group)


def test_die_neue_gruppe_kann_ablehnen(change, group_admin, other_admin, member, other_group):
    change_requests.decide(change, group_admin, approve=True)
    change_requests.decide(change, other_admin, approve=False, note="Kein Platz.")

    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.REJECTED
    assert member.is_group_member(change.from_group)
    assert not member.is_group_member(other_group)


def test_ablehnung_braucht_eine_begruendung(change, group_admin):
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.decide(change, group_admin, approve=False, note="   ")


def test_die_neue_gruppe_entscheidet_nicht_vorab(change, other_admin):
    assert not change_requests.may_decide(other_admin, change)
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.decide(change, other_admin, approve=True)


def test_die_alte_gruppe_entscheidet_nicht_zweimal(change, group_admin, other_admin):
    change_requests.decide(change, group_admin, approve=True)
    change.refresh_from_db()

    assert not change_requests.may_decide(group_admin, change)
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.decide(change, group_admin, approve=True)


def test_ueber_den_eigenen_antrag_entscheidet_ein_anderer(make_user, group, other_group):
    admin = make_user("wechselwillig@example.com", last_name="Wandel")
    GroupMembership.objects.create(user=admin, group=group, role=GroupMembership.Role.ADMIN)
    # Zweiter Admin, sonst scheiterte der Antrag schon an der Admin-Regel.
    second = make_user("bleibt@example.com", last_name="Bleibt")
    GroupMembership.objects.create(user=second, group=group, role=GroupMembership.Role.ADMIN)

    change = change_requests.create_request(
        user=admin, from_group=group, to_group=other_group, reason="Ich wechsle."
    )

    assert not change_requests.may_decide(admin, change)
    assert change_requests.may_decide(second, change)


def test_system_admin_entscheidet_notfalls_selbst(change, superuser):
    assert change_requests.may_decide(superuser, change)


def test_nur_ein_offener_antrag(change, member, group, other_group):
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.create_request(
            user=member, from_group=group, to_group=other_group, reason="Noch einmal."
        )


def test_kein_antrag_waehrend_der_schicht(member, group, other_group):
    TimeEntry.objects.create(user=member, group=group, start=timezone.now())

    with pytest.raises(change_requests.GroupChangeError):
        change_requests.create_request(
            user=member, from_group=group, to_group=other_group, reason="Jetzt gleich."
        )


def test_kein_wechsel_waehrend_der_schicht(change, group, member, group_admin, other_admin):
    change_requests.decide(change, group_admin, approve=True)
    TimeEntry.objects.create(user=member, group=group, start=timezone.now())

    with pytest.raises(change_requests.GroupChangeError):
        change_requests.decide(change, other_admin, approve=True)

    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.PENDING_TARGET
    assert member.is_group_member(group)


def test_der_einzige_admin_bleibt(group_admin, group, other_group):
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.create_request(
            user=group_admin, from_group=group, to_group=other_group, reason="Ich möchte weg."
        )


def test_kein_antrag_in_die_eigene_gruppe(member, group):
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.create_request(
            user=member, from_group=group, to_group=group, reason="Im Kreis."
        )


def test_kein_antrag_ohne_mitgliedschaft(make_user, group, other_group):
    fremder = make_user("fremd@example.com", last_name="Fremd")

    with pytest.raises(change_requests.GroupChangeError):
        change_requests.create_request(
            user=fremder, from_group=group, to_group=other_group, reason="Ich bin gar nicht da."
        )


def test_kein_antrag_in_eine_stillgelegte_gruppe(member, group, other_group):
    other_group.is_active = False
    other_group.save(update_fields=["is_active"])

    with pytest.raises(change_requests.GroupChangeError):
        change_requests.create_request(
            user=member, from_group=group, to_group=other_group, reason="Dorthin."
        )


def test_verschwundene_mitgliedschaft_bricht_den_wechsel_ab(
    change, group, member, group_admin, other_admin, other_group
):
    change_requests.decide(change, group_admin, approve=True)
    GroupMembership.objects.filter(user=member, group=group).delete()

    with pytest.raises(change_requests.GroupChangeError):
        change_requests.decide(change, other_admin, approve=True)

    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.PENDING_TARGET
    assert not GroupMembership.objects.filter(user=member, group=other_group).exists()


def test_zuruecknehmen_nur_durch_den_antragsteller(change, member, group_admin):
    with pytest.raises(change_requests.GroupChangeError):
        change_requests.withdraw(change, group_admin)

    change_requests.withdraw(change, member)
    change.refresh_from_db()
    assert change.status == GroupChangeRequest.Status.WITHDRAWN
    # Nach der Rücknahme ist der Weg für einen neuen Antrag frei.
    assert change_requests.open_request_for(member) is None


def test_entschiedener_antrag_wird_nicht_zurueckgenommen(change, member, group_admin):
    change_requests.decide(change, group_admin, approve=False, note="Nein.")

    with pytest.raises(change_requests.GroupChangeError):
        change_requests.withdraw(change, member)


def test_jeder_schritt_steht_im_protokoll(change, group_admin, other_admin):
    change_requests.decide(change, group_admin, approve=True)
    change_requests.decide(change, other_admin, approve=True)

    actions = list(
        AuditLog.objects.filter(target_type="GroupChangeRequest").values_list("action", flat=True)
    )
    assert AuditLog.Action.GROUP_CHANGE_REQUESTED in actions
    assert actions.count(AuditLog.Action.GROUP_CHANGE_APPROVED) == 3


def test_zaehler_fuer_die_navigation(change, member, group_admin, other_admin):
    def offen(user):
        return change_requests.navigation_counts(user)["pending_group_changes"]

    def neu(user):
        return change_requests.navigation_counts(user)["new_group_changes"]

    assert offen(group_admin) == 1
    assert offen(other_admin) == 0

    change_requests.decide(change, group_admin, approve=True)
    assert offen(group_admin) == 0
    assert offen(other_admin) == 1

    change_requests.decide(change, other_admin, approve=True)
    assert neu(member) == 1
    change_requests.mark_decisions_seen(member)
    assert neu(member) == 0


def test_die_navigation_braucht_eine_abfrage(change, group_admin, django_assert_num_queries):
    """Die Navigation steht auf jeder Seite, beide Zähler teilen sich eine Abfrage."""
    group_ids = group_admin.administrated_group_ids()

    with django_assert_num_queries(1):
        change_requests.navigation_counts(group_admin, group_ids)
