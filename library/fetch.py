"""Download what each board needs, pinned at the crawled commit, and resolve every 3D model it uses.

    python3 library/fetch.py [--limit N] [--only slug ...] [--workers 8]

Per board, into library/cache/src/<slug>/:
  orig.kicad_pcb      the file as committed
  board.kicad_pcb     the same file with every resolvable (model ...) path rewritten to an absolute path
  board.kicad_pro     sibling project, sch/*.kicad_sch sibling schematics (symbol descriptions)
  models.json         {total, resolved, hidden, missing: [{ref, path}]}

Model files from the repo are downloaded once per (repo, commit, path) into library/cache/blobs/. A model
path is resolved, in order: KiCad's stock variables -> the installed KiCad 3dmodels library; ${KIPRJMOD}
and plain relative paths -> the repo tree (then the installed library, for KiCad 4/5 implicit paths);
then for anything else (absolute paths from the author's machine, custom variables such as
${KICAD_USER_3DMOD} or ${MYLIB}): the longest matching path suffix in the repo tree, then in the installed
library, then the same file name in the repo, then in the installed library. A .wrl whose .step twin
exists resolves to the .step (what kicad-cli --subst-models does). Offset/scale/rotate are untouched, and
hidden models stay hidden. Nothing is invented: an unresolved model is recorded as missing.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import posixpath
import re
import subprocess
import threading
import urllib.parse
from pathlib import Path

from common import (BOARDS_JSON, CACHE, REPOS_JSON, SRC, child, children, kicad_cli, load_json, log, parse_sexpr,
                    prop, save_json)

BLOBS = CACHE / "blobs"
MAX_SCH = 16
MAX_MODEL_BYTES = 60_000_000
MAX_MODELS_BYTES = 250_000_000
MODEL_EXT = re.compile(r"\.(step|stp|stpz|wrl|wrz|vrml)$", re.I)
STOCK_VARS = re.compile(r"^(KISYS3DMOD|KICAD\d*_3DMODEL_DIR|KICAD_3DMODEL_DIR)$", re.I)
MODEL_RE = re.compile(r'\(model\s+("(?:[^"\\]|\\.)*"|[^\s()]+)')
VAR_RE = re.compile(r"^(?:\$[{(]([A-Za-z0-9_]+)[})]|:([A-Za-z0-9_]+):)/?(.*)$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


# ---------------------------------------------------------------- downloads

def download(url: str, dest: Path, tries: int = 3, quiet: bool = False) -> bool:
    """curl (system certificates; python.org builds often lack a CA bundle). Atomic via .part + rename."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".part{threading.get_ident()}")
    p = subprocess.run(["curl", "-fsSL", "--retry", str(0 if quiet else tries), "--max-time", "600", "-o", str(tmp), url],
                       capture_output=True, text=True)
    if p.returncode == 0 and tmp.exists():
        tmp.replace(dest)
        return True
    tmp.unlink(missing_ok=True)
    if not quiet:
        log(f"    download failed {url}: {p.stderr.strip()[:120]}")
    return False


