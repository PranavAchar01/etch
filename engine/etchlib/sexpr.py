"""Minimal KiCad S-expression reader and writer.

Kept Python 3.9 compatible because kicad_side/ imports it under KiCad's bundled Python.
A node is a list: [head, child, child, ...]. Strings that were quoted in the source are
wrapped in Q so the writer can quote them again.
"""

import re


class Q(str):
    """A string that was quoted in the source file."""


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.DOTALL)


def parse(text):
    stack = [[]]
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"bad s-expression near offset {pos}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            node = stack.pop()
            stack[-1].append(node)
        elif m.group(3) is not None:
            stack[-1].append(Q(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            stack[-1].append(m.group(4))
    if len(stack) != 1:
        raise ValueError("unbalanced s-expression")
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def _atom(x):
    if isinstance(x, Q):
        return '"' + x.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(x, float):
        s = (f"{x:.4f}").rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    return str(x)


def dump(node, indent=0):
    """Serialise in KiCad's tab-indented style."""
    if not isinstance(node, list):
        return _atom(node)
    pad = "\t" * indent
    simple = all(not isinstance(c, list) for c in node)
    if simple:
        return pad + "(" + " ".join(_atom(c) for c in node) + ")"
    parts = []
    head = []
    for c in node:
        if isinstance(c, list):
            break
        head.append(_atom(c))
    out = pad + "(" + " ".join(head)
    for c in node[len(head) :]:
        if isinstance(c, list):
            parts.append(dump(c, indent + 1))
        else:
            parts.append("\t" * (indent + 1) + _atom(c))
    return out + "\n" + "\n".join(parts) + "\n" + pad + ")"


def find(node, head):
    for c in node:
        if isinstance(c, list) and c and c[0] == head:
            return c
    return None


def find_all(node, head):
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def extract_block(text, opener):
    """Return the substring of the balanced block starting with `opener` (string-aware)."""
    i = text.find(opener)
    if i < 0:
        return None
    depth = 0
    j = i
    in_str = False
    n = len(text)
    while j < n:
        ch = text[j]
        if in_str:
            if ch == "\\":
                j += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[i : j + 1]
        j += 1
    return None
