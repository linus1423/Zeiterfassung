"""Exporte nach Zeitplan verschicken (Issue 55).

Alle Zeiten stehen als festes Datum im Test: "heute minus eine Spanne" liefe
je nach Uhrzeit des Laufs über Mitternacht oder in einen anderen
Abrechnungszeitraum, und dann stimmten die Summen nicht mehr.
"""

import codecs
from datetime import date, datetime, time, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.groups.models import Activity, GroupMembership
from apps.reporting import schedules as jobs
from apps.reporting.models import ExportProfile, ExportRun, ExportSchedule
from apps.tracking.models import TimeEntry

# Der Plan ist am 3. März 2026 fällig und wertet dann den Februar aus.
ANGELEGT = datetime(2026, 1, 2, 9, 0)
LAUFTAG = date(2026, 3, 5)
FAELLIG = date(2026, 3, 3)


@pytest.fixture(autouse=True)
def mailversand_an(settings):
    settings.EXPORT_EMAILS_ENABLED = True
    settings.EXPORT_MAX_ATTACHMENT_MB = 10
    settings.EXPORT_MAX_ROWS = 50000


def _eintrag(user, group, tag: date, stunden: int = 8):
    start = timezone.make_aware(datetime.combine(tag, time(8, 0)))
    activity, _ = Activity.objects.get_or_create(group=group, name="Montage")
    return TimeEntry.objects.create(
        user=user,
        group=group,
        activity=activity,
        start=start,
        end=start + timedelta(hours=stunden),
    )


@pytest.fixture
def februar_eintrag(member, group):
    return _eintrag(member, group, date(2026, 2, 10))


@pytest.fixture
def profil(group_admin):
    return ExportProfile.objects.create(
        name="Monatsabrechnung",
        owner=group_admin,
        columns=["full_name", "date", "hours"],
        grouping=ExportProfile.Grouping.ENTRY,
        filters={"csv_dialect": "de"},
    )


def _plan(profil, ersteller, **kwargs):
    """Legt einen Plan an und datiert ihn zurück, damit die Fälligkeit zählt."""
    plan = ExportSchedule.objects.create(
        profile=profil,
        created_by=ersteller,
        day_of_month=kwargs.pop("day_of_month", 3),
        timeframe=kwargs.pop("timeframe", ExportSchedule.Timeframe.PREVIOUS_PERIOD),
        export_format=kwargs.pop("export_format", ExportSchedule.Format.XLSX),
        recipients=kwargs.pop("recipients", ["buchhaltung@example.com"]),
        **kwargs,
    )
    ExportSchedule.objects.filter(pk=plan.pk).update(created_at=timezone.make_aware(ANGELEGT))
    plan.refresh_from_db()
    return plan


@pytest.fixture
def plan(profil, group_admin):
    return _plan(profil, group_admin)


# --- Fälligkeit -------------------------------------------------------------


def test_faelligkeit_ist_der_letzte_erreichte_tag(plan):
    assert jobs.due_date(plan, date(2026, 3, 5)) == date(2026, 3, 3)
    assert jobs.due_date(plan, date(2026, 3, 3)) == date(2026, 3, 3)
    assert jobs.due_date(plan, date(2026, 3, 2)) == date(2026, 2, 3)
    assert jobs.due_date(plan, date(2026, 1, 1)) == date(2025, 12, 3)


def test_zeitraum_ist_der_letzte_abgeschlossene_abrechnungszeitraum(plan):
    assert jobs.timeframe_for(plan, FAELLIG) == (date(2026, 2, 1), date(2026, 2, 28))


def test_zeitraum_folgt_dem_zyklus_der_gruppe(plan, group):
    group.month_start_day = 15
    group.save(update_fields=["month_start_day"])
    plan.period_group = group
    plan.save(update_fields=["period_group"])

    assert jobs.timeframe_for(plan, FAELLIG) == (date(2026, 1, 15), date(2026, 2, 14))


def test_zwoelf_zeitraeume_reichen_ein_jahr_zurueck(plan):
    plan.timeframe = ExportSchedule.Timeframe.LAST_TWELVE
    plan.save(update_fields=["timeframe"])

    assert jobs.timeframe_for(plan, FAELLIG) == (date(2025, 3, 1), date(2026, 2, 28))


def test_vor_dem_anlegen_wird_nichts_nachgeholt(profil, group_admin, februar_eintrag):
    plan = ExportSchedule.objects.create(
        profile=profil, created_by=group_admin, day_of_month=3, recipients=["b@example.com"]
    )
    # Der Plan entsteht heute, die Fälligkeit läge im Jahr 2026 davor.
    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert (ergebnis.sent, ergebnis.skipped) == (0, 1)
    assert not ExportRun.objects.filter(schedule=plan).exists()
    assert mail.outbox == []


