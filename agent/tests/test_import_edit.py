"""In-place edits of imported boards (agent/import_edit.py): the plan check against a real netlist, the colour-only
path, stackup edits, DRC deltas and the run with the model stubbed. Nothing here starts KiCad.

uv run --project engine python -m pytest agent/tests/test_import_edit.py
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import import_edit as ie

CAT = {
    "led_green_0603": {"key": "led_green_0603", "lib_id": "Device:LED", "footprint": "LED_SMD:LED_0603_1608Metric",
                       "family": "led", "name": "LED", "value": "GREEN", "pins": ["1", "2"], "model": None,
                       "source": "index"},
    "resistor_0603": {"key": "resistor_0603", "lib_id": "Device:R", "footprint": "Resistor_SMD:R_0603_1608Metric",
                      "family": "passive", "name": "R", "value": "10k", "pins": ["1", "2"], "model": None,
                      "source": "index"},
    "tact_smd_tl3342": {"key": "tact_smd_tl3342", "lib_id": "Switch:SW_Push", "footprint": "Button_Switch_SMD:SW_SPST_TL3342",
                        "family": "switch", "name": "SW_Push", "value": "SW_Push", "pins": ["1", "2"], "model": None,
                        "source": "index"},
}


def pad(num, net, fn="", pitch=1.27):
    return {"num": num, "net": net, "fn": fn, "layers": ["F.Cu"], "x": int(num) * pitch, "y": 0}


FACTS = {
    "board": {"w": 30, "h": 50, "copper": ["F.Cu", "B.Cu"], "bbox": [0, 0, 30, 50],
              "rules": {"clearance": 0.2, "track": 0.25, "via_d": 0.6, "via_drill": 0.3, "min_clearance": 0.15,
                        "min_track": 0.15, "edge_clearance": 0.3}},
    "parts": [
        {"ref": "U1", "value": "ESP32", "fpid": "RF_Module:ESP32-WROOM-32", "side": "top", "x": 10, "y": 10,
         "pads": [pad("1", "GND", "GND"), pad("2", "+3V3", "3V3"), pad("3", "/EN", "EN"), pad("14", "/GPIO12", "IO12"),
                  pad("15", "", "IO13"), pad("16", "unconnected-(U1-IO14-Pad16)", "IO14"), pad("17", "/SDA", "IO21")]},
        {"ref": "J1", "value": "Conn", "fpid": "Connector:PinHeader_1x04", "side": "top", "x": 20, "y": 30,
         "pads": [pad("1", "GND"), pad("2", "+3V3"), pad("3", "/GPIO12"), pad("4", "/SDA")]},
        {"ref": "R1", "value": "10k", "fpid": "Resistor_SMD:R_0603_1608Metric", "side": "top", "x": 5, "y": 5,
         "pads": [pad("1", "/EN"), pad("2", "+3V3")]},
        {"ref": "C1", "value": "100n", "fpid": "Capacitor_SMD:C_0603_1608Metric", "side": "top", "x": 6, "y": 5,
         "pads": [pad("1", "+3V3"), pad("2", "GND")]},
        {"ref": "U3", "value": "QFN", "fpid": "x:QFN", "side": "top", "x": 1, "y": 1,
         "pads": [pad("1", "GND", pitch=0.4), pad("2", "", "GPIO5", pitch=0.4), pad("3", "/SDA", pitch=0.4)]},
        {"ref": "U2", "value": "SENS", "fpid": "x:y", "side": "top", "x": 6, "y": 25,
         "pads": [pad("1", "GND"), pad("2", "+3V3"), pad("3", "/SDA")]},
    ],
    "zones": {"GND": ["F.Cu", "B.Cu"]},
}
FACTS["nets"] = {}
for _p in FACTS["parts"]:
    for _q in _p["pads"]:
        if _q["net"]:
            FACTS["nets"].setdefault(_q["net"], []).append(f"{_p['ref']}.{_q['num']}")


def plan(**kw):
    base = {"say": "", "refuse": "", "add": [], "connect": [], "remove": [], "values": [],
            "board": {"mask": "", "silk": "", "finish": ""}}
    base.update(kw)
    return base


def add(ref, part, value="", near=""):
    return {"ref": ref, "part": part, "value": value, "near": near, "why": ""}


def con(pin, net="", to=""):
    return {"pin": pin, "net": net, "to": to, "why": ""}


class Validate(unittest.TestCase):
    def test_led_with_resistor_on_an_existing_gpio(self):
        p = plan(add=[add("D20", "led_green_0603", "green", "J1"), add("R20", "resistor_0603", "1k")],
                 connect=[con("R20.1", "/GPIO12"), con("R20.2", "LED_A"), con("D20.2", "LED_A"), con("D20.1", "GND")])
        r, errs = ie.validate(p, FACTS, CAT)
        self.assertEqual(errs, [])
        by = {a["ref"]: a for a in r["add"]}
        self.assertEqual(by["R20"]["pads"], {"1": "/GPIO12", "2": "LED_A"})
        self.assertEqual(by["D20"]["pads"], {"2": "LED_A", "1": "GND"})
        self.assertEqual(by["D20"]["near"], "J1")
        self.assertEqual(r["new_nets"], {"LED_A": ["D20.2", "R20.2"]})
        self.assertEqual(r["assign"], [])

    def test_to_an_existing_pin_uses_its_net(self):
        p = plan(add=[add("SW1", "tact_smd_tl3342")], connect=[con("SW1.1", to="R1.1"), con("SW1.2", "GND")])
        r, errs = ie.validate(p, FACTS, CAT)
        self.assertEqual(errs, [])
        self.assertEqual(r["add"][0]["pads"], {"1": "/EN", "2": "GND"})

    def test_free_pins_get_a_new_net_named_after_the_pin(self):
        p = plan(add=[add("BZ1", "resistor_0603")], connect=[con("BZ1.1", to="U1.15"), con("BZ1.2", "GND")])
        r, errs = ie.validate(p, FACTS, CAT)
        self.assertEqual(errs, [])
        self.assertEqual(r["add"][0]["pads"]["1"], "IO13")
        self.assertEqual(r["assign"], [{"ref": "U1", "pad": "15", "net": "IO13"}])
        # an "unconnected-(...)" net counts as free too
        p = plan(add=[add("BZ1", "resistor_0603")], connect=[con("BZ1.1", to="U1.16"), con("BZ1.2", "GND")])
        r, errs = ie.validate(p, FACTS, CAT)
        self.assertEqual(errs, [])
        self.assertEqual(r["assign"][0]["net"], "IO14")

    def test_fine_pitch_free_pins_are_refused(self):
        p = plan(add=[add("R20", "resistor_0603")], connect=[con("R20.1", to="U3.2"), con("R20.2", "GND")])
        _, errs = ie.validate(p, FACTS, CAT)
        self.assertTrue(any("fine-pitch" in e for e in errs), errs)

    def test_never_rewires_an_existing_pin(self):
        p = plan(add=[add("D20", "led_green_0603")], connect=[con("D20.1", "GND"), con("D20.2", "X1"), con("U1.17", "X1")])
        _, errs = ie.validate(p, FACTS, CAT)
        self.assertTrue(any("already on /SDA" in e for e in errs), errs)

    def test_never_shorts_two_nets(self):
        p = plan(add=[add("R20", "resistor_0603")], connect=[con("R20.1", "GND"), con("R20.1", "+3V3"),
                                                             con("R20.2", "GND")])
        _, errs = ie.validate(p, FACTS, CAT)
        self.assertTrue(any("short" in e for e in errs), errs)

    def test_invented_pins_nets_and_parts_are_refused(self):
        p = plan(add=[add("D20", "led_green_0603"), add("U9", "no_such_part"), add("R1", "resistor_0603")],
                 connect=[con("D20.3", "GND"), con("D20.1", "GND"), con("D20.2", to="U1.99"), con("Q7.1", "GND")])
        _, errs = ie.validate(p, FACTS, CAT)
        text = " | ".join(errs)
        self.assertIn("no pad 3", text)
        self.assertIn("no_such_part", text)
        self.assertIn("R1 is already on the board", text)
        self.assertIn("no pad U1.99", text)
        self.assertIn("Q7 is neither", text)
        self.assertIn("D20 pad 2 not connected", text)

    def test_a_new_net_needs_two_ends(self):
        p = plan(add=[add("D20", "led_green_0603")], connect=[con("D20.1", "GND"), con("D20.2", "LONELY")])
        _, errs = ie.validate(p, FACTS, CAT)
        self.assertTrue(any("alone on its net" in e for e in errs), errs)

    def test_removals_values_and_colours(self):
        p = plan(remove=["C1", "Z9"], values=[{"ref": "R1", "value": "4.7k"}], board={"mask": "black", "silk": "",
                                                                                      "finish": "enig"})
        r, errs = ie.validate(p, FACTS, CAT)
        self.assertEqual(errs, ["cannot remove Z9: it is not on the board"])
        self.assertEqual(r["remove"], ["C1"])
        self.assertEqual(r["values"], {"R1": "4.7k"})
        self.assertEqual(r["board"], {"mask": "Black", "finish": "ENIG"})
        _, errs = ie.validate(plan(board={"mask": "plaid", "silk": "", "finish": ""}), FACTS, CAT)
        self.assertTrue(errs and "plaid" in errs[0])

    def test_connecting_to_a_removed_part_is_refused(self):
        p = plan(remove=["U2"], add=[add("R20", "resistor_0603")], connect=[con("R20.1", to="U2.3"),
                                                                            con("R20.2", "GND")])
        _, errs = ie.validate(p, FACTS, CAT)
        self.assertTrue(any("removes" in e for e in errs), errs)

    def test_empty_plan(self):
        _, errs = ie.validate(plan(), FACTS, CAT)
        self.assertEqual(errs, ["the plan changes nothing"])


class ColourOnly(unittest.TestCase):
    def test_colour_requests_need_no_model(self):
        self.assertEqual(ie.colour_plan("make the board black")["board"], {"mask": "Black", "silk": "", "finish": ""})
        self.assertEqual(ie.colour_plan("red solder mask with black silkscreen and ENIG")["board"],
                         {"mask": "Red", "silk": "Black", "finish": "ENIG"})
        self.assertEqual(ie.colour_plan("use a blue PCB")["board"]["mask"], "Blue")

    def test_anything_else_goes_to_the_planner(self):
        self.assertIsNone(ie.colour_plan("add a red LED"))
        self.assertIsNone(ie.colour_plan("add a reset button"))
        self.assertIsNone(ie.colour_plan("what does this board do"))


STACK = """(kicad_pcb (version 20240108)
  (setup
    (stackup
      (layer "F.SilkS" (type "Top Silk Screen"))
      (layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01))
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "B.Cu" (type "copper") (thickness 0.035))
      (layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
      (layer "B.SilkS" (type "Bottom Silk Screen"))
      (copper_finish "None")
      (dielectric_constraints no)
    )
    (pad_to_mask_clearance 0)
  )
)
"""


class Stackup(unittest.TestCase):
    def test_sets_mask_silk_and_finish(self):
        t = ie.set_stackup(STACK, {"mask": "Black", "silk": "Yellow", "finish": "ENIG"})
        self.assertIn('(layer "F.Mask" (type "Top Solder Mask") (color "Black") (thickness 0.01))', t)
        self.assertIn('(layer "B.Mask" (type "Bottom Solder Mask") (color "Black") (thickness 0.01))', t)
        self.assertIn('(layer "F.SilkS" (type "Top Silk Screen") (color "Yellow"))', t)
        self.assertIn('(copper_finish "ENIG")', t)
        self.assertEqual(t.count("(color"), 4)

    def test_adds_a_stackup_when_the_board_has_none(self):
        t = ie.set_stackup("(kicad_pcb (setup (pad_to_mask_clearance 0)))", {"mask": "Red"}, copper=4)
        self.assertIn('(layer "In2.Cu" (type "copper")', t)
        self.assertIn('(layer "F.Mask" (type "Top Solder Mask") (color "Red"))', t)
        self.assertEqual(t.count("("), t.count(")"))

    def test_nothing_to_set_keeps_the_file(self):
        self.assertEqual(ie.set_stackup(STACK, {}), STACK)


class DrcDelta(unittest.TestCase):
    def test_only_new_violations_count(self):
        old = {"type": "clearance", "severity": "error", "items": [{"uuid": "a"}, {"uuid": "b"}]}
        before = {"violations": [old], "unconnected_items": []}
        new = {"type": "shorting_items", "severity": "error", "description": "short", "items": [{"uuid": "c"}]}
        warn = {"type": "silk_over_copper", "severity": "warning", "items": [{"uuid": "d"}]}
        unc = {"type": "unconnected_items", "items": [{"uuid": "e", "description": "Pad 1 of D20"}]}
        d = ie.drc_delta(before, {"violations": [old, new, warn], "unconnected_items": [unc]})
        self.assertEqual((d["new_errors"], d["new_warnings"], d["new_unconnected"]), (1, 1, 1))
        self.assertEqual((d["errors_before"], d["errors_after"]), (1, 2))
        self.assertEqual(d["new_types"], ["shorting_items"])
        self.assertFalse(ie.drc_delta(before, {})["ran"])


class Context(unittest.TestCase):
    def test_board_text_lists_free_pins_and_header_gpios(self):
        t = ie.board_text(FACTS, "Test board")
        self.assertIn("U1.15 [IO13]", t)
        self.assertIn("U1.16 [IO14]", t)
        self.assertIn("/GPIO12 (U1.14, J1.3)", t)
        self.assertIn("14=/GPIO12[IO12]", t)
        self.assertNotIn("U1.14 [", t)


class RunStubbed(unittest.TestCase):
    """The run up to the KiCad step with the model stubbed: a plan with an invented pad is repaired once, and a
    refusal finishes cleanly with lead done ok false."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="etch-ie-test-"))
        self.out = self.tmp / "live-1792256559999"
        self.out.mkdir()
        base = self.tmp / "live-1792256550000"
        base.mkdir()
        (base / "board.kicad_pcb").write_text("(kicad_pcb)")
        (base / "design.json").write_text(json.dumps({"title": "T", "parts": []}))
        (base / "imported.json").write_text(json.dumps({"board": "gh:a/b", "inplace": True}))
        self.base = base

    def stream(self):
        from agent.events import Stream

        return Stream(self.out.name, log=self.out / "run.jsonl", echo=False)

    def patches(self):
        def fake_kicad(script, mode, pcb, out, *rest, **kw):
            Path(out).write_text(json.dumps(FACTS))

        return [mock.patch.object(ie, "kicad_python", fake_kicad), mock.patch.object(ie, "drc", lambda p, o: {}),
                mock.patch.object(ie, "catalog", lambda: CAT), mock.patch.object(ie, "_lock", mock.MagicMock()),
                mock.patch.object(ie.eng, "url", lambda p: "/" + Path(p).name)]

    def test_refusal_finishes_cleanly(self):
        st = self.stream()
        ps = self.patches()
        for p in ps:
            p.start()
        try:
            res = ie.run("add a fusion reactor", self.out, st, base=self.base,
                         complete=lambda msgs: plan(refuse="There is no part for that."))
        finally:
            for p in ps:
                p.stop()
        self.assertFalse(res["ok"])
        kinds = [(e["agent"], e["type"]) for e in st.events]
        self.assertIn(("picker", "pick"), kinds)
        self.assertEqual(kinds[-1], ("lead", "done"))
        self.assertIn("no part", st.events[-1]["error"])

    def test_bad_plan_goes_back_once_then_is_reported(self):
        calls = []

        def complete(msgs):
            calls.append(msgs)
            return plan(add=[add("D20", "led_green_0603")], connect=[con("D20.1", "GND"), con("D20.7", "/GPIO12")])

        st = self.stream()
        ps = self.patches()
        for p in ps:
            p.start()
        try:
            res = ie.run("add an LED on GPIO12", self.out, st, base=self.base, complete=complete)
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(len(calls), 3)  # first try + two repairs
        self.assertIn("no pad 7", calls[1][-1]["content"])
        self.assertFalse(res["ok"])
        gate = next(e for e in st.events if e["type"] == "gate")
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["gate"], "plan matches the board's netlist")


if __name__ == "__main__":
    unittest.main()
