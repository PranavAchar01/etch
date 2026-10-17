"""ESP32 support: vendored Espressif pieces, the reference power block, the wifi sensor recipe, the Pico W swap,
the rail-MCU and LDO simulation roles, and the MicroPython firmware tests. No KiCad run needed."""

import copy
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from agent import esp32, firmware, firmware_esp32, kicad_catalog, live, recipes
from etchlib import sim
from etchlib.kicad import VENDOR_LIBS
from etchlib.spec import load_spec

VENDOR = VENDOR_LIBS["Espressif"]


@pytest.fixture(scope="module")
def cat():
    return {p["key"]: p for p in kicad_catalog.load()["pieces"]}


# ---------------------------------------------------------------- catalog


def test_vendor_clone_has_provenance():
    prov = (VENDOR / "PROVENANCE.md").read_text()
    assert "3.2.1" in prov and "1dfc3110895c9cd62daf332f49c49ee0ee200831" in prov
    assert (VENDOR / "LICENSE.md").is_file()


@pytest.mark.parametrize(
    "key,chip,v3,reserved",
    [
        (esp32.S3, "esp32s3", "2", {"27", "13", "14"}),
        (esp32.C3, "esp32c3", "3", {"23", "26", "27", "5", "22"}),
    ],
)
def test_module_pieces(cat, key, chip, v3, reserved):
    p = cat[key]
    assert p["pads_ok"] and p["source"] == "vendor-lib"
    assert p["model"] and Path(p["model"]).is_file() and str(VENDOR) in p["model"]
    sp = p["spice"]
    assert (
        sp["role"] == "mcu" and sp["rail"] and sp["chip"] == chip and sp["pins"] == [v3]
    )
    assert set(sp["reserved"]) == reserved
    assert not reserved & set(
        sp["gpio"]
    )  # strap and USB pins are never handed to firmware
    assert p["provenance"]["commit"].startswith("1dfc311")
    assert "CC-BY-SA" in p["provenance"]["licence"]


def test_power_pieces(cat):
    ldo = cat[esp32.LDO]
    assert ldo["pads_ok"] and ldo["model"] and ldo["spice"]["role"] == "ldo"
    assert ldo["spice"]["pins"] == ["3", "2", "1"]  # VI, VO, GND
    usb = cat[esp32.USBC16]
    assert usb["pads_ok"] and usb["model"] and usb["spice"]["role"] == "power_in"
    assert {"A6", "B6", "A7", "B7"} <= set(
        usb["pins"]
    )  # the data pair the 6P power-only part lacks


def test_catalog_kept_old_pieces(cat):
    for k in (recipes.PICO, recipes.PICO_W, recipes.R, recipes.LED, recipes.DHT):
        assert k in cat


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize(
    "ask,chip",
    [
        ("an esp32 wifi temperature and humidity sensor", "esp32s3"),
        ("a wifi weather station", "esp32s3"),
        ("esp32-c3 temperature sensor", "esp32c3"),
        ("an esp32 board", "esp32s3"),
    ],
)
def test_sensor_recipe(cat, ask, chip):
    d, name, _ = recipes.match(ask, cat)
    assert name == "esp32_sensor" and d["chip"] == chip
    refs = [p["ref"] for p in d["parts"]]
    assert len(refs) == len(set(refs))
    pieces = [p["piece"] for p in d["parts"]]
    assert pieces[0] == esp32.USBC16  # first, so the simulation takes it as the supply
    assert esp32.LDO in pieces and esp32.CHIP[chip]["piece"] in pieces
    vals = sorted(p["value"] for p in d["parts"] if p["piece"] == esp32.SW)
    assert vals == ["BOOT", "RESET"]
    assert d["rules"] == esp32.FINE_PITCH


def test_plain_temperature_stays_pico(cat):
    d, name, _ = recipes.match("a thermometer", cat)
    assert name == "temp_display" and d["parts"][0]["piece"] == recipes.PICO


@pytest.mark.parametrize(
    "ask,recipe",
    [("a wifi led strip with a knob", "strip_knob"), ("a wifi motor car", "motor_car")],
)
def test_wireless_recipes_move_to_s3(cat, ask, recipe):
    d, name, _ = recipes.match(ask, cat)
    assert name == recipe and d["chip"] == "esp32s3"
    assert not any("RaspberryPi_Pico" in p["piece"] for p in d["parts"])
    assert "ESP32-S3" in d["say"] and "Pico" not in d["say"]


