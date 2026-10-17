"""Download only what each board needs, pinned at the crawled commit, into library/cache/src/<slug>/.

    python3 library/fetch.py [--limit N] [--only slug ...]

Per board: the .kicad_pcb, the sibling .kicad_pro and .kicad_sch files, and repo-local 3D models the pcb
references (${KIPRJMOD}/..., relative paths, or absolute paths whose file name exists in the repo). Model
paths that point into the repo are rewritten to ${KIPRJMOD}/<path relative to the pcb> in the local copy so
kicad-cli finds them; paths into KiCad's own library are left alone.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import posixpath
import re
import urllib.parse
import subprocess
from pathlib import Path

from common import BOARDS_JSON, REPOS_JSON, SRC, load_json, log, save_json

MAX_SCH = 16
MAX_MODEL_BYTES = 8_000_000
MAX_MODELS_BYTES = 40_000_000
# variables KiCad resolves to its own installed library
STD_VARS = re.compile(r"^\$\{?(KISYS3DMOD|KICAD\d*_3DMODEL_DIR|KICAD_3DMODEL_DIR)\}?", re.I)
MODEL_RE = re.compile(r'\(model\s+("(?:[^"\\]|\\.)*"|[^\s()]+)')


def raw_url(owner: str, repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/" + urllib.parse.quote(path)


def download(url: str, dest: Path, tries: int = 3) -> bool:
    """curl (system certificates; python.org builds often lack a CA bundle)."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    p = subprocess.run(["curl", "-fsSL", "--retry", str(tries), "--max-time", "120", "-o", str(tmp), url],
                       capture_output=True, text=True)
    if p.returncode == 0 and tmp.exists():
        tmp.replace(dest)
        return True
    tmp.unlink(missing_ok=True)
    log(f"    download failed {url}: {p.stderr.strip()[:120]}")
    return False


def _norm(p: str) -> str:
    return posixpath.normpath(p).lstrip("./") if p else p


def resolve_model(raw: str, pcb_dir: str, files: dict[str, int], by_name: dict[str, list[str]]) -> str | None:
    """Repo path of the model file a pcb (model ...) path points to, or None (KiCad library / not found)."""
    p = raw.replace("\\", "/")
    if STD_VARS.match(p):
        return None
    m = re.match(r"^\$\{?KIPRJMOD\}?/?(.*)$", p)
    cands = []
    if m:
        cands.append(_norm(posixpath.join(pcb_dir, m.group(1))))
    elif not p.startswith("$") and not re.match(r"^([A-Za-z]:)?/", p):
        cands.append(_norm(posixpath.join(pcb_dir, p)))
    for c in cands:
        if c in files:
            return c
    # last resort: same file name anywhere in the repo (absolute paths from the author's machine, other vars)
    name = p.rsplit("/", 1)[-1].lower()
    hits = by_name.get(name)
    if hits:
        return sorted(hits, key=lambda h: (not h.startswith(pcb_dir), len(h)))[0]
    return None


def step_twin(path: str, files: dict[str, int]) -> str | None:
    """A .step/.stp next to a .wrl with the same stem (kicad-cli --subst-models prefers STEP)."""
    stem = path.rsplit(".", 1)[0]
    for ext in (".step", ".stp", ".STEP", ".STP"):
        if stem + ext in files:
            return stem + ext
    return None