def raw_url(owner: str, repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/" + urllib.parse.quote(path)


def fetch_repo_file(owner: str, repo: str, sha: str, path: str) -> Path | None:
    """Repo file at a commit, cached in blobs/<owner>__<repo>/<sha>/<path>; follows Git LFS pointers."""
    dest = BLOBS / f"{owner}__{repo}".lower() / sha / path
    with _locks_guard:
        lock = _locks.setdefault(str(dest), threading.Lock())
    with lock:
        if not download(raw_url(owner, repo, sha, path), dest):
            return None
        with dest.open("rb") as f:
            head = f.read(64)
        if head.startswith(b"version https://git-lfs"):
            dest.unlink()
            if not download(f"https://media.githubusercontent.com/media/{owner}/{repo}/{sha}/" + urllib.parse.quote(path),
                            dest):
                return None
    return dest


# ---------------------------------------------------------------- installed KiCad model library

_LIB: dict | None = None


def kicad_models() -> dict:
    """Index of the installed 3dmodels library: root, relative paths, and lower-case file name -> paths."""
    global _LIB
    if _LIB is None:
        cli = kicad_cli()
        root = cli.parent.parent / "SharedSupport" / "3dmodels" if cli else None
        rel: set[str] = set()
        names: dict[str, list[str]] = {}
        if root and root.is_dir():
            for dp, _, fs in os.walk(root):
                for f in fs:
                    if MODEL_EXT.search(f):
                        r = os.path.relpath(os.path.join(dp, f), root)
                        rel.add(r)
                        names.setdefault(f.lower(), []).append(r)
        _LIB = {"root": root, "rel": rel, "names": names, "lower": {r.lower(): r for r in rel}}
    return _LIB


# ---------------------------------------------------------------- official KiCad 3D library (older versions)

OFFICIAL = CACHE / "kicadlib"
# (label, git url, tag, path prefix inside the repo). Blobless shallow clones: the file list is local, and only
# the model files a board uses are downloaded (git show fetches the blob on demand).
GITLAB_3D = "https://gitlab.com/kicad/libraries/kicad-packages3D.git"
LIB_REPOS = {
    **{t: (GITLAB_3D, t, "") for t in ("5.1.12", "6.0.11", "7.0.11", "8.0.9", "9.0.9", "10.0.7")},
    "4.0.7": ("https://github.com/KiCad/kicad-library.git", "4.0.7", "modules/packages3d/"),
}
TAG_FOR = {"6": "6.0.11", "7": "7.0.11", "8": "8.0.9", "9": "9.0.9", "10": "10.0.7"}
_lib_lock = threading.Lock()
_lib_files: dict[str, set[str]] = {}


def _official_sources(var: str | None) -> list[str]:
    """Library versions to try, in order, for a stock-library path written with this variable."""
    m = re.match(r"^KICAD(\d+)_3DMODEL_DIR$", var or "", re.I)
    if m and m.group(1) in TAG_FOR:
        return [TAG_FOR[m.group(1)]]
    # KISYS3DMOD / no variable: KiCad 5, then the KiCad 4 library (old names such as Capacitors_SMD.3dshapes)
    return ["5.1.12", "4.0.7"]


def _lib_checkout(label: str) -> tuple[Path, set[str]]:
    url, tag, prefix = LIB_REPOS[label]
    repo = OFFICIAL / "git" / label
    with _lib_lock:
        if label not in _lib_files:
            if not (repo / ".git").exists():
                repo.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", "--depth", "1",
                                "--branch", tag, url, str(repo)], check=True, capture_output=True)
            out = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", "HEAD"],
                                 capture_output=True, text=True, check=True).stdout
            _lib_files[label] = {f[len(prefix):] for f in out.splitlines() if f.startswith(prefix)}
    return repo, _lib_files[label]


def official_model(var: str | None, rel: str) -> Path | None:
    """The model from the official KiCad 3D library at the version the variable names, cached locally."""
    rel = posixpath.normpath(rel).lstrip("/")
    for label in _official_sources(var):
        try:
            repo, names = _lib_checkout(label)
        except subprocess.CalledProcessError as e:
            log(f"    cannot clone KiCad library {label}: {e.stderr[-200:] if e.stderr else e}")
            continue
        for t in _twins(rel):
            if t not in names:
                continue
            dest = OFFICIAL / label / t
            if dest.exists() and dest.stat().st_size:
                return dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + f".part{threading.get_ident()}")
            with tmp.open("wb") as f:
                p = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{LIB_REPOS[label][2]}{t}"], stdout=f,
                                   stderr=subprocess.PIPE)
            if p.returncode == 0 and tmp.stat().st_size:
                tmp.replace(dest)
                return dest
            tmp.unlink(missing_ok=True)
            log(f"    git show failed for {label}:{t}: {p.stderr[-150:]!r}")
    return None


# ---------------------------------------------------------------- the author's own published KiCad library

# owner (lower case) -> their KiCad library repo, searched by file name for models their boards reference
# through a personal library path (e.g. Olimex boards use ${KISYS3DMOD}/LED_0603_B.wrl from OLIMEX/KiCAD)
AUTHOR_LIBS = {"olimex": "OLIMEX/KiCAD"}
_author_lock = threading.Lock()
_author: dict[str, dict] = {}


