"""Mehrere Pausen je Korrekturantrag (Issue 2)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.corrections import services
from apps.corrections.models import CorrectionRequest
from apps.tracking.models import BreakEntry, TimeEntry


@pytest.fixture
def workday(member, group, activity):
    """Ein abgeschlossener Arbeitstag von 8 bis 16 Uhr, gestern."""
    start = (timezone.localtime() - timedelta(days=1)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    return TimeEntry.objects.create(
        user=member,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=8),
    )


def _local(entry, hour, minute=0):
    return (timezone.localtime(entry.start).replace(hour=hour, minute=minute)).strftime(
        "%Y-%m-%dT%H:%M"
    )


def _post_data(workday, breaks, total=6):
    data = {
        "group": workday.group_id,
        "activity": workday.activity_id,
        "start": _local(workday, 8),
        "end": _local(workday, 16),
        "reason": "Zwei Pausen waren nicht erfasst.",
        "pausen-TOTAL_FORMS": str(total),
        "pausen-INITIAL_FORMS": "0",
        "pausen-MIN_NUM_FORMS": "0",
        "pausen-MAX_NUM_FORMS": "6",
    }
    for index in range(total):
        start, end = breaks[index] if index < len(breaks) else ("", "")
        data[f"pausen-{index}-start"] = start
        data[f"pausen-{index}-end"] = end
    return data


def test_two_breaks_end_up_in_the_request(client, member, workday):
    client.force_login(member)

    response = client.post(
        reverse("corrections:create_for_entry", args=[workday.pk]),
        _post_data(
            workday,
            [
                (_local(workday, 10), _local(workday, 10, 15)),
                (_local(workday, 12), _local(workday, 12, 30)),
            ],
        ),
        follow=True,
    )

    assert response.status_code == 200
    correction = CorrectionRequest.objects.get(requested_by=member)
    assert len(correction.proposed_breaks) == 2
    assert correction.proposed_break_total == timedelta(minutes=45)


def test_breaks_are_stored_in_order(client, member, workday):
    client.force_login(member)

    client.post(
        reverse("corrections:create_for_entry", args=[workday.pk]),
        _post_data(
            workday,
            [
                (_local(workday, 13), _local(workday, 13, 30)),
                (_local(workday, 10), _local(workday, 10, 30)),
            ],
        ),
        follow=True,
    )

    correction = CorrectionRequest.objects.get(requested_by=member)
    periods = correction.proposed_break_periods
    assert [period["start"].hour for period in periods] == [10, 13]


def test_overlapping_breaks_are_refused(client, member, workday):
    client.force_login(member)

    response = client.post(
        reverse("corrections:create_for_entry", args=[workday.pk]),
        _post_data(
            workday,
            [
                (_local(workday, 10), _local(workday, 11)),
                (_local(workday, 10, 30), _local(workday, 12)),
            ],
        ),
    )

    assert response.status_code == 200
    assert "überschneiden" in response.content.decode()
    assert not CorrectionRequest.objects.exists()


def test_a_break_outside_the_working_time_is_refused(client, member, workday):
    client.force_login(member)

    response = client.post(
        reverse("corrections:create_for_entry", args=[workday.pk]),
        _post_data(workday, [(_local(workday, 7), _local(workday, 7, 30))]),
    )

    assert response.status_code == 200
    assert "innerhalb der Arbeitszeit" in response.content.decode()
    assert not CorrectionRequest.objects.exists()


def test_half_a_break_is_refused(client, member, workday):
    client.force_login(member)

    response = client.post(
        reverse("corrections:create_for_entry", args=[workday.pk]),
        _post_data(workday, [(_local(workday, 10), "")]),
    )

    assert response.status_code == 200
    assert "Beginn und Ende der Pause" in response.content.decode()


def test_existing_breaks_are_prefilled(client, member, workday):
    BreakEntry.objects.create(
        time_entry=workday,
        start=workday.start + timedelta(hours=2),
        end=workday.start + timedelta(hours=2, minutes=30),
    )
    client.force_login(member)

    response = client.get(reverse("corrections:create_for_entry", args=[workday.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'name="pausen-0-start"' in content
    assert timezone.localtime(workday.start + timedelta(hours=2)).strftime("%H:%M") in content


def test_approval_writes_every_break(workday, member, group_admin, activity):
    correction = services.create_request(
        requested_by=member,
        group=workday.group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Pausen nachtragen",
        entry=workday,
        proposed_start=workday.start,
        proposed_end=workday.end,
        proposed_activity=activity,
        proposed_breaks=[
            {
                "start": (workday.start + timedelta(hours=2)).isoformat(),
                "end": (workday.start + timedelta(hours=2, minutes=15)).isoformat(),
            },
            {
                "start": (workday.start + timedelta(hours=4)).isoformat(),
                "end": (workday.start + timedelta(hours=4, minutes=30)).isoformat(),
            },
        ],
    )

    services.approve(correction, group_admin)

    workday.refresh_from_db()
    assert workday.breaks.count() == 2
    assert workday.break_duration == timedelta(minutes=45)
    assert workday.duration == timedelta(hours=7, minutes=15)


def test_the_decision_page_lists_the_breaks(client, workday, member, group_admin, activity):
    correction = services.create_request(
        requested_by=member,
        group=workday.group,
        kind=CorrectionRequest.Kind.EDIT,
        reason="Pausen nachtragen",
        entry=workday,
        proposed_start=workday.start,
        proposed_end=workday.end,
        proposed_activity=activity,
        proposed_breaks=[
            {
                "start": (workday.start + timedelta(hours=2)).isoformat(),
                "end": (workday.start + timedelta(hours=2, minutes=15)).isoformat(),
            },
            {
                "start": (workday.start + timedelta(hours=5)).isoformat(),
                "end": (workday.start + timedelta(hours=5, minutes=30)).isoformat(),
            },
        ],
    )
    client.force_login(group_admin)

    response = client.get(reverse("corrections:decide", args=[correction.pk]))
    content = response.content.decode()

    assert "Beantragte Pausen" in content
    assert content.count("bis ") >= 2
    assert "0:45" in content
