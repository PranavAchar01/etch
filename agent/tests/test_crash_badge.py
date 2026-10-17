"""Badge ask 10-17 (job 1UPoHr6_p3s4Wd_A): firmware.commands in another shape crashed the fix-up chain, "RV1" as a
piece name went to 160 s of web sourcing, and "kicid:" was a misspelt library prefix."""

import time

from agent import live, parts_live
from agent.events import Stream

POT = "kicad:Device:R_Potentiometer_Trim@"


def _st() -> Stream:
    return Stream("t", echo=False)


def test_firmware_commands_dict_becomes_a_list():
    d = {"firmware": {"commands": {"1": ["LED_R"], "2": "LED_G", "o": {"high": []}}}}
    live._normalize_firmware(d)
    assert d["firmware"]["commands"] == [
        {"input": "1", "high": ["LED_R"]},
        {"input": "2", "high": ["LED_G"]},
        {"input": "o", "high": []},
    ]


def test_firmware_commands_strings_and_bare_list():
    d = {"firmware": {"commands": ["1", {"input": "2", "high": "LED_B"}, None]}}
    live._normalize_firmware(d)
    assert d["firmware"]["commands"] == [
        {"input": "1", "high": []},
        {"input": "2", "high": ["LED_B"]},
    ]
    d = {"firmware": [{"input": "a", "high": ["X"]}]}
    live._normalize_firmware(d)
    assert d["firmware"] == {"commands": [{"input": "a", "high": ["X"]}]}
    d = {"firmware": "blink"}
    live._normalize_firmware(d)
    assert d["firmware"] == {}
    d = {"parts": []}
    live._normalize_firmware(d)
    assert "firmware" not in d


def test_wire_named_gpio_survives_a_dict_of_commands():
    """The crash itself: _wire_named_gpio called c.get("high") on the dict's string keys."""
    pico = live._cat()[live.PICO_KEY]
    d = {
        "parts": [
            {"ref": "U1", "piece": live.PICO_KEY, "pins": {"3": "GND", "20": "LED_R"}},
        ],
        "firmware": {"commands": {"1": ["LED_R"]}},
    }
    live._normalize_firmware(d)
    live._wire_named_gpio(d, [pico])


def test_reference_designators_resolve_locally(monkeypatch):
    monkeypatch.setattr(
        live,
        "source_missing",
        lambda s, m: (_ for _ in ()).throw(AssertionError("web sourcing")),
    )
    cases = {
        "kicad:Device:RV1": ({"1": "A", "2": "B"}, POT),
        "VR2": ({"1": "A", "2": "B", "3": "C"}, POT),
        "kicad:Device:potentiometer": ({"1": "A", "2": "B"}, POT),
        "R3": ({"1": "A", "2": "B"}, "kicad:Device:R@"),
        "C2": ({"1": "A", "2": "B"}, "kicad:Device:C@"),
        "D4": ({"1": "A", "2": "B"}, "kicad:Device:LED@"),
        "SW1": ({"1": "A", "2": "B"}, "kicad:Switch:SW_Push@"),
        "BZ1": ({"1": "A", "2": "B"}, "kicad:Device:Buzzer@"),
        "J2": ({"1": "A", "2": "B", "3": "C"}, "kicad:Connector_Generic:Conn_01x03@"),
    }
    for name, (pins, want) in cases.items():
        part = {"ref": "X1", "piece": name, "pins": pins}
        kept: list = []
        t0 = time.time()
        live._resolve_unknown(_st(), [part], kept, set(), set())
        assert part["piece"].startswith(want), (name, part["piece"])
        assert time.time() - t0 < 2


def test_generic_names_never_go_to_web_sourcing(monkeypatch):
    calls = []
    monkeypatch.setattr(live, "source_missing", lambda s, m: calls.append(m) or [])
    monkeypatch.setattr(live, "_twin_piece", lambda *a: None)
    monkeypatch.setattr(live, "_catalog_hit", lambda t: None)
    for name in ("RV1", "R3", "potentiometer", "LED", "USB1"):
        live._resolve_unknown(
            _st(), [{"ref": "X1", "piece": name, "pins": {}}], [], set(), set()
        )
    assert calls == []
    assert live._ref_word("RV1") == "RV" and live._ref_word("Pico") is None


def test_misspelt_library_prefix():
    key = "kicad:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical"
    part = {
        "ref": "R1",
        "piece": "kicid:Device:R@R_Axial_DIN0204_L3.6mm_D1.6mm_P2.54mm_Vertical",
        "pins": {},
    }
    live._resolve_unknown(_st(), [part], [{"key": key}], {key}, set())
    assert part["piece"] == key


def test_sourcing_budget_is_per_run(monkeypatch):
    seen = []

    def fake_find(query, stream, allow_network=True, deadline=None):
        seen.append(deadline)
        return {"status": "not_found", "tiers": []}

    monkeypatch.setattr(parts_live, "find_part", fake_find)
    monkeypatch.setattr(live, "_catalog_hit", lambda t: None)
    st = _st()
    t0 = time.time()
    live.source_missing(st, [{"what": "HC-SR501 PIR", "search": "HC-SR501"}])
    live.source_missing(st, [{"what": "MQ-2 gas", "search": "MQ-2"}])
    assert seen and len(set(seen)) == 1  # one deadline shared by every call in the run
    assert (
        t0 + live.SOURCING_BUDGET_S - 1
        <= seen[0]
        <= time.time() + live.SOURCING_BUDGET_S
    )


def test_find_part_stops_at_the_deadline(monkeypatch):
    monkeypatch.setattr(parts_live, "search_index", lambda q: [])
    for f in ("you_find", "jlc_search", "github_fetch", "web_search"):
        monkeypatch.setattr(
            parts_live,
            f,
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("network tier ran")),
        )
    res = parts_live.find_part("HC-SR501", _st(), deadline=time.time() - 1)
    assert res["status"] == "not_found"
    assert [t["tier"] for t in res["tiers"]] == ["local", "budget"]
