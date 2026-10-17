"""Read symbols out of KiCad's installed .kicad_sym libraries, flattening `extends`."""

from __future__ import annotations

import copy
import functools
from dataclasses import dataclass

from . import sexpr
from .kicad import symbol_lib_path


@dataclass
class Pin:
    number: str
    name: str
    etype: str
    x: float
    y: float
    angle: float
    unit: int = 1  # 0 = common to every unit (graphics only in practice)


@functools.lru_cache(maxsize=64)
def _lib_text(lib: str) -> str:
    path = symbol_lib_path(lib)
    if not path.exists():
        raise KeyError(f"symbol library {lib} not installed")
    return path.read_text()


def _raw(lib: str, name: str) -> list | None:
    block = sexpr.extract_block(_lib_text(lib), f'\n\t(symbol "{name}"\n')
    if block is None:
        return None
    return sexpr.parse(block.strip())


@functools.lru_cache(maxsize=256)
def load(lib_id: str) -> list:
    """Return the flattened symbol node named with its full lib_id, ready for lib_symbols."""
    lib, name = lib_id.split(":", 1)
    node = _raw(lib, name)
    if node is None:
        raise KeyError(f"symbol {lib_id} not found")
    ext = sexpr.find(node, "extends")
    if ext is not None:
        parent = copy.deepcopy(load(f"{lib}:{ext[1]}"))
        pname = ext[1]
        child_props = {p[1]: p for p in sexpr.find_all(node, "property")}
        flat = [parent[0], sexpr.Q(name)]
        for c in parent[2:]:
            if isinstance(c, list) and c and c[0] == "property" and c[1] in child_props:
                flat.append(child_props.pop(c[1]))
            elif isinstance(c, list) and c and c[0] == "symbol":
                sub = copy.deepcopy(c)
                sub[1] = sexpr.Q(str(sub[1]).replace(f"{lib}:{pname}", name, 1).replace(pname, name, 1))
                flat.append(sub)
            else:
                flat.append(c)
        # child-only properties go before the unit sub-symbols
        idx = next(i for i, c in enumerate(flat) if isinstance(c, list) and c and c[0] == "symbol")
        for p in child_props.values():
            flat.insert(idx, p)
            idx += 1
        node = flat
    node = copy.deepcopy(node)
    node[1] = sexpr.Q(lib_id)
    return node


def pins(lib_id: str) -> list[Pin]:
    node = load(lib_id)
    out: list[Pin] = []
    for sub in sexpr.find_all(node, "symbol"):
        # sub-symbol names end in _<unit>_<style>; multi-unit parts (74xx gates) have units 1..n
        try:
            unit = int(str(sub[1]).rsplit("_", 2)[-2])
        except (ValueError, IndexError):
            unit = 1
        for p in sexpr.find_all(sub, "pin"):
            at = sexpr.find(p, "at")
            num = sexpr.find(p, "number")
            nm = sexpr.find(p, "name")
            out.append(
                Pin(
                    str(num[1]),
                    str(nm[1]) if nm else "",
                    str(p[1]),
                    float(at[1]),
                    float(at[2]),
                    float(at[3]) if len(at) > 3 else 0.0,
                    unit,
                )
            )
    return out


def exists(lib_id: str) -> bool:
    try:
        lib, name = lib_id.split(":", 1)
        return _raw(lib, name) is not None
    except KeyError:
        return False
