"""Gemeinsame Einstellungen für die Tests."""

import pytest


@pytest.fixture(autouse=True)
def legacy_text_paths(monkeypatch, request):
    """Die alten Text-Wege (Lesezeichen vor v19) sind im Betrieb abgeschaltet (GT_LEGACY_TEXT); ihre Tests laufen
    weiter mit eingeschaltetem Schalter. Tests mit Marker no_legacy prüfen den abgeschalteten Zustand."""
    if request.node.get_closest_marker("no_legacy"):
        monkeypatch.delenv("GT_LEGACY_TEXT", raising=False)
    else:
        monkeypatch.setenv("GT_LEGACY_TEXT", "1")


def pytest_configure(config):
    config.addinivalue_line("markers", "no_legacy: alte Text-Wege abgeschaltet (wie im Betrieb)")