# --- Versand ----------------------------------------------------------------


def test_faelliger_plan_verschickt_den_export(plan, februar_eintrag, group_admin):
    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert (ergebnis.sent, ergebnis.failed) == (1, 0)
    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.SENT
    assert (lauf.due_on, lauf.period_start, lauf.period_end) == (
        FAELLIG,
        date(2026, 2, 1),
        date(2026, 2, 28),
    )
    assert lauf.row_count == 1

    nachricht = mail.outbox[0]
    assert nachricht.to == ["buchhaltung@example.com"]
    assert "Monatsabrechnung" in nachricht.subject
    assert "Februar 2026" in nachricht.subject
    dateiname, inhalt, typ = nachricht.attachments[0]
    assert dateiname.endswith(".xlsx")
    assert typ == jobs.XLSX_MIMETYPE
    assert len(inhalt) == lauf.size_bytes

    protokoll = AuditLog.objects.get(action=AuditLog.Action.EXPORT)
    assert protokoll.actor == group_admin
    assert "Monatsabrechnung" in protokoll.note
    assert "buchhaltung@example.com" in protokoll.note


def test_csv_wird_als_csv_verschickt(profil, group_admin, februar_eintrag):
    _plan(profil, group_admin, export_format=ExportSchedule.Format.CSV)

    jobs.send_due_exports(LAUFTAG)

    dateiname, inhalt, typ = mail.outbox[0].attachments[0]
    assert dateiname.endswith(".csv")
    assert typ == jobs.CSV_MIMETYPE
    # Excel liest die Umlaute nur mit BOM am Anfang richtig.
    rohdaten = inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt
    assert rohdaten.startswith(codecs.BOM_UTF8)


def test_mehrere_empfaenger_bekommen_dieselbe_mail(profil, group_admin, februar_eintrag):
    _plan(profil, group_admin, recipients=["a@example.com", "b@example.com"])

    jobs.send_due_exports(LAUFTAG)

    assert mail.outbox[0].to == ["a@example.com", "b@example.com"]


# --- Höchstens einmal je Fälligkeit -----------------------------------------


def test_zweiter_lauf_verschickt_nicht_noch_einmal(plan, februar_eintrag):
    erst = jobs.send_due_exports(LAUFTAG)
    zweit = jobs.send_due_exports(LAUFTAG)

    assert (erst.sent, zweit.sent) == (1, 0)
    assert zweit.skipped == 1
    assert len(mail.outbox) == 1
    assert ExportRun.objects.filter(schedule=plan).count() == 1


def test_gleichzeitige_laeufe_sichern_sich_dieselbe_faelligkeit_nur_einmal(plan):
    erster = jobs._claim(plan, FAELLIG)
    zweiter = jobs._claim(plan, FAELLIG)

    assert erster is not None
    assert zweiter is None
    assert ExportRun.objects.filter(schedule=plan, due_on=FAELLIG).count() == 1


def test_ein_abgestuerzter_lauf_wird_nach_einer_weile_uebernommen(plan):
    lauf = jobs._claim(plan, FAELLIG)
    ExportRun.objects.filter(pk=lauf.pk).update(
        started_at=timezone.now() - jobs.STALE_AFTER - timedelta(minutes=1)
    )

    uebernommen = jobs._claim(plan, FAELLIG)

    assert uebernommen is not None
    assert uebernommen.pk == lauf.pk
    assert uebernommen.attempts == 2


def test_naechster_monat_ist_eine_neue_faelligkeit(plan, februar_eintrag):
    jobs.send_due_exports(LAUFTAG)
    jobs.send_due_exports(date(2026, 4, 4))

    assert len(mail.outbox) == 2
    assert set(ExportRun.objects.values_list("due_on", flat=True)) == {
        FAELLIG,
        date(2026, 4, 3),
    }


# --- Fehler statt stillem Verlust -------------------------------------------


def test_ohne_mailserver_bleibt_ein_fehler_stehen(settings, plan, februar_eintrag):
    settings.EXPORT_EMAILS_ENABLED = False

    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert (ergebnis.sent, ergebnis.failed) == (0, 1)
    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.FAILED
    assert "EXPORT_EMAILS_ENABLED" in lauf.message
    assert mail.outbox == []
    assert AuditLog.objects.filter(action=AuditLog.Action.NOTIFICATION_FAILED).exists()


def test_nach_einem_fehler_wird_beim_naechsten_lauf_erneut_versucht(
    settings, plan, februar_eintrag
):
    settings.EXPORT_EMAILS_ENABLED = False
    jobs.send_due_exports(LAUFTAG)

    settings.EXPORT_EMAILS_ENABLED = True
    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert ergebnis.sent == 1
    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.SENT
    assert lauf.attempts == 2
    assert len(mail.outbox) == 1


