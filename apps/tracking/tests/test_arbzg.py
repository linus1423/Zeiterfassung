"""Arbeitszeitgesetz über den ganzen Tag geprüft (Issue 49).

Alle Zeiten sind fest verankert: "jetzt minus ein paar Stunden" liefe je nach
Uhrzeit des Testlaufs über Mitternacht und zählte dann nur anteilig.
"""

from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone

from apps.groups.models import Activity, GroupMembership
from apps.tracking import arbzg, services, views
from apps.tracking.models import BreakEntry, TimeEntry

# Ein Montag, weit genug weg von jeder Zeitumstellung.
MONDAY = date(2026, 5, 11)
TUESDAY = date(2026, 5, 12)


def at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def entry(user, group, day, start_hour, end_hour, *, end_day=None, pause_minutes=0):
    """Ein Eintrag mit fester Uhrzeit, wahlweise mit einer Pause in der Mitte."""
    start = at(day, start_hour)
    end = at(end_day or day, end_hour)
    activity, _ = Activity.objects.get_or_create(group=group, name="Montage")
    item = TimeEntry.objects.create(user=user, group=group, activity=activity, start=start, end=end)
    if pause_minutes:
        pause_start = start + (end - start) / 2
        BreakEntry.objects.create(
            time_entry=item,
            start=pause_start,
            end=pause_start + timedelta(minutes=pause_minutes),
        )
    return item


def check(user, first_day=MONDAY, last_day=TUESDAY):
    return arbzg.check(TimeEntry.objects.filter(user=user), first_day, last_day)


def rules(violations) -> list[str]:
    return [violation.rule for violation in violations]


def test_two_short_entries_add_up_to_a_break_violation(member, group):
    """Zweimal vier Stunden sind acht Stunden Arbeitszeit — und brauchen Pause."""
    entry(member, group, MONDAY, 6, 10)
    entry(member, group, MONDAY, 11, 15)

    found = check(member)

    assert rules(found) == [arbzg.Rule.BREAK]
    assert found[0].day == MONDAY
    assert found[0].limit == arbzg.SHORT_BREAK


def test_a_single_short_entry_is_fine(member, group):
    entry(member, group, MONDAY, 8, 12)

    assert check(member) == []


def test_breaks_of_the_whole_day_count_together(member, group):
    """Zwei Pausen von je 20 Minuten erfüllen die halbe Stunde zusammen."""
    entry(member, group, MONDAY, 6, 11, pause_minutes=20)
    entry(member, group, MONDAY, 12, 16, pause_minutes=20)

    assert arbzg.Rule.BREAK not in rules(check(member))


def test_nine_hours_need_three_quarters_of_an_hour(member, group):
    entry(member, group, MONDAY, 6, 16, pause_minutes=35)

    found = [item for item in check(member) if item.rule == arbzg.Rule.BREAK]

    assert found[0].limit == arbzg.LONG_BREAK


def test_more_than_ten_hours_a_day(member, group):
    entry(member, group, MONDAY, 5, 11)
    entry(member, group, MONDAY, 12, 18)

    found = [item for item in check(member) if item.rule == arbzg.Rule.DAILY_MAX]

    assert len(found) == 1
    assert found[0].value == timedelta(hours=12)
    assert found[0].limit == arbzg.MAX_DAILY_WORK
    assert "zehn Stunden" in found[0].message


def test_exactly_ten_hours_are_still_allowed(member, group):
    entry(member, group, MONDAY, 6, 17, pause_minutes=60)

    assert arbzg.Rule.DAILY_MAX not in rules(check(member))


def test_a_night_shift_is_split_over_both_days(member, group):
    """22:00 bis 6:00 sind zwei und sechs Stunden, an keinem Tag zu viel."""
    entry(member, group, MONDAY, 22, 6, end_day=TUESDAY)

    summaries = {
        summary.day: summary.work
        for summary in arbzg.day_summaries(
            list(TimeEntry.objects.filter(user=member).prefetch_related("breaks")),
            MONDAY,
            TUESDAY,
        )
    }

    assert summaries == {MONDAY: timedelta(hours=2), TUESDAY: timedelta(hours=6)}
    assert check(member) == []


def test_too_little_rest_between_two_days(member, group):
    entry(member, group, MONDAY, 14, 22)
    entry(member, group, TUESDAY, 6, 12)

    found = [item for item in check(member) if item.rule == arbzg.Rule.REST]

    assert len(found) == 1
    assert found[0].day == TUESDAY
    assert found[0].value == timedelta(hours=8)
    assert "Ruhezeit" in found[0].message


