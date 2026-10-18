"""Neue Tor-Route: ControlPort-Adresse aus TOR_CONTROL_ADDR (Standard 127.0.0.1:9051)."""

import asyncio

from utils.maintenance import new_tor_identity


def test_control_address_from_env(monkeypatch):
    seen = []

    async def fake_open(host, port):
        seen.append((host, port))
        raise OSError("kein Tor im Test")

    monkeypatch.setenv("TOR_CONTROL_PASSWORD", "x")
    monkeypatch.setattr(asyncio, "open_connection", fake_open)
    monkeypatch.delenv("TOR_CONTROL_ADDR", raising=False)
    assert asyncio.run(new_tor_identity()) is False
    monkeypatch.setenv("TOR_CONTROL_ADDR", "tor:9151")
    asyncio.run(new_tor_identity())
    assert seen == [("127.0.0.1", 9051), ("tor", 9151)]