def test_ein_stummer_mailserver_bricht_den_lauf_nicht_ab(plan, februar_eintrag):
    with patch(
        "zeiterfassung.mailing.EmailMessage.send", side_effect=OSError("Verbindung abgelehnt")
    ):
        ergebnis = jobs.send_due_exports(LAUFTAG)

    assert (ergebnis.sent, ergebnis.failed) == (0, 1)
    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.FAILED
    assert "Verbindung abgelehnt" in lauf.message
    assert AuditLog.objects.filter(action=AuditLog.Action.NOTIFICATION_FAILED).exists()


def test_ein_kaputter_plan_haelt_die_uebrigen_nicht_auf(profil, group_admin, februar_eintrag):
    kaputt = _plan(profil, group_admin, day_of_month=3, recipients=[])
    heil = _plan(profil, group_admin, day_of_month=2)

    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert (ergebnis.sent, ergebnis.failed) == (1, 1)
    assert ExportRun.objects.get(schedule=kaputt).status == ExportRun.Status.BLOCKED
    assert ExportRun.objects.get(schedule=heil).status == ExportRun.Status.SENT
    assert len(mail.outbox) == 1


# --- Größe des Anhangs ------------------------------------------------------


def test_zu_grosser_anhang_wird_verstaendlich_abgelehnt(settings, plan, februar_eintrag):
    settings.EXPORT_MAX_ATTACHMENT_MB = 0

    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert ergebnis.failed == 1
    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.BLOCKED
    assert "zu groß für eine Mail" in lauf.message
    assert mail.outbox == []


def test_zu_viele_zeilen_werden_gar_nicht_erst_gebaut(settings, plan, member, group):
    settings.EXPORT_MAX_ROWS = 1
    _eintrag(member, group, date(2026, 2, 10))
    _eintrag(member, group, date(2026, 2, 11))

    jobs.send_due_exports(LAUFTAG)

    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.BLOCKED
    assert "Zeilen" in lauf.message
    assert mail.outbox == []


# --- Rechte beim Ausführen --------------------------------------------------


def test_ohne_rechte_wird_nichts_verschickt_und_der_plan_stillgelegt(
    plan, group_admin, group, februar_eintrag
):
    # Aus dem Admin wird ein einfaches Mitglied, nachdem der Plan steht.
    GroupMembership.objects.filter(user=group_admin, group=group).update(
        role=GroupMembership.Role.MEMBER
    )

    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert (ergebnis.sent, ergebnis.failed) == (0, 1)
    plan.refresh_from_db()
    assert plan.is_active is False
    lauf = ExportRun.objects.get(schedule=plan)
    assert lauf.status == ExportRun.Status.BLOCKED
    assert "nicht mehr benutzen" in lauf.message
    assert mail.outbox == []


def test_geteilte_vorlage_liefert_nur_die_eigenen_gruppen(
    accountant, group_admin, member, group, other_group
):
    """Ein Gruppen-Admin bekommt über einen Plan keine fremden Zeiten."""
    GroupMembership.objects.create(user=accountant, group=group, role=GroupMembership.Role.ADMIN)
    geteilt = ExportProfile.objects.create(
        name="Alle Gruppen",
        owner=accountant,
        share_with_group_admins=True,
        columns=["full_name", "hours"],
        filters={},
    )
    fremd = ExportProfile.objects.create(
        name="Kontrolle", owner=accountant, columns=["full_name", "hours"], filters={}
    )
    _eintrag(member, group, date(2026, 2, 10))
    _eintrag(accountant, other_group, date(2026, 2, 11))

    plan_admin = _plan(geteilt, group_admin, recipients=["admin@example.com"])
    plan_buchhaltung = _plan(fremd, accountant, recipients=["buch@example.com"])

    jobs.send_due_exports(LAUFTAG)

    assert ExportRun.objects.get(schedule=plan_admin).row_count == 1
    assert ExportRun.objects.get(schedule=plan_buchhaltung).row_count == 2


def test_kommando_verschickt_die_faelligen_plaene(plan, februar_eintrag):
    ausgabe = StringIO()
    call_command("send_scheduled_exports", "--today", LAUFTAG.isoformat(), stdout=ausgabe)

    assert "1 Exporte versandt" in ausgabe.getvalue()
    assert len(mail.outbox) == 1
    assert ExportRun.objects.get(schedule=plan).status == ExportRun.Status.SENT


def test_inaktive_plaene_laufen_nicht(plan, februar_eintrag):
    ExportSchedule.objects.filter(pk=plan.pk).update(is_active=False)

    ergebnis = jobs.send_due_exports(LAUFTAG)

    assert ergebnis == jobs.Result()
    assert mail.outbox == []
