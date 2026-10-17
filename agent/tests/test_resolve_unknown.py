"""Made-up piece keys resolve to the real catalog part, or go to sourcing once (agent/live._resolve_unknown)."""

from agent import live
from agent.events import Stream


def test_catalog_part_under_a_made_up_key():
    part = {"ref": "Q1", "piece": "2N3904@TO-92", "pins": {}}
    kept, keys = [], set()
    live._resolve_unknown(Stream("t", echo=False), [part], kept, keys, set())
    assert part["piece"].startswith("kicad:Transistor_BJT:2N3904@")
    assert part["piece"] in keys and kept[0]["key"] == part["piece"]


def test_unknown_part_sourced_once(monkeypatch):
    calls = []
    monkeypatch.setattr(live, "source_missing", lambda s, m: calls.append(m[0]["what"]) or [])
    part = {"ref": "U1", "piece": "PIR_Sensor@HC-SR501", "pins": {}}
    tried: set[str] = set()
    for _ in range(3):
        live._resolve_unknown(Stream("t", echo=False), [part], [], set(), tried)
    assert calls == ["PIR Sensor"]  # one sourcing attempt, never a guessed piece
    assert part["piece"] == "PIR_Sensor@HC-SR501"


def test_pico_names_map_to_the_library_module():
    assert live._catalog_hit("Raspberry Pi Pico")["key"] == live.PICO_KEY
    assert live._catalog_hit("Raspberry Pi Pico W")["key"] == live.PICO_W_KEY
