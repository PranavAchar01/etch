"""Crawl GitHub for public KiCad projects with an allow-listed licence and list their boards.

    python3 library/crawl.py --limit 200          # repos with at least one board, stars desc
    python3 library/crawl.py --refresh            # ignore the per-repo cache

Writes library/cache/repos.json (every repo looked at, with the reason it was kept or skipped) and
library/cache/boards.json (one record per .kicad_pcb to fetch and render).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
import urllib.parse

from common import (BOARDS_JSON, REPOS_JSON, copyable, gh, load_json, log, save_json, slugify)

SEARCHES = [
    "topic:kicad",
    "topic:kicad-pcb",
    "topic:open-hardware kicad",
    "topic:pcb kicad",
    "topic:kicad-project",
    "topic:keyboard topic:kicad",
]
OWNERS: list[str] = []  # orgs/users whose public repos are always included, first
# owners never crawled, stored as sha256 of the lower-case login so the names stay out of the repo
DENY_OWNER_SHA256 = {"e574c641d65c0913772ca9bc92622b76df3f2e5ee4427c6f691731ed550e444e"}


def denied(owner: str) -> bool:
    return hashlib.sha256(owner.lower().encode()).hexdigest() in DENY_OWNER_SHA256
# hand-picked extras (always included): ESP32 boards with Ethernet/RJ45 + USB, for the board view demo
PINNED_REPOS = ["yellobyte/YB-ESP32-S3-ETH", "Hans-Beerman/Deurnode_ESP32-PoE", "kazkojima/esp32-phy",
                "enkhbold470/iron-node"]
# boards pinned by hand to an exact commit and pcb list (reference boards for the board view)
PINNED_BOARDS = {
    "OLIMEX/ESP32-POE": {
        "commit": "deb38a377d50eadb04ddc7118a17a5ae58b1ee53",
        "pcbs": ["HARDWARE/ESP32-PoE-hardware-revision-M2/ESP32-PoE_Rev_M2.kicad_pcb",
                 "HARDWARE/ESP32-PoE-hardware-revision-M1/ESP32-PoE_Rev_M1.kicad_pcb",
                 "HARDWARE/ESP32-PoE-hardware-revision-M/ESP32-PoE_Rev_M.kicad_pcb"],
        "category": "dev-boards",
    },
}
MAX_BOARDS_PER_REPO = 3
MAX_PCB_BYTES = 25_000_000

# path segments that mean "not a real board": backups, old revisions, library examples, templates
SKIP_SEG = re.compile(
    r"(backup|archive|^old$|^old[-_ .]|[-_ ]old$|^_?libs?$|^librar(y|ies)$|template|example|deprecated|obsolete|"
    r"^\.|^test(s|ing)?$|^footprints?$|^symbols?$|^3d|^packages3d$|^extern(al)?$|^third[-_]?party$|^vendor$|"
    r"^node_modules$|^kicad[-_]?(lib|library|libraries|footprints|symbols)$)",
    re.I,
)
SKIP_FILE = re.compile(r"(_autosave|\.bak$|-bak\.|^_|-backup|_backup|-old\.|_old\.)", re.I)
# repos that are libraries/tools rather than boards
SKIP_REPO = re.compile(
    r"(awesome|footprint|symbol[s-]|[-_]lib(s|rary)?$|^lib[-_]|librar|plugin|kicad[-_]?(action|tools?|scripts?|templates?)|"
    r"interactivehtmlbom|kibot|kikit|cookbook|tutorial|workshop|course|kicad-source|^kicad$)",
    re.I,
)

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("keyboards", ["keyboard", "keeb", "macropad", "macro-pad", "split-keyboard", "qmk", "zmk", "kmk", "mechanical-keyboard",
                   "keycap", "ortholinear", "numpad", "corne", "sofle", "lily58", "hotswap", "choc"]),
    ("wearables", ["wearable", "watch", "smartwatch", "badge", "ring", "bracelet", "glasses", "flex-pcb", "wristband"]),
    ("retro", ["retro", "8051", "z80", "6502", "6809", "68000", "commodore", "c64", "amiga", "atari", "nes", "snes", "gameboy",
               "game-boy", "zx", "spectrum", "msx", "apple-ii", "apple2", "vintage", "homebrew-computer", "ttl", "cpld",
               "sega", "nintendo", "cartridge", "floppy", "8-bit", "8bit", "pdp", "s100", "isa"]),
    ("robotics", ["robot", "robotics", "servo", "motor", "bldc", "esc", "foc", "drone", "quadcopter", "fpv", "rover",
                  "flight-controller", "stepper", "cnc", "3d-printer", "printer", "animatronic", "hexapod", "arm",
                  "ros", "odrive", "vesc", "motor-driver"]),
    ("audio", ["audio", "synth", "synthesizer", "eurorack", "amplifier", "amp", "dac", "headphone", "speaker", "midi",
               "guitar", "pedal", "effects", "modular", "i2s", "sound", "mixer", "preamp", "microphone", "vco", "vcf"]),
    ("rf", ["rf", "radio", "sdr", "antenna", "lora", "lorawan", "meshtastic", "ham", "ham-radio", "transceiver", "gnss",
            "gps", "uwb", "433mhz", "nrf24", "zigbee", "802.15.4", "rfid", "nfc", "cc1101", "si4463", "mmwave"]),
    ("displays", ["display", "oled", "lcd", "e-ink", "eink", "epaper", "e-paper", "tft", "nixie", "vfd", "matrix", "clock",
                  "segment", "dot-matrix", "screen"]),
    ("lighting", ["led", "leds", "lighting", "light", "lamp", "ws2812", "neopixel", "rgb", "sk6812", "apa102", "flashlight",
                  "torch", "night-light", "wled", "dmx", "strip"]),
    ("power", ["power", "power-supply", "psu", "battery", "bms", "charger", "buck", "boost", "converter", "usb-pd", "pd",
               "solar", "ups", "smps", "regulator", "lipo", "load", "electronic-load", "inverter", "mppt"]),
    ("tools", ["tool", "tools", "tester", "test", "multimeter", "oscilloscope", "logic-analyzer", "analyzer", "programmer",
               "debugger", "jtag", "swd", "probe", "breakout", "adapter", "measurement", "instrument", "signal-generator",
               "function-generator", "bench", "usb-c-tester", "ruler", "cable-tester", "continuity", "power-meter"]),
    ("computing", ["computer", "computing", "fpga", "risc-v", "riscv", "cpu", "sbc", "single-board-computer", "linux",
                   "raspberry-pi", "raspberrypi", "cm4", "compute-module", "pcie", "ddr", "soc", "server", "nas", "ice40",
                   "ecp5", "xilinx", "lattice", "zynq", "imx", "allwinner", "rockchip"]),
    ("iot", ["iot", "home-automation", "homeassistant", "home-assistant", "esphome", "smart-home", "wifi", "ble",
             "bluetooth", "mqtt", "sensor", "sensors", "weather", "co2", "air-quality", "thread", "matter", "tasmota",
             "zigbee", "esp8266", "smart", "plant", "soil", "environmental"]),
    ("dev-boards", ["dev-board", "devboard", "development-board", "devkit", "dev-kit", "evaluation", "eval", "breakout-board",
                    "arduino", "esp32", "esp32-s3", "esp32-c3", "rp2040", "rp2350", "stm32", "atmega", "attiny", "samd",
                    "nrf52", "ch32", "ch32v003", "microcontroller", "mcu", "feather", "pico", "teensy", "blackpill",
                    "bluepill", "minimal", "board"]),
]


def categorize(name: str, desc: str, topics: list[str]) -> str:
    words = set(topics)
    text = f"{name} {desc}".lower()
    toks = set(re.split(r"[^a-z0-9.+]+", text)) | set(re.split(r"[^a-z0-9.+-]+", text))
    words |= toks
    # topics are the strongest signal, then words in name/description
    for cat, kws in CATEGORY_RULES:
        if any(k in topics for k in kws):
            return cat
    for cat, kws in CATEGORY_RULES:
        if any(k in words for k in kws):
            return cat
    return "everything-else"


ACRONYMS = {"usb", "led", "rgb", "pcb", "mcu", "i2c", "spi", "rf", "sdr", "lora", "oled", "lcd", "tft", "fpga", "cpu", "ble",
            "esc", "fc", "gps", "dac", "adc", "psu", "bms", "io", "iot", "pd", "ttl", "vfd", "midi", "mp3", "nfc", "uwb", "dc",
            "ac", "hdmi", "jtag", "swd", "uart", "can", "rs485", "sbc", "tv", "sd", "ups"}


def humanize(s: str) -> str:
    words = [w for w in re.split(r"[-_ ]+", s) if w]
    out = []
    for w in words:
        lw = w.lower()
        if lw in ACRONYMS or (re.search(r"\d", w) and len(w) <= 8 and w.lower() == w):
            out.append(w.upper())
        elif w.islower():
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out) or s


def search_repos(pages: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for q in SEARCHES:
        for page in range(1, pages + 1):
            qs = urllib.parse.quote(q)
            res = gh(f"search/repositories?q={qs}&sort=stars&order=desc&per_page=100&page={page}", search=True)
            items = (res or {}).get("items") or []
            for it in items:
                found.setdefault(it["full_name"], it)
            log(f"search {q!r} p{page}: {len(items)} (total unique {len(found)})")
            if len(items) < 100:
                break
            time.sleep(2.2)  # search API: 30 requests/minute
    return found


def owner_repos(owner: str) -> dict[str, dict]:
    out = {}
    for kind in ("orgs", "users"):
        res = gh(f"{kind}/{owner}/repos?per_page=100&type=public")
        if res:
            for it in res:
                out[it["full_name"]] = it
            break
    return out


def pick_pcbs(tree: list[dict]) -> list[dict]:
    pcbs = []
    for e in tree:
        if e.get("type") != "blob" or not e["path"].lower().endswith(".kicad_pcb"):
            continue
        parts = e["path"].split("/")
        if any(SKIP_SEG.search(seg) for seg in parts[:-1]) or SKIP_FILE.search(parts[-1]):
            continue
        if e.get("size", 0) > MAX_PCB_BYTES or e.get("size", 0) < 2000:
            continue
        pcbs.append(e)
    # shallow first, then newest-looking revision (v3 before v2) by reverse name
    pcbs.sort(key=lambda e: (e["path"].count("/"), [-ord(c) for c in e["path"].lower()]))
    return pcbs[:MAX_BOARDS_PER_REPO]


def repo_record(item: dict) -> dict:
    lic = (item.get("license") or {}).get("spdx_id")
    return {
        "full_name": item["full_name"],
        "owner": item["owner"]["login"],
        "repo": item["name"],
        "description": item.get("description") or "",
        "topics": item.get("topics") or [],
        "stars": item.get("stargazers_count", 0),
        "default_branch": item.get("default_branch") or "main",
        "license": lic,
        "archived": item.get("archived", False),
        "fork": item.get("fork", False),
        "pushed_at": item.get("pushed_at"),
    }


def inspect_repo(rec: dict) -> None:
    """Fill commit, license path and pcbs for one repo (3 core API calls)."""
    o, r = rec["owner"], rec["repo"]
    pin = PINNED_BOARDS.get(rec["full_name"])
    if pin:
        rec["commit"] = pin["commit"]
    else:
        br = gh(f"repos/{o}/{r}/branches/{urllib.parse.quote(rec['default_branch'], safe='')}")
        if not br:
            rec["status"] = "skip: no default branch"
            return
        rec["commit"] = br["commit"]["sha"]
    tree = gh(f"repos/{o}/{r}/git/trees/{rec['commit']}?recursive=1")
    if not tree:
        rec["status"] = "skip: no tree"
        return
    entries = tree.get("tree") or []
    rec["tree_truncated"] = bool(tree.get("truncated"))
    if pin:
        sizes = {e["path"]: e.get("size", 0) for e in entries}
        pcbs = [{"path": p, "size": sizes.get(p, 0)} for p in pin["pcbs"] if p in sizes]
    else:
        pcbs = pick_pcbs(entries)
    if not pcbs:
        rec["status"] = "skip: no .kicad_pcb"
        return
    # keep only the tree paths fetch.py may need (3D models, project/schematic files) to keep the cache small
    keep = re.compile(r"\.(kicad_pro|kicad_sch|pro|sch|step|stp|stpz|wrl|wrz|vrml)$", re.I)
    rec["files"] = {e["path"]: e.get("size", 0) for e in entries if e.get("type") == "blob" and keep.search(e["path"])}
    rec["pcbs"] = [{"path": e["path"], "size": e.get("size", 0)} for e in pcbs]
    lic = gh(f"repos/{o}/{r}/license")
    rec["license_path"] = (lic or {}).get("path") or "LICENSE"
    rec["status"] = "ok"


def board_records(rec: dict) -> list[dict]:
    out = []
    multi = len(rec["pcbs"]) > 1
    used = set()
    for p in rec["pcbs"]:
        path = p["path"]
        d, _, fname = path.rpartition("/")
        stem = fname[: -len(".kicad_pcb")]
        sub = d if (multi and d) else (stem if multi else "")
        if sub in used:
            sub = f"{d}/{stem}" if d else stem
        used.add(sub)
        bid = f"gh:{rec['owner']}/{rec['repo']}" + (f"/{sub}" if sub else "")
        title = humanize(rec["repo"])
        if multi:
            title += " · " + humanize(stem if stem.lower() != rec["repo"].lower() else (d.rsplit("/", 1)[-1] or stem))
        out.append({
            "id": bid,
            "slug": slugify(bid),
            "title": title,
            "owner": rec["owner"],
            "repo": rec["repo"],
            "commit": rec["commit"],
            "pcb_path": path,
            "pcb_bytes": p["size"],
            "category": PINNED_BOARDS.get(rec["full_name"], {}).get("category")
            or categorize(rec["repo"] + " " + stem, rec["description"], rec["topics"]),
            "stars": rec["stars"],
            "license": rec["license"],
            "copyable": copyable(rec["license"]),
            "description": rec["description"],
            "topics": rec["topics"],
            "license_path": rec.get("license_path", "LICENSE"),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=150, help="stop after this many repos with boards")
    ap.add_argument("--pages", type=int, default=10, help="search pages (100 repos each) per query")
    ap.add_argument("--refresh", action="store_true", help="re-inspect repos already in the cache")
    ap.add_argument("--min-stars", type=int, default=0)
    ap.add_argument("--no-search", action="store_true", help="reuse repos already in the cache instead of searching")
    args = ap.parse_args()

    cache: dict[str, dict] = {r["full_name"]: r for r in load_json(REPOS_JSON, [])}
    cand: dict[str, dict] = {}
    for o in OWNERS:
        cand.update(owner_repos(o))
    for full in [*PINNED_BOARDS, *PINNED_REPOS]:
        it = gh(f"repos/{full}")
        if it:
            cand[it["full_name"]] = it
    pinned = set(cand)
    if not args.no_search:
        cand.update(search_repos(args.pages))

    cand = {k: v for k, v in cand.items() if not denied(v["owner"]["login"])}
    cache = {k: v for k, v in cache.items() if not denied(v["owner"])}
    recs = []
    if args.no_search:
        recs = [r for n, r in cache.items() if n not in cand]
    for name, item in cand.items():
        rec = repo_record(item)
        old = cache.get(name)
        pin = PINNED_BOARDS.get(name)
        if old and not args.refresh and old.get("status") and old.get("pushed_at") == rec["pushed_at"] \
                and (not pin or old.get("commit") == pin["commit"]):
            old.update({k: rec[k] for k in ("stars", "topics", "description")})
            rec = old
        recs.append(rec)
    recs.sort(key=lambda r: (r["full_name"] not in PINNED_BOARDS, r["full_name"] not in pinned, -r["stars"]))

    kept = 0
    for rec in recs:
        if kept >= args.limit:
            break
        if rec.get("status") == "ok":
            kept += 1
            continue
        if rec.get("status"):
            continue
        if not rec["license"] or rec["license"] == "NOASSERTION":
            rec["status"] = "skip: no licence" if not rec["license"] else "skip: NOASSERTION licence"
            continue
        if not copyable(rec["license"]):
            rec["status"] = f"skip: licence {rec['license']} not on allow-list"
            continue
        if rec["fork"]:
            rec["status"] = "skip: fork"
            continue
        if SKIP_REPO.search(rec["repo"]):
            rec["status"] = "skip: library/tool repo"
            continue
        if rec["stars"] < args.min_stars and rec["full_name"] not in pinned:
            rec["status"] = "skip: stars"
            continue
        inspect_repo(rec)
        log(f"{rec['full_name']:50s} {rec['stars']:6d}* {rec['license']:16s} {rec['status']}"
            + (f" ({len(rec['pcbs'])} pcb)" if rec.get("pcbs") else ""))
        if rec["status"] == "ok":
            kept += 1
        save_json(REPOS_JSON, list({**cache, **{r["full_name"]: r for r in recs}}.values()))

    allrecs = {**cache, **{r["full_name"]: r for r in recs}}
    save_json(REPOS_JSON, list(allrecs.values()))
    boards = []
    order = {r["full_name"]: i for i, r in enumerate(recs)}
    for rec in sorted((r for r in allrecs.values() if r.get("status") == "ok"),
                      key=lambda r: order.get(r["full_name"], 1e9)):
        boards.extend(board_records(rec))
    save_json(BOARDS_JSON, boards)
    st: dict[str, int] = {}
    for r in allrecs.values():
        k = (r.get("status") or "not inspected").split(":")[0] + (": " + r["status"].split(":", 1)[1].strip()
                                                                     if r.get("status", "").startswith("skip") else "")
        st[k] = st.get(k, 0) + 1
    log(f"repos seen {len(allrecs)}, kept {sum(1 for r in allrecs.values() if r.get('status') == 'ok')}, boards {len(boards)}")
    for k, v in sorted(st.items(), key=lambda kv: -kv[1]):
        log(f"  {v:5d}  {k}")


if __name__ == "__main__":
    main()