def test_eleven_hours_rest_are_enough(member, group):
    entry(member, group, MONDAY, 12, 19)
    entry(member, group, TUESDAY, 6, 12)

    assert arbzg.Rule.REST not in rules(check(member))


def test_a_gap_within_one_day_is_no_rest_period(member, group):
    """Wer mittags aus- und wieder einstempelt, hat keinen Feierabend gemacht."""
    entry(member, group, MONDAY, 8, 12)
    entry(member, group, MONDAY, 13, 16)

    assert arbzg.Rule.REST not in rules(check(member))


def test_the_rest_of_the_first_day_looks_at_the_evening_before(member, group):
    """Die Lücke über die Grenze des Zeitraums hinweg zählt trotzdem."""
    entry(member, group, MONDAY, 14, 22)
    entry(member, group, TUESDAY, 6, 12)

    found = check(member, first_day=TUESDAY, last_day=TUESDAY)

    assert arbzg.Rule.REST in rules(found)


def test_the_rest_is_measured_over_the_change_to_summer_time(member, group):
    """In der Nacht der Umstellung ist die Lücke eine Stunde kürzer als am Ziffernblatt."""
    saturday = date(2026, 3, 28)
    sunday = date(2026, 3, 29)
    entry(member, group, saturday, 14, 22)
    entry(member, group, sunday, 9, 14)

    found = [
        item
        for item in arbzg.check(TimeEntry.objects.filter(user=member), saturday, sunday)
        if item.rule == arbzg.Rule.REST
    ]

    # Auf der Uhr sind es elf Stunden, tatsächlich vergangen sind zehn.
    assert len(found) == 1
    assert found[0].value == timedelta(hours=10)


def test_a_running_entry_does_not_break_the_check(member, group, activity):
    TimeEntry.objects.create(user=member, group=group, activity=activity, start=timezone.now())

    today = timezone.localdate()

    assert arbzg.check(TimeEntry.objects.filter(user=member), today, today) == []


def test_the_break_warning_can_be_switched_off(member, group, settings):
    entry(member, group, MONDAY, 6, 15)
    settings.STATUTORY_BREAK_WARNINGS = False

    assert arbzg.Rule.BREAK not in rules(check(member))


def test_the_new_rules_can_be_switched_off(member, group, settings):
    entry(member, group, MONDAY, 5, 11)
    entry(member, group, MONDAY, 12, 18)
    entry(member, group, TUESDAY, 6, 12)
    settings.STATUTORY_LIMIT_WARNINGS = False

    found = rules(check(member))

    assert arbzg.Rule.DAILY_MAX not in found
    assert arbzg.Rule.REST not in found
    # Die Pausenwarnung hängt an ihrer eigenen Einstellung und bleibt.
    assert arbzg.Rule.BREAK in found


def test_the_check_does_not_grow_with_the_number_of_people(
    django_assert_max_num_queries, group, member, make_user
):
    for index in range(4):
        person = make_user(f"person{index}@example.com")
        GroupMembership.objects.create(user=person, group=group)
        entry(person, group, MONDAY, 6, 16)

    entry(member, group, MONDAY, 6, 16)

    with django_assert_max_num_queries(2):
        found = arbzg.check(TimeEntry.objects.filter(group=group), MONDAY, TUESDAY)

    assert len(found) == 5


def test_the_warning_of_an_entry_covers_every_day_it_touches(member, group):
    """Eine Nachtschicht berührt zwei Tage, geprüft werden beide."""
    night = entry(member, group, MONDAY, 22, 6, end_day=TUESDAY)
    entry(member, group, TUESDAY, 8, 18)

    warnings = services.warnings_for_entry(night)

    assert any("12.05.2026" in text and "Pause" in text for text in warnings)


def test_clocking_out_shows_the_warning(client, member, group, activity, monkeypatch):
    """Der Hinweis landet als Meldung auf der Stempeluhr.

    Die Regeln selbst sind oben geprüft; hier geht es nur um den Weg vom
    Ausstempeln zur Meldung, deshalb ohne Zeiten, die vom Testzeitpunkt
    abhängen.
    """
    monkeypatch.setattr(views.services, "warnings_for_entry", lambda entry: ["Zu wenig Pause."])
    services.clock_in(member, group, activity)
    client.force_login(member)

    response = client.post(reverse("tracking:clock_out"), follow=True)

    assert "Zu wenig Pause." in response.content.decode()
