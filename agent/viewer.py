"""The finished board for the page's 3D viewer, and the checked export zip.

  python -m agent.viewer glb RUN_DIR      -> RUN_DIR/view/board.glb (KiCad's own GLB export: library STEP
                                             models on every part, tracks, pads, silk, solder mask)
  python -m agent.viewer parts RUN_DIR    -> RUN_DIR/view/parts.json (ref, value, part, why, source per ref)
  python -m agent.viewer export RUN_DIR [--force]  -> fresh ERC + DRC; on a pass RUN_DIR/view/<name>-export.zip
                                             (Gerbers + drill, BOM CSV, STEP, GLB, KiCad project, firmware),
                                             on a fail the reasons and no zip

Nothing here runs during a build: the page asks for the GLB once the board is done, so build time is unchanged.
Each command prints one JSON line on stdout.
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from etchlib.build import _parse_drc, _parse_erc  # noqa: E402
from etchlib.glb import _export  # noqa: E402
from etchlib.kicad import VENDOR_LIBS, cli  # noqa: E402

from . import kicad_catalog, provenance  # noqa: E402

PACKS = Path(__file__).resolve().parent / "firmware_packs"

GLB_FLAGS = [
    "--subst-models",
    "--include-tracks",
    "--include-pads",
    "--include-silkscreen",
    "--include-soldermask",
]  # no --include-zones: the pour would hide the traces under one copper sheet (same call as the 2D frames)


def _pcb(run: Path) -> Path:
    return next(p for p in run.glob("*.kicad_pcb") if not p.name.startswith("_"))


def _fresh(dst: Path, src: Path) -> bool:
    return dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime


def glb(run: Path) -> dict[str, Any]:
    pcb = _pcb(run)
    view = run / "view"
    view.mkdir(exist_ok=True)
    out = view / "board.glb"
    if _fresh(out, pcb):
        return {
            "ok": True,
            "glb": str(out),
            "cached": True,
            "bytes": out.stat().st_size,
        }
    _, secs, err = _export(pcb, out, GLB_FLAGS)
    if err or not out.exists():
        return {"ok": False, "error": err or "kicad-cli wrote no GLB"}
    return {
        "ok": True,
        "glb": str(out),
        "seconds": round(secs, 2),
        "bytes": out.stat().st_size,
    }


def _model_url(model: str | None) -> str | None:
    if model and "3dmodels/" in model:
        return (
            f"https://gitlab.com/kicad/libraries/kicad-packages3D/-/blob/{provenance.kicad_version()}/"
            + model.split("3dmodels/", 1)[1]
        )
    return None


def parts(run: Path) -> dict[str, Any]:
    d = json.loads((run / "design.json").read_text())
    cat = {p["key"]: p for p in kicad_catalog.load()["pieces"]}
    rows = {}
    for p in d.get("parts", []):
        e = cat.get(p.get("piece")) or {}
        src = provenance.short(e) if e else {}
        rows[p["ref"]] = {
            "ref": p["ref"],
            "value": p.get("value") or "",
            "name": e.get("name")
            or (p.get("piece") or "").split(":")[-1].split("@")[0],
            "family": e.get("family") or "",
            "footprint": (e.get("footprint") or (p.get("piece") or "").split("@")[-1]),
            "why": p.get("why") or "",
            "group": p.get("group") or "",
            "source": src.get("source_url"),
            "model": src.get("model_url") or _model_url(e.get("model")),
            "licence": src.get("licence"),
            "route": src.get("route"),
        }
    out = {"title": d.get("title"), "name": d.get("name"), "parts": rows}
    view = run / "view"
    view.mkdir(exist_ok=True)
    (view / "parts.json").write_text(json.dumps(out, indent=1))
    return {"ok": True, **out}


def check(run: Path, tmp: Path) -> dict[str, Any]:
    """ERC and DRC again on the files that go in the zip (not the numbers stored at build time)."""
    pcb = _pcb(run)
    sch = pcb.with_suffix(".kicad_sch")
    cli(
        "sch",
        "erc",
        "--format",
        "json",
        "--severity-all",
        "-o",
        str(tmp / "erc.json"),
        str(sch),
        nice=True,
        check=False,
    )
    cli(
        "pcb",
        "drc",
        "--format",
        "json",
        "--severity-all",
        "--schematic-parity",
        "-o",
        str(tmp / "drc.json"),
        str(pcb),
        nice=True,
        check=False,
    )
    if not (tmp / "erc.json").exists() or not (tmp / "drc.json").exists():
        return {
            "pass": False,
            "reasons": ["the checks did not run (kicad-cli wrote no report)"],
        }
    erc, drc = _parse_erc(tmp / "erc.json"), _parse_drc(tmp / "drc.json")
    reasons = []
    if erc["errors"]:
        reasons.append(f"ERC: {erc['errors']} error(s) ({', '.join(erc['types'])})")
    if drc["violations"]:
        reasons.append(
            f"DRC: {drc['violations']} violation(s) ({', '.join(drc['types'])})"
        )
    if drc["unconnected"]:
        reasons.append(f"DRC: {drc['unconnected']} unconnected item(s)")
    if drc["parity"]:
        reasons.append(f"DRC: board and schematic differ in {drc['parity']} place(s)")
    return {"pass": not reasons, "reasons": reasons, "erc": erc, "drc": drc}


def _readme(
    run: Path, name: str, chk: dict[str, Any], prt: dict[str, Any], files: list[str]
) -> str:
    lines = [
        f"{prt.get('title') or name}: made with Etch",
        "",
        "Checks run on these exact files when this zip was made (kicad-cli "
        + provenance.kicad_version()
        + "):",
        f"  ERC errors: {chk['erc']['errors']}   DRC violations: {chk['drc']['violations']}   "
        f"unconnected: {chk['drc']['unconnected']}   board vs schematic differences: {chk['drc']['parity']}",
        "",
        "What is inside:",
    ]
    lines += [f"  {f}" for f in files]
    lines += [
        "",
        "Where every part comes from (footprint, symbol and 3D model are KiCad library files):",
    ]
    for r in prt["parts"].values():
        lines.append(f"  {r['ref']}  {r['name']} {r['value']}".rstrip())
        for k in ("source", "model"):
            if r.get(k):
                lines.append(f"      {k}: {r[k]}")
        if r.get("licence"):
            lines.append(f"      licence: {r['licence']}")
    lines += [
        "",
        "Fab: upload gerbers/ as one zip (Gerber RS-274X + Excellon drill) to any PCB house.",
    ]
    return "\n".join(lines) + "\n"


def export(run: Path, force: bool = False) -> dict[str, Any]:
    """force: rebuild the zip even when it is newer than the pcb (export contents changed, e.g. lib tables)."""
    t0 = time.time()
    pcb = _pcb(run)
    name = pcb.stem
    view = run / "view"
    view.mkdir(exist_ok=True)
    zp = view / f"{name}-export.zip"
    if not force and _fresh(zp, pcb) and (view / "export.json").exists():
        return {**json.loads((view / "export.json").read_text()), "cached": True}
    tmp = Path(tempfile.mkdtemp(prefix=".export-", dir=view))
    try:
        chk = check(run, tmp)
        if not chk["pass"]:
            return {
                "ok": False,
                "reasons": chk["reasons"],
                "seconds": round(time.time() - t0, 2),
            }
        g = tmp / "gerbers"
        g.mkdir()
        # only the layers a fab uses (KiCad's default plot also writes a dozen empty User/Eco/Adhesive files)
        inner = re.findall(r'\(\d+ "(In\d+\.Cu)" signal', pcb.read_text())
        fab = [
            "F.Cu",
            *inner,
            "B.Cu",
            "F.Mask",
            "B.Mask",
            "F.Silkscreen",
            "B.Silkscreen",
            "F.Paste",
            "B.Paste",
            "Edge.Cuts",
        ]
        cli(
            "pcb",
            "export",
            "gerbers",
            "-l",
            ",".join(fab),
            "-o",
            str(g) + "/",
            str(pcb),
            nice=True,
        )
        cli("pcb", "export", "drill", "-o", str(g) + "/", str(pcb), nice=True)
        step = tmp / f"{name}.step"
        cli(
            "pcb",
            "export",
            "step",
            "--force",
            "--subst-models",
            "--no-dnp",
            "-o",
            str(step),
            str(pcb),
            nice=True,
            check=False,
        )
        gl = glb(run)
        # the BOM is written fresh from the schematic, so older runs get today's columns (no blank DNP column)
        bom = tmp / f"{name}-bom.csv"
        cli("sch", "export", "bom", "--fields", "Reference,Value,Footprint,QUANTITY", "--labels",
            "Refs,Value,Footprint,Qty", "--exclude-dnp", "--output", str(bom), str(pcb.with_suffix(".kicad_sch")),
            nice=True, check=False)
        if not bom.exists():
            bom = next(run.glob("*-bom.csv"), None)
        prt = parts(run)
        files: list[tuple[Path, str]] = [
            (f, f"gerbers/{f.name}") for f in sorted(g.iterdir())
        ]
        if bom:
            files.append((bom, bom.name))
        if step.exists():
            files.append((step, step.name))
        if gl.get("ok"):
            files.append((Path(gl["glb"]), f"{name}.glb"))
        for ext in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
            f = pcb.with_suffix(ext)
            if f.exists():
                files.append((f, f"kicad/{f.name}"))
        # project library tables, so the project opens with no missing-library warnings on any machine: the
        # build writes this Mac's absolute KiCad paths; the export points them at KiCad's own path variables
        for table in ("fp-lib-table", "sym-lib-table"):
            src = run / table
            if src.exists():
                dst = tmp / table
                dst.write_text(_portable_table(src.read_text()))
                files.append((dst, f"kicad/{table}"))
                for lib, root in VENDOR_LIBS.items():
                    # a vendored manufacturer library (Espressif) travels inside the project, with its licence
                    if str(root) in src.read_text():
                        files += [
                            (f, f"kicad/lib/{lib}/{f.relative_to(root)}")
                            for f in sorted(root.rglob("*"))
                            if f.is_file()
                            and ".git" not in f.relative_to(root).parts
                            and f.suffix.lower() not in (".step", ".wrl")
                        ]
        files = list(dict.fromkeys(files))
        fw = run / "firmware"
        if fw.is_dir():
            files += [
                (f, f"firmware/{f.relative_to(fw)}")
                for f in sorted(fw.rglob("*"))
                if f.is_file()
            ]
        pack = json.loads((run / "design.json").read_text()).get("firmware_pack")
        pdir = PACKS / str(pack) if pack else None
        if pdir and pdir.is_dir() and pdir.parent == PACKS:
            files += [
                (f, f"firmware/arm/{f.relative_to(pdir)}")
                for f in sorted(pdir.rglob("*"))
                if f.is_file() and not f.name.startswith(".")
            ]
        names = [a for _, a in files]
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f, a in files:
                z.write(f, f"{name}/{a}")
            z.writestr(
                f"{name}/README.txt",
                _readme(run, name, chk, prt, names + ["README.txt"]),
            )
            z.writestr(f"{name}/parts.csv", _parts_csv(prt))
        res = {
            "ok": True,
            "zip": str(zp),
            "bytes": zp.stat().st_size,
            "files": len(names) + 2,
            "step": step.exists(),
            "checks": {
                "erc_errors": chk["erc"]["errors"],
                "drc_violations": chk["drc"]["violations"],
                "unconnected": chk["drc"]["unconnected"],
                "parity": chk["drc"]["parity"],
            },
            "seconds": round(time.time() - t0, 2),
        }
        (view / "export.json").write_text(json.dumps(res))
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _portable_table(text: str) -> str:
    """A lib table with this Mac's KiCad install paths swapped for KiCad 10's own path variables, and vendored
    libraries pointed at their copy inside the exported project (kicad/lib/<lib>/)."""
    for lib, root in VENDOR_LIBS.items():
        text = text.replace(str(root), "${KIPRJMOD}/lib/" + lib)
    text = re.sub(
        r'\(uri "[^"]*/SharedSupport/footprints/',
        '(uri "${KICAD10_FOOTPRINT_DIR}/',
        text,
    )
    return re.sub(
        r'\(uri "[^"]*/SharedSupport/symbols/', '(uri "${KICAD10_SYMBOL_DIR}/', text
    )


def _parts_csv(prt: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ref", "part", "value", "footprint", "source", "3d_model", "licence"])
    for r in prt["parts"].values():
        w.writerow(
            [
                r["ref"],
                r["name"],
                r["value"],
                r["footprint"],
                r["source"] or "",
                r["model"] or "",
                r["licence"] or "",
            ]
        )
    return buf.getvalue()


def main() -> None:
    cmd, run = sys.argv[1], Path(sys.argv[2]).resolve()
    fn = {"glb": glb, "parts": parts, "export": export}[cmd]
    try:
        res = export(run, force=True) if cmd == "export" and "--force" in sys.argv[3:] else fn(run)
    except Exception as e:  # one JSON line back to the server, whatever happened
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    print(json.dumps(res, default=str))


if __name__ == "__main__":
    main()
