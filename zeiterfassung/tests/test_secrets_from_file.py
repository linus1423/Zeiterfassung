"""Geheimnisse dürfen als Datei statt als Umgebungsvariable kommen."""

import os

import pytest
from django.core.exceptions import ImproperlyConfigured

from zeiterfassung.settings import _secrets_from_files


def test_liest_den_wert_aus_der_datei(tmp_path, monkeypatch):
    datei = tmp_path / "secret"
    # Ein abschließender Zeilenumbruch ist üblich und gehört nicht zum Wert.
    datei.write_text("geheimer-schluessel\n", encoding="utf-8")
    # setenv statt delenv, damit monkeypatch den ursprünglichen Zustand
    # danach wiederherstellt; leer zählt wie nicht gesetzt.
    monkeypatch.setenv("DJANGO_SECRET_KEY", "")
    monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(datei))

    assert _secrets_from_files()["DJANGO_SECRET_KEY"] == "geheimer-schluessel"


def test_der_wert_landet_nicht_in_der_prozessumgebung(tmp_path, monkeypatch):
    """Sonst stünde er in /proc/<pid>/environ und in jedem Kindprozess."""
    datei = tmp_path / "secret"
    datei.write_text("geheimer-schluessel", encoding="utf-8")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "")
    monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(datei))

    _secrets_from_files()

    assert os.environ["DJANGO_SECRET_KEY"] == ""


def test_umgebungsvariable_hat_vorrang(tmp_path, monkeypatch):
    datei = tmp_path / "secret"
    datei.write_text("aus-der-datei", encoding="utf-8")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "aus-der-umgebung")
    monkeypatch.setenv("DJANGO_SECRET_KEY_FILE", str(datei))

    assert "DJANGO_SECRET_KEY" not in _secrets_from_files()


def test_ohne_file_variable_passiert_nichts(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.delenv("DATABASE_URL_FILE", raising=False)

    assert _secrets_from_files() == {}


def test_fehlende_datei_faellt_auf(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_URL_FILE", str(tmp_path / "gibt-es-nicht"))

    with pytest.raises(ImproperlyConfigured):
        _secrets_from_files()


def test_leere_datei_faellt_auf(tmp_path, monkeypatch):
    datei = tmp_path / "leer"
    datei.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_URL_FILE", str(datei))

    with pytest.raises(ImproperlyConfigured):
        _secrets_from_files()