def author_lib(owner: str) -> dict | None:
    full = AUTHOR_LIBS.get(owner.lower())
    if not full:
        return None
    with _author_lock:
        if full not in _author:
            cache = CACHE / "authorlibs" / (full.replace("/", "__") + ".json")
            data = load_json(cache, None)
            if not data:
                from common import gh
                br = gh(f"repos/{full}/commits/HEAD")
                tree = gh(f"repos/{full}/git/trees/{br['sha']}?recursive=1") if br else None
                names: dict[str, list[str]] = {}
                for e in (tree or {}).get("tree", []):
                    if e.get("type") == "blob" and MODEL_EXT.search(e["path"]):
                        names.setdefault(e["path"].rsplit("/", 1)[-1].lower(), []).append(e["path"])
                data = {"repo": full, "commit": br["sha"] if br else None, "names": names}
                save_json(cache, data, indent=None)
            _author[full] = data
    return _author[full]


# ---------------------------------------------------------------- model resolution

def _twins(path: str) -> list[str]:
    """Same model, preferred order: .step twin first for .wrl (kicad-cli --subst-models does the same)."""
    stem, _, ext = path.rpartition(".")
    if ext.lower() in ("wrl", "vrml", "wrz"):
        return [stem + ".step", stem + ".stp", stem + ".STEP", stem + ".STP", stem + ".stpZ", path]
    if ext.lower() == "stpz":
        return [path, stem + ".step"]
    return [path]


class Resolver:
    def __init__(self, pcb_dir: str, files: dict[str, int], owner: str = ""):
        self.pcb_dir = pcb_dir
        self.author = author_lib(owner) if owner else None
        self.files = {p: s for p, s in files.items() if MODEL_EXT.search(p)}
        self.lower = {p.lower(): p for p in self.files}
        self.names: dict[str, list[str]] = {}
        for p in self.files:
            self.names.setdefault(p.rsplit("/", 1)[-1].lower(), []).append(p)
        self.lib = kicad_models()

    def _repo(self, rel: str) -> str | None:
        rel = posixpath.normpath(rel)
        if rel.startswith("../") or rel == "..":
            return None
        for t in _twins(rel):
            if t in self.files:
                return t
            if t.lower() in self.lower:
                return self.lower[t.lower()]
        return None

    def _lib(self, rel: str) -> str | None:
        for t in _twins(posixpath.normpath(rel)):
            if t in self.lib["rel"]:
                return t
            if t.lower() in self.lib["lower"]:
                return self.lib["lower"][t.lower()]
        return None

    def _suffix(self, segs: list[str]) -> tuple[str, str] | None:
        """Longest trailing path match (>= 2 segments) in the repo, then in the installed library."""
        for k in range(len(segs), 1, -1):
            tail = "/".join(segs[-k:])
            for src, cands in (("repo", self.files), ("lib", self.lib["rel"])):
                for t in _twins(tail):
                    tl = "/" + t.lower()
                    hits = [p for p in cands if ("/" + p.lower()).endswith(tl)]
                    if hits:
                        hits.sort(key=lambda h: (not h.startswith(self.pcb_dir), len(h)))
                        return src, hits[0]
        return None

    def _name(self, name: str) -> tuple[str, str] | None:
        """Same file name: repo, then the author's own library, then the installed KiCad library. Twins are
        tried in order (.step before .wrl) across the repo and the author's library, because kicad-cli loads
        STEP reliably and some VRML files only open in KiCad's interactive viewer."""
        a = self.author["names"] if self.author and self.author.get("commit") else {}
        for t in _twins(name):
            hits = self.names.get(t.lower())
            if hits:
                return "repo", sorted(hits, key=lambda h: (not h.startswith(self.pcb_dir), len(h)))[0]
            hits = a.get(t.lower())
            if hits:  # current library first, archived releases ("Old/...") last
                return "author", sorted(hits, key=lambda h: (h.lower().startswith("old/"), len(h)))[0]
        for t in _twins(name):
            hits = self.lib["names"].get(t.lower())
            if hits:
                return "lib", sorted(hits, key=len)[0]
        return None

    def resolve(self, raw: str) -> tuple[str, str] | None:
        """-> ("repo", repo path) | ("lib", path in the installed 3dmodels) | ("official", local file) |
        ("absent", why) when the path names KiCad's own library and that library never had the model |
        None (not found anywhere)."""
        p = raw.replace("\\", "/").strip()
        m = VAR_RE.match(p)
        var, rest = ((m.group(1) or m.group(2)), m.group(3)) if m else (None, p)
        stock = bool(var and STOCK_VARS.match(var))
        bare = not var and not re.match(r"^([A-Za-z]:)?/", p)
        if stock:
            hit = self._lib(rest)
            if hit:
                return "lib", hit
        elif var == "KIPRJMOD":
            hit = self._repo(posixpath.join(self.pcb_dir, rest))
            if hit:
                return "repo", hit
        elif bare:
            hit = self._repo(posixpath.join(self.pcb_dir, p))
            if hit:
                return "repo", hit
            hit = self._lib(p)  # KiCad 4/5: a bare "Lib.3dshapes/x.wrl" is relative to KISYS3DMOD
            if hit:
                return "lib", hit
        if stock or bare:
            f = official_model(var if stock else None, rest)
            if f:
                return "official", str(f)
        segs = [s for s in re.sub(r"^([A-Za-z]:)", "", rest).split("/") if s and s not in (".", "..")]
        hit = (self._suffix(segs) or self._name(segs[-1])) if segs else None
        if hit:
            return hit
        if stock and self._official_dir(var, rest):
            return "absent", "not in the KiCad 3D library at the version the board names"
        return None

    @staticmethod
    def _official_dir(var: str | None, rest: str) -> bool:
        """True when the path's folder (e.g. Connector_USB.3dshapes) is a folder of the official library."""
        d = posixpath.dirname(posixpath.normpath(rest))
        if not d:
            return False
        for label in _official_sources(var):
            try:
                _, names = _lib_checkout(label)
            except subprocess.CalledProcessError:
                continue
            if any(n.startswith(d + "/") for n in names):
                return True
        return False