def fetch_board(b: dict, repo: dict) -> dict:
    o, r, sha = b["owner"], b["repo"], b["commit"]
    out = SRC / b["slug"]
    pcb_path = b["pcb_path"]
    pcb_dir = posixpath.dirname(pcb_path)
    files: dict[str, int] = repo.get("files", {})
    local_pcb = out / "board.kicad_pcb"
    orig = out / "orig.kicad_pcb"
    if not download(raw_url(o, r, sha, pcb_path), orig):
        return {"ok": False, "error": "pcb download failed"}
    if orig.read_bytes()[:40].startswith(b"version https://git-lfs"):
        orig.unlink()
        if not download(f"https://media.githubusercontent.com/media/{o}/{r}/{sha}/" + urllib.parse.quote(pcb_path), orig):
            return {"ok": False, "error": "pcb is a Git LFS pointer and the LFS download failed"}
    head = orig.read_bytes()[:200].lstrip()
    if not head.startswith(b"(kicad_pcb"):
        return {"ok": False, "error": "not an s-expression .kicad_pcb"}

    stem = posixpath.basename(pcb_path)[: -len(".kicad_pcb")]
    # sibling project + schematics (for symbol descriptions); the project file must share the pcb's stem
    sib = [p for p in files if posixpath.dirname(p) == pcb_dir]
    pro = posixpath.join(pcb_dir, stem + ".kicad_pro")
    if pro in files:
        download(raw_url(o, r, sha, pro), out / "board.kicad_pro")
    schs = sorted((p for p in sib if p.endswith(".kicad_sch")), key=lambda p: (not p.endswith(stem + ".kicad_sch"), p))
    for p in schs[:MAX_SCH]:
        download(raw_url(o, r, sha, p), out / "sch" / posixpath.basename(p))

    text = orig.read_text(errors="replace")
    by_name: dict[str, list[str]] = {}
    for p in files:
        if re.search(r"\.(step|stp|wrl|vrml)$", p, re.I):
            by_name.setdefault(p.rsplit("/", 1)[-1].lower(), []).append(p)
    rewrites: dict[str, str] = {}
    got, total, missing = 0, 0, 0
    for m in MODEL_RE.finditer(text):
        token = m.group(1)
        raw = token[1:-1] if token.startswith('"') else token
        if raw in rewrites:
            continue
        rp = resolve_model(raw, pcb_dir, files, by_name)
        if rp is None:
            if not STD_VARS.match(raw.replace("\\", "/")):
                missing += 1
            continue
        twin = step_twin(rp, files) if rp.lower().endswith((".wrl", ".vrml")) else None
        use = twin or rp
        size = files.get(use, 0)
        if size > MAX_MODEL_BYTES or total + size > MAX_MODELS_BYTES:
            continue
        dest = out / "models" / use
        ok = download(raw_url(o, r, sha, use), dest)
        if ok and dest.read_bytes()[:40].startswith(b"version https://git-lfs"):
            dest.unlink()  # Git LFS pointer: the real file lives on media.githubusercontent.com
            ok = download(f"https://media.githubusercontent.com/media/{o}/{r}/{sha}/" + urllib.parse.quote(use), dest)
        if ok:
            total += size
            got += 1
            rewrites[raw] = "${KIPRJMOD}/models/" + use
    if rewrites:
        def sub(m: re.Match) -> str:
            token = m.group(1)
            raw = token[1:-1] if token.startswith('"') else token
            return f'(model "{rewrites[raw]}"' if raw in rewrites else m.group(0)
        text = MODEL_RE.sub(sub, text)
    local_pcb.write_text(text)
    return {"ok": True, "models": got, "model_bytes": total, "unresolved_models": missing, "schematics": min(len(schs), MAX_SCH)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    boards = load_json(BOARDS_JSON, [])
    repos = {r["full_name"]: r for r in load_json(REPOS_JSON, [])}
    if args.only:
        boards = [b for b in boards if b["slug"] in args.only]
    if args.limit:
        boards = boards[: args.limit]

    def job(b: dict) -> tuple[dict, dict]:
        return b, fetch_board(b, repos.get(f"{b['owner']}/{b['repo']}", {}))

    with cf.ThreadPoolExecutor(args.workers) as ex:
        for b, res in ex.map(job, boards):
            b["fetch"] = res
            log(f"{b['slug']:60s} {'ok' if res['ok'] else 'FAIL'} "
                + (f"models {res['models']} ({res['model_bytes'] // 1024} KB), unresolved {res['unresolved_models']}, "
                   f"sch {res['schematics']}" if res["ok"] else res["error"]))
    allb = {b["slug"]: b for b in load_json(BOARDS_JSON, [])}
    for b in boards:
        allb[b["slug"]] = b
    save_json(BOARDS_JSON, list(allb.values()))


if __name__ == "__main__":
    main()