def test_pico_named_keeps_pico_w(cat):
    d, name, _ = recipes.match("a pico w led strip with wifi", cat)
    assert name == "strip_knob" and d["parts"][0]["piece"] == recipes.PICO_W


def test_swap_needs_the_pieces(cat):
    d = recipes.strip_knob("a wifi led strip")
    short = {k: v for k, v in cat.items() if k != esp32.S3}
    assert esp32.swap(d, short, "esp32s3") is None


# ---------------------------------------------------------------- firmware + simulation, as live.assemble runs them


def _assemble(d, cat, out):
    kept = list({p["piece"]: cat[p["piece"]] for p in d["parts"]}.values())
    live._normalize_pins(d, kept)
    firmware.tie_grounds(d, kept)
    live._drop_lonely_gpio(d, kept)
    live._fix_refs(d)
    assert (
        not live._lonely_nets(d)
        and not live._ref_mismatch(d)
        and not live._wiring_lint(d, kept)
    )
    fw = firmware.build(d, kept, out)
    assert not isinstance(fw, str), fw
    d["scenarios"], rep = fw
    fb = firmware.benchmarks(d, kept)
    assert not isinstance(fb, str), fb
    d["benchmarks"] = fb
    spec = live.to_spec(d, "TEST")
    (out / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return spec, rep


def _check(res, benches):
    bad = []
    for b in benches:
        if "." not in b["check"] and not b["check"].startswith("supply"):
            continue  # drc / erc / parity come from the real KiCad build, not from ngspice
        v = res.get(b["check"])
        if (
            v is None
            or (b.get("min") is not None and v < b["min"])
            or (b.get("max") is not None and v > b["max"])
        ):
            bad.append((b["check"], v))
    return bad


@pytest.mark.skipif(
    not (shutil.which("ngspice") or Path("/opt/homebrew/bin/ngspice").exists()),
    reason="no ngspice",
)
@pytest.mark.parametrize(
    "ask",
    [
        "an esp32 wifi temperature and humidity sensor",
        "esp32-c3 temperature sensor",
        "a wifi motor car",
    ],
)
def test_firmware_and_sim(cat, ask):
    d = copy.deepcopy(recipes.match(ask, cat)[0])
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        spec, rep = _assemble(d, cat, out)
        assert rep["pass"]
        assert spec["rules"]["track"] == 0.2 and spec["rules"]["clearance"] == 0.15
        main = (out / "firmware" / "main.py").read_text()
        assert "BOOTSEL" not in main and "esptool.py" in main
        res = sim.simulate(load_spec(out / "spec.yaml"), out)
        assert res["ok"], res
        assert not _check(res, spec["benchmarks"])
        supply = [v for k, v in res.items() if k.endswith("supply_ma_avg")]
        assert (
            min(supply) > 60
        )  # the module's Wi-Fi receive current is counted through the LDO


def test_readout_firmware_wifi_json():
    gpio = {"DHT_DATA": 4, "KNOB": 1}
    drv = [{"kind": "dht11", "ref": "U3", "gpio": 4, "net": "DHT_DATA"}]
    code = firmware_esp32.generate_readout(gpio, drv, "T", "esp32s3")
    assert "import dht" in code and "ADC.ATTN_11DB" in code and 'WIFI_SSID = ""' in code
    proc, printed, _, http = firmware_esp32._run(code, wifi=True)
    assert (
        proc.returncode == 0
        and http
        and '"temperature_c": 23' in http[0]
        and '"KNOB": 40000' in http[0]
    )


def test_retarget_command_firmware():
    main = firmware.generate([{"input": "f", "high": ["A"]}], {"A": 4}, "CAR")
    out = firmware_esp32.retarget(main, "esp32c3")
    assert "Raspberry Pi Pico" not in out and "BOOTSEL" not in out
    assert "ESP32-C3" in out and "--chip esp32c3" in out


def test_sound_mode_rejected_on_esp32(cat):
    d = recipes.match("an esp32 board", cat)[0]
    d = dict(d, firmware={"mode": "sound"})
    kept = list({p["piece"]: cat[p["piece"]] for p in d["parts"]}.values())
    with tempfile.TemporaryDirectory() as td:
        assert "Pico-only" in firmware.build(d, kept, Path(td))