def model_entries(tree: list) -> list[dict]:
    out = []
    for fp in children(tree, "footprint") + children(tree, "module"):
        ref = prop(fp, "Reference")
        if not ref:
            for t in children(fp, "fp_text"):
                if len(t) > 2 and t[1] == "reference":
                    ref = t[2]
        for m in children(fp, "model"):
            if len(m) < 2 or not isinstance(m[1], str):
                continue
            hidden = "hide" in m[2:] or bool((child(m, "hide") or [None, None])[1] == "yes")
            out.append({"ref": ref or "?", "path": m[1], "hidden": hidden})
    return out


def _quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fetch_board(b: dict, repo: dict) -> dict:
    o, r, sha = b["owner"], b["repo"], b["commit"]
    out = SRC / b["slug"]
    out.mkdir(parents=True, exist_ok=True)
    pcb_path = b["pcb_path"]
    pcb_dir = posixpath.dirname(pcb_path)
    files: dict[str, int] = repo.get("files", {})

    orig_blob = fetch_repo_file(o, r, sha, pcb_path)
    if not orig_blob:
        return {"ok": False, "error": "pcb download failed"}
    text = orig_blob.read_text(errors="replace")
    if not text.lstrip().startswith("(kicad_pcb"):
        return {"ok": False, "error": "not an s-expression .kicad_pcb (KiCad 3 or older)"}
    (out / "orig.kicad_pcb").write_text(text)

    stem = posixpath.basename(pcb_path)[: -len(".kicad_pcb")]
    pro = posixpath.join(pcb_dir, stem + ".kicad_pro")
    if pro in files:
        f = fetch_repo_file(o, r, sha, pro)
        if f:
            (out / "board.kicad_pro").write_bytes(f.read_bytes())
    schs = sorted((p for p in files if posixpath.dirname(p) == pcb_dir and p.endswith(".kicad_sch")),
                  key=lambda p: (not p.endswith(stem + ".kicad_sch"), p))
    for p in schs[:MAX_SCH]:
        f = fetch_repo_file(o, r, sha, p)
        if f:
            (out / "sch").mkdir(exist_ok=True)
            (out / "sch" / posixpath.basename(p)).write_bytes(f.read_bytes())

    try:
        entries = model_entries(parse_sexpr(text))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"parse: {e}"}
    res = Resolver(pcb_dir, files, o)
    lib_root = kicad_models()["root"]
    abs_for: dict[str, str | None] = {}
    absent: set[str] = set()
    total_bytes = 0
    for raw in dict.fromkeys(e["path"] for e in entries):
        hit = res.resolve(raw)
        if not hit:
            abs_for[raw] = None
            continue
        src, rel = hit
        if src == "absent":
            abs_for[raw] = None
            absent.add(raw)
            continue
        if src == "lib":
            abs_for[raw] = str(lib_root / rel)
            continue
        if src == "official":
            abs_for[raw] = rel
            continue
        if src == "author":
            f = fetch_repo_file(*res.author["repo"].split("/"), res.author["commit"], rel)
            abs_for[raw] = str(f) if f else None
            continue
        size = files.get(rel, 0)
        if size > MAX_MODEL_BYTES or total_bytes + size > MAX_MODELS_BYTES:
            log(f"    {b['slug']}: model too large, not fetched: {rel} ({size / 1e6:.0f} MB)")
            abs_for[raw] = None
            continue
        f = fetch_repo_file(o, r, sha, rel)
        abs_for[raw] = str(f) if f else None
        total_bytes += size if f else 0

    def sub(m: re.Match) -> str:
        token = m.group(1)
        raw = token[1:-1].replace('\\"', '"').replace("\\\\", "\\") if token.startswith('"') else token
        a = abs_for.get(raw)
        return f"(model {_quote(a)}" if a else m.group(0)

    tmp = out / "board.kicad_pcb.tmp"
    tmp.write_text(MODEL_RE.sub(sub, text))
    tmp.replace(out / "board.kicad_pcb")  # render.py may be reading the previous version
    shown = [e for e in entries if not e["hidden"]]
    missing = [{"ref": e["ref"], "path": e["path"],
                "reason": "the KiCad library has no such model (KiCad shows none either)" if e["path"] in absent
                else "model file not in the repo or the KiCad library"}
               for e in shown if not abs_for.get(e["path"])]
    # identical = every model the author's KiCad would draw is drawn. A stock-library path that the KiCad
    # library never shipped draws nothing in KiCad either, so it does not break identity.
    summary = {"total": len(shown), "resolved": len(shown) - len(missing), "hidden": len(entries) - len(shown),
               "missing": missing, "identical": all(e["path"] in absent for e in missing),
               "repo_model_bytes": total_bytes}
    save_json(out / "models.json", summary)
    return {"ok": True, **{k: summary[k] for k in ("total", "resolved", "hidden")}, "missing": len(missing),
            "identical": summary["identical"], "schematics": min(len(schs), MAX_SCH)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    boards = load_json(BOARDS_JSON, [])
    repos = {r["full_name"]: r for r in load_json(REPOS_JSON, [])}
    if args.only:
        boards = [b for b in boards if b["slug"] in args.only]
    if args.limit:
        boards = boards[: args.limit]
    kicad_models()

    def job(b: dict) -> tuple[dict, dict]:
        try:
            return b, fetch_board(b, repos.get(f"{b['owner']}/{b['repo']}", {}))
        except Exception as e:  # noqa: BLE001
            return b, {"ok": False, "error": f"crash: {e}"}

    results = {}
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for b, res in ex.map(job, boards):
            results[b["slug"]] = res
            log(f"{b['slug']:60s} " + (f"ok models {res['resolved']}/{res['total']} (hidden {res['hidden']}), "
                                       f"sch {res['schematics']}" if res["ok"] else "FAIL " + res["error"]))
    allb = load_json(BOARDS_JSON, [])
    for b in allb:
        if b["slug"] in results:
            b["fetch"] = results[b["slug"]]
    save_json(BOARDS_JSON, allb)
    ok = [r for r in results.values() if r["ok"]]
    log(f"fetched {len(ok)}/{len(results)}; identical: {sum(1 for r in ok if r['identical'])}, "
        f"all models resolved: {sum(1 for r in ok if not r['missing'])}")


if __name__ == "__main__":
    main()
