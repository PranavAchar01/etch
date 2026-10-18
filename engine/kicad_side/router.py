"""Two-layer grid router (A* with turn and via costs) for small boards.

Pure Python 3.9 so it can run inside KiCad's bundled interpreter next to pcbnew.
Everything is in mm, origin at the board's top-left corner, y down.

Clearance model: every copper item of net m blocks, for other nets, every grid cell whose
centre is closer than (clearance + track/2 + own half size + margin) to it. The margin
covers the worst case of a diagonal move between two cell centres. KiCad DRC is the judge;
this only has to be conservative.
"""

import heapq
import math
import time

FREE = -1
BLOCKED = -2
LAYERS = ("F.Cu", "B.Cu")
WIDE_MAX_EXPAND = 40000
# A* expansions per connection. A net that runs out of them on the first attempt is retried with RETRY_MAX_EXPAND:
# the RGB + Pico board (live-1792255105003) had GPIO15 need ~0.5-0.8 M expansions to leave the Pico's pin row, and
# four identical 400 k attempts all failed it (QA 10-17).
MAX_EXPAND = 400000
RETRY_MAX_EXPAND = 1600000
# rip-up ring (mm, times the attempt number) around a failed net's pads: other nets with copper inside it are
# ripped and routed again, the rest of the best attempt's copper is kept (the keyboard re-routed all 80 nets on
# every attempt before)
RIP_MARGIN_MM = 3.0
# a conservative A* rate for this pure Python search, used to cap one search by the budget time left
EXPANSIONS_PER_S = 25000
# bucket size (mm) of the via check's spatial index. The check used to scan every pad and track on the board for
# every candidate via: 2/3 of the routing time on the 61-key keyboard (122 parts, 6 M distance calls per 5 nets)
VBIN = 1.0
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def _rect_dist(px, py, r):
    dx = max(r[0] - px, 0.0, px - r[2])
    dy = max(r[1] - py, 0.0, py - r[3])
    return math.hypot(dx, dy)


def _seg_dist(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * vx + (py - y1) * vy) / L2))
    return math.hypot(px - (x1 + t * vx), py - (y1 + t * vy))


class _DSU:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


class Router:
    def __init__(self, w, h, rules, pads, pitch=0.2, keepouts=()):
        self.w, self.h, self.g = float(w), float(h), float(pitch)
        self.nx = int(round(self.w / self.g)) + 1
        self.ny = int(round(self.h / self.g)) + 1
        self.track = float(rules.get("track", 0.3))
        self.clr = float(rules.get("clearance", 0.2))
        self.via_d = float(rules.get("via_d", 0.6))
        self.via_drill = float(rules.get("via_drill", 0.3))
        self.edge = float(rules.get("edge_clearance", 0.5))
        self.via_cost = float(rules.get("via_cost", 8.0))
        # power nets get wider copper (rules.power_nets / rules.power_track); the search then also
        # needs the neighbouring cells clear, since the clearance grid assumes a signal-width track
        self.power_nets = set(rules.get("power_nets", []))
        self.power_track = float(rules.get("power_track", self.track))
        self._dil = 0
        # high-voltage nets (rules.hv_nets, IPC-2221 spacing in rules.hv_clearance). Clearance is pairwise, so
        # three ownership grids: "own" (HV copper claims hv_clr, the rest clr) is what other nets search, "own_hv"
        # (everything claims hv_clr) is what an HV net searches, and "own_lo" (everything at clr) is used inside
        # the pin zone of a part that has an HV pin next to other pins (pin_zones): there the part's pitch, not
        # the board, sets the gap, and the tracks leaving those pins cannot do better
        self.hv_clr = max(self.clr, float(rules.get("hv_clearance", self.clr)))
        self.hv_names = set(rules.get("hv_nets", [])) if self.hv_clr > self.clr else set()
        self.margin = self.g * 0.75
        self.pads = pads  # dicts: ref, num, net, x, y, rect, layers
        self.nets = sorted({p["net"] for p in pads if p["net"]})
        self.net_id = {n: i for i, n in enumerate(self.nets)}
        self.hv_ids = {self.net_id[n] for n in self.hv_names if n in self.net_id}
        size = self.nx * self.ny
        self.own = [[FREE] * size for _ in LAYERS]
        self.own_hv = [[FREE] * size for _ in LAYERS] if self.hv_ids else None
        self.own_lo = [[FREE] * size for _ in LAYERS] if self.hv_ids else None
        self.pin_zone = set()
        for box in pin_zones(pads, self.hv_names).values():
            for ix, iy in self._cells_near(*box, 0):
                x, y = self.cell_xy(ix, iy)
                if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                    self.pin_zone.add(self.idx(ix, iy))
        self.padown = [[FREE] * size for _ in LAYERS]
        self.tracks = []  # dicts: net, layer, width, pts
        self.vias = []  # dicts: net, x, y, d, drill
        self.items = []  # (net_id, layer_idx_set, kind, geom) for via checks
        self._vbins = {}  # VBIN mm buckets -> items whose bounding box touches them (via_ok looks only nearby)
        self._via_cache = {}
        self._block_edges()
        for p in pads:
            self._add_pad(p)
        self.no_via = set()
        for k in keepouts:
            self._add_keepout(k)

    # grid helpers
    def idx(self, ix, iy):
        return iy * self.nx + ix

    def cell_xy(self, ix, iy):
        return ix * self.g, iy * self.g

    def nearest(self, x, y):
        return (min(self.nx - 1, max(0, int(round(x / self.g)))), min(self.ny - 1, max(0, int(round(y / self.g)))))

    def _cells_near(self, x0, y0, x1, y1, reach):
        ix0 = max(0, int(math.floor((x0 - reach) / self.g)))
        iy0 = max(0, int(math.floor((y0 - reach) / self.g)))
        ix1 = min(self.nx - 1, int(math.ceil((x1 + reach) / self.g)))
        iy1 = min(self.ny - 1, int(math.ceil((y1 + reach) / self.g)))
        for iy in range(iy0, iy1 + 1):
            for ix in range(ix0, ix1 + 1):
                yield ix, iy

    def _clr(self, net, other=None):
        return self.hv_clr if net in self.hv_ids or other in self.hv_ids else self.clr

    def _grids(self):
        return [g for g in (self.own, self.own_hv, self.own_lo) if g is not None]

    def _claim(self, layer, i, net, grid=None):
        g = self.own if grid is None else grid
        cur = g[layer][i]
        if cur == FREE:
            g[layer][i] = net
        elif cur != net:
            g[layer][i] = BLOCKED

    def _claim_at(self, layers, i, net, d, extra):
        """Claim cell i (copper edge d mm away) for net in every grid whose clearance reaches it."""
        for L in layers:
            if d < self._clr(net) + extra:
                self._claim(L, i, net)
            if self.own_hv is not None:
                if d < self.hv_clr + extra:
                    self._claim(L, i, net, self.own_hv)
                if d < self.clr + extra:
                    self._claim(L, i, net, self.own_lo)

    def _block_edges(self):
        reach = self.edge + self.track / 2 + self.margin
        for iy in range(self.ny):
            for ix in range(self.nx):
                x, y = self.cell_xy(ix, iy)
                if x < reach or y < reach or x > self.w - reach or y > self.h - reach:
                    for g in self._grids():
                        for L in range(2):
                            g[L][self.idx(ix, iy)] = BLOCKED

    def _add_keepout(self, k):
        """A footprint rule area (e.g. a module's antenna): no tracks and/or vias inside it on its layers."""
        pts = k["pts"]
        edges = list(zip(pts, pts[1:] + pts[:1]))
        reach = self.clr + max(self.track / 2, self.via_d / 2) + self.margin
        layers = [LAYERS.index(L) for L in k["layers"] if L in LAYERS]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        for ix, iy in self._cells_near(min(xs), min(ys), max(xs), max(ys), reach):
            x, y = self.cell_xy(ix, iy)
            inside = False
            for (x1, y1), (x2, y2) in edges:
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
            if not inside and min(_seg_dist(x, y, x1, y1, x2, y2) for (x1, y1), (x2, y2) in edges) >= reach:
                continue
            i = self.idx(ix, iy)
            if k.get("tracks", True):
                for g in self._grids():
                    for L in layers:
                        g[L][i] = BLOCKED
            if k.get("vias", True):
                self.no_via.add(i)

    def _add_pad(self, p):
        net = self.net_id.get(p["net"], BLOCKED)
        r = p["rect"]
        extra = self.track / 2 + self.margin
        reach = max(self._clr(net), self.hv_clr if self.hv_ids else 0) + extra
        inner = self.track / 2
        layers = [LAYERS.index(L) for L in p["layers"] if L in LAYERS]
        self.items.append((net, set(layers), "rect", r))
        self._bin(self.items[-1])
        for ix, iy in self._cells_near(r[0], r[1], r[2], r[3], reach):
            x, y = self.cell_xy(ix, iy)
            d = _rect_dist(x, y, r)
            i = self.idx(ix, iy)
            self._claim_at(layers, i, net, d, extra)
            for L in layers:
                inside = (r[0] + inner <= x <= r[2] - inner) and (r[1] + inner <= y <= r[3] - inner)
                if inside and net >= 0:
                    self.padown[L][i] = net
        cx, cy = self.nearest(p["x"], p["y"])
        for L in layers:
            if net >= 0:
                self.padown[L][self.idx(cx, cy)] = net

    def _mark_segment(self, net, L, x1, y1, x2, y2, half):
        extra = self.track / 2 + half + self.margin
        reach = max(self._clr(net), self.hv_clr if self.hv_ids else 0) + extra
        self.items.append((net, {L}, "seg", (x1, y1, x2, y2, half)))
        self._bin(self.items[-1])
        for ix, iy in self._cells_near(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), reach):
            x, y = self.cell_xy(ix, iy)
            self._claim_at((L,), self.idx(ix, iy), net, _seg_dist(x, y, x1, y1, x2, y2), extra)

    def _mark_via(self, net, x, y, d):
        extra = self.track / 2 + self.margin
        reach = max(self._clr(net), self.hv_clr if self.hv_ids else 0) + extra + d / 2
        self.items.append((net, {0, 1}, "via", (x, y, d / 2)))
        self._bin(self.items[-1])
        for ix, iy in self._cells_near(x, y, x, y, reach):
            cx, cy = self.cell_xy(ix, iy)
            self._claim_at((0, 1), self.idx(ix, iy), net, math.hypot(cx - x, cy - y) - d / 2, extra)
        self._via_cache.clear()

    def passable(self, net, L, i):
        o = self.own[L][i]
        if not (o == FREE or o == net or self.padown[L][i] == net):
            return False
        if self._dil:
            ix, iy = i % self.nx, i // self.nx
            r = self._dil
            for jy in range(max(0, iy - r), min(self.ny - 1, iy + r) + 1):
                for jx in range(max(0, ix - r), min(self.nx - 1, ix + r) + 1):
                    j = jy * self.nx + jx
                    o = self.own[L][j]
                    if not (o == FREE or o == net or self.padown[L][j] == net):
                        return False
        return True

    def _bin(self, item):
        """Index one copper item by its bounding box (pad rect, segment incl. half width, via disc)."""
        _n, _layers, kind, g = item
        if kind == "rect":
            b = g
        elif kind == "seg":
            b = (min(g[0], g[2]) - g[4], min(g[1], g[3]) - g[4], max(g[0], g[2]) + g[4], max(g[1], g[3]) + g[4])
        else:
            b = (g[0] - g[2], g[1] - g[2], g[0] + g[2], g[1] + g[2])
        ent = (item[0], kind, g)
        for bx in range(int(b[0] // VBIN), int(b[2] // VBIN) + 1):
            for by in range(int(b[1] // VBIN), int(b[3] // VBIN) + 1):
                self._vbins.setdefault((bx, by), []).append(ent)

    def _near(self, x, y, reach):
        """Each item whose bounding box comes within reach of (x, y), once."""
        seen = set()
        for bx in range(int((x - reach) // VBIN), int((x + reach) // VBIN) + 1):
            for by in range(int((y - reach) // VBIN), int((y + reach) // VBIN) + 1):
                for ent in self._vbins.get((bx, by), ()):
                    if id(ent) not in seen:
                        seen.add(id(ent))
                        yield ent

    def via_ok(self, net, ix, iy):
        key = (net, ix, iy)
        if key in self._via_cache:
            return self._via_cache[key]
        x, y = self.cell_xy(ix, iy)
        rv = self.via_d / 2
        ok = (
            x - rv >= self.edge
            and y - rv >= self.edge
            and x + rv <= self.w - self.edge
            and y + rv <= self.h - self.edge
        )
        if ok and self.idx(ix, iy) in self.no_via:
            ok = False
        if ok:
            for n, kind, geom in self._near(x, y, max(self.hv_clr, self.clr, 0.3) + rv):
                if kind == "rect":
                    # no via in or next to any pad, own net included (keeps pads solderable)
                    if _rect_dist(x, y, geom) < self._clr(n, net) + rv:
                        ok = False
                        break
                elif kind == "seg":
                    if n != net and _seg_dist(x, y, *geom[:4]) < self._clr(n, net) + rv + geom[4]:
                        ok = False
                        break
                else:
                    gap = math.hypot(x - geom[0], y - geom[1])
                    # hole to hole spacing applies to every via, same net or not
                    if gap < rv + geom[2] + (self._clr(n, net) if n != net else 0.3):
                        ok = False
                        break
        self._via_cache[key] = ok
        return ok

    # existing copper (incremental builds)
    def add_existing(self, tracks, vias):
        for t in tracks:
            net = self.net_id.get(t["net"])
            if net is None:
                continue
            L = LAYERS.index(t["layer"])
            pts = t["pts"]
            for a, b in zip(pts, pts[1:]):
                self._mark_segment(net, L, a[0], a[1], b[0], b[1], t["width"] / 2)
            self.tracks.append(t)
        for v in vias:
            net = self.net_id.get(v["net"])
            if net is None:
                continue
            self._mark_via(net, v["x"], v["y"], v["d"])
            self.vias.append(v)

    def _groups(self, net_name):
        """Connected groups of one net: pads joined by existing tracks and vias."""
        dsu = _DSU()
        pads = [p for p in self.pads if p["net"] == net_name]
        tr = [t for t in self.tracks if t["net"] == net_name]
        vs = [v for v in self.vias if v["net"] == net_name]
        for i, _ in enumerate(pads):
            dsu.find(("p", i))
        eps = 1e-3
        segs = []
        for ti, t in enumerate(tr):
            L = t["layer"]
            for a, b in zip(t["pts"], t["pts"][1:]):
                segs.append((ti, L, a, b))
            dsu.find(("t", ti))

        def touches(pt, L):
            hits = []
            for i, p in enumerate(pads):
                r = p["rect"]
                if L in p["layers"] and r[0] - eps <= pt[0] <= r[2] + eps and r[1] - eps <= pt[1] <= r[3] + eps:
                    hits.append(("p", i))
            for ti, SL, a, b in segs:
                if SL == L and _seg_dist(pt[0], pt[1], a[0], a[1], b[0], b[1]) < eps:
                    hits.append(("t", ti))
            for vi, v in enumerate(vs):
                if math.hypot(pt[0] - v["x"], pt[1] - v["y"]) < eps:
                    hits.append(("v", vi))
            return hits

        for ti, t in enumerate(tr):
            for pt in (t["pts"][0], t["pts"][-1]):
                for h in touches(pt, t["layer"]):
                    dsu.union(("t", ti), h)
        for vi, v in enumerate(vs):
            for L in LAYERS:
                for h in touches((v["x"], v["y"]), L):
                    dsu.union(("v", vi), h)
        groups = {}
        for i, p in enumerate(pads):
            groups.setdefault(dsu.find(("p", i)), {"pads": [], "tracks": []})["pads"].append(p)
        for ti, t in enumerate(tr):
            root = dsu.find(("t", ti))
            if root in groups:
                groups[root]["tracks"].append(t)
        return list(groups.values())

    def _group_cells(self, grp):
        cells = set()
        for p in grp["pads"]:
            ix, iy = self.nearest(p["x"], p["y"])
            for L in p["layers"]:
                if L in LAYERS:
                    cells.add((LAYERS.index(L), ix, iy))
        for t in grp["tracks"]:
            L = LAYERS.index(t["layer"])
            for a, b in zip(t["pts"], t["pts"][1:]):
                n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (self.g * 0.5)))
                for k in range(n + 1):
                    x = a[0] + (b[0] - a[0]) * k / n
                    y = a[1] + (b[1] - a[1]) * k / n
                    ix, iy = self.nearest(x, y)
                    cx, cy = self.cell_xy(ix, iy)
                    if math.hypot(cx - x, cy - y) < 1e-6 or _seg_dist(cx, cy, a[0], a[1], b[0], b[1]) < 1e-6:
                        cells.add((L, ix, iy))
        return cells

    def _mask(self, net):
        """Cached per (net, dilation). A net's own commits never change its own mask (its cells stay passable to
        it), so the cache only drops entries when another net commits copper."""
        cache = self.__dict__.setdefault("_mcache", {})
        k = (net, self._dil)
        if k not in cache:
            cache[k] = self._mask_build(net)
        return cache[k]

    def _mask_build(self, net):
        """passable() for every cell of both layers at once, as two lists of bools (same rule, incl. the
        power-track dilation). Built once per search: a per-cell call inside the A* loop was most of the
        routing time (16M calls on a 76x59 mm board)."""
        nx, ny, r = self.nx, self.ny, self._dil
        out = []
        for L in range(2):
            grid = self.own_hv if net in self.hv_ids else self.own
            m = [o == FREE or o == net or p == net for o, p in zip(grid[L], self.padown[L])]
            if self.pin_zone:
                lo, pd = self.own_lo[L], self.padown[L]
                for i in self.pin_zone:
                    m[i] = lo[i] == FREE or lo[i] == net or pd[i] == net
            if r:
                # square erosion, separable: a cell stays open only if every in-bounds cell within r is open
                h = [False] * len(m)
                for iy in range(ny):
                    base = iy * nx
                    h[base : base + nx] = _erode_line(m[base : base + nx], r)
                v = [False] * len(m)
                for ix in range(nx):
                    v[ix::nx] = _erode_line(h[ix::nx], r)
                m = v
            out.append(m)
        return out

    def _search(self, net, sources, targets, max_expand=MAX_EXPAND):
        """A* from a set of (L, ix, iy) cells to any target cell. Returns the cell path, or None (self.capped is
        True when the expansion budget ran out rather than the board having no way through)."""
        self.capped = False
        mask = self._mask(net)
        nx, ny = self.nx, self.ny
        tlist = list({(ix, iy) for (_, ix, iy) in targets})
        tset = set(targets)

        def h(ix, iy):
            best = 1e18
            for tx, ty in tlist:
                dx, dy = abs(tx - ix), abs(ty - iy)
                v = (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)
                if v < best:
                    best = v
            return best

        openh = []
        gbest = {}
        parent = {}
        for L, ix, iy in sources:
            s = (L, ix, iy, -1)
            gbest[s] = 0.0
            parent[s] = None
            heapq.heappush(openh, (h(ix, iy), 0.0, s))
        expanded = 0
        while openh:
            f, gc, s = heapq.heappop(openh)
            if gc > gbest.get(s, 1e18) + 1e-9:
                continue
            L, ix, iy, d = s
            if (L, ix, iy) in tset and parent[s] is not None:
                path = []
                while s is not None:
                    path.append(s[:3])
                    s = parent[s]
                return path[::-1]
            expanded += 1
            if expanded > max_expand:
                self.capped = True
                return None
            mL = mask[L]
            for k, (dx, dy) in enumerate(DIRS):
                jx, jy = ix + dx, iy + dy
                if not (0 <= jx < nx and 0 <= jy < ny):
                    continue
                if not mL[jy * nx + jx]:
                    continue
                if dx and dy:
                    # diagonal: both orthogonal neighbours must be clear too (no corner cutting)
                    if not (mL[iy * nx + ix + dx] and mL[(iy + dy) * nx + ix]):
                        continue
                step = math.sqrt(2) if (dx and dy) else 1.0
                turn = 0.0
                if d >= 0 and d != k:
                    diff = min((k - d) % 8, (d - k) % 8)
                    turn = 0.4 * diff if diff <= 2 else 5.0
                layer_pen = 0.15 if L == 1 else 0.0
                ng = gc + step + turn + layer_pen
                ns = (L, jx, jy, k)
                if ng < gbest.get(ns, 1e18):
                    gbest[ns] = ng
                    parent[ns] = s
                    heapq.heappush(openh, (ng + h(jx, jy), ng, ns))
            OL = 1 - L
            if mask[OL][iy * nx + ix] and self.via_ok(net, ix, iy):
                ns = (OL, ix, iy, -1)
                ng = gc + self.via_cost
                if ng < gbest.get(ns, 1e18):
                    gbest[ns] = ng
                    parent[ns] = s
                    heapq.heappush(openh, (ng + h(ix, iy), ng, ns))
        return None

    def _commit(self, net_name, path, start_pad, end_pad, width=None):
        net = self.net_id[net_name]
        mc = self.__dict__.get("_mcache")
        if mc:  # this copper blocks every other net; their cached masks are stale now
            for k in [k for k in mc if k[0] != net]:
                del mc[k]
        runs = []  # list of (layer, [cells])
        for c in path:
            if runs and runs[-1][0] == c[0]:
                runs[-1][1].append(c)
            else:
                runs.append((c[0], [c]))
        new_tracks, new_vias = [], []
        for ri, (L, cells) in enumerate(runs):
            pts = [self.cell_xy(c[1], c[2]) for c in cells]
            if ri == 0 and start_pad is not None:
                pts.insert(0, (start_pad["x"], start_pad["y"]))
            if ri == len(runs) - 1 and end_pad is not None:
                pts.append((end_pad["x"], end_pad["y"]))
            pts = _simplify(pts)
            if len(pts) >= 2:
                new_tracks.append(
                    {
                        "net": net_name,
                        "layer": LAYERS[L],
                        "width": width or self.track,
                        "pts": [[round(x, 4), round(y, 4)] for x, y in pts],
                    }
                )
            if ri < len(runs) - 1:
                x, y = self.cell_xy(cells[-1][1], cells[-1][2])
                new_vias.append(
                    {"net": net_name, "x": round(x, 4), "y": round(y, 4), "d": self.via_d, "drill": self.via_drill}
                )
        for t in new_tracks:
            L = LAYERS.index(t["layer"])
            for a, b in zip(t["pts"], t["pts"][1:]):
                self._mark_segment(net, L, a[0], a[1], b[0], b[1], t["width"] / 2)
        for v in new_vias:
            self._mark_via(net, v["x"], v["y"], v["d"])
        self.tracks.extend(new_tracks)
        self.vias.extend(new_vias)
        return new_tracks, new_vias

    def route_net(self, net_name, max_expand=MAX_EXPAND):
        """Join every group of the net. Returns (new_tracks, new_vias, unrouted_pad_list)."""
        net = self.net_id[net_name]
        out_t, out_v = [], []
        groups = self._groups(net_name)
        while len(groups) > 1:
            groups.sort(key=lambda g: -(len(g["pads"]) + len(g["tracks"])))
            base = groups[0]
            src = self._group_cells(base)
            targets = {}
            for gi, grp in enumerate(groups[1:], 1):
                for p in grp["pads"]:
                    ix, iy = self.nearest(p["x"], p["y"])
                    for L in p["layers"]:
                        if L in LAYERS:
                            targets[(LAYERS.index(L), ix, iy)] = (gi, p)
            width = self.track
            path = None
            if net_name in self.power_nets and self.power_track > self.track:
                # wide first; a connection with no room for the wide track falls back to signal width
                self._dil = max(1, int(math.ceil((self.power_track - self.track) / 2 / self.g)))
                # capped: a wide track that has no room is found out quickly, then signal width is tried
                path = self._search(net, src, set(targets), max_expand=WIDE_MAX_EXPAND)
                self._dil = 0
                if path is not None:
                    width = self.power_track
            if path is None:
                path = self._search(net, src, set(targets), max_expand=max_expand)
            if path is None:
                rest = [p for g in groups[1:] for p in g["pads"]]
                return out_t, out_v, rest
            end_gi, end_pad = targets[path[-1]]
            start_pad = None
            for p in base["pads"]:
                ix, iy = self.nearest(p["x"], p["y"])
                if (ix, iy) == (path[0][1], path[0][2]):
                    start_pad = p
                    break
            t, v = self._commit(net_name, path, start_pad, end_pad, width)
            out_t += t
            out_v += v
            merged = {
                "pads": base["pads"] + groups[end_gi]["pads"],
                "tracks": base["tracks"] + groups[end_gi]["tracks"] + t,
            }
            groups = [merged] + [g for k, g in enumerate(groups) if k not in (0, end_gi)]
        return out_t, out_v, []


def pin_zones(pads, hv_names, pad_margin=0.25):
    """{ref: (x0, y0, x1, y1)}: the pad area (plus pad_margin, inside the courtyard) of every part that has an HV
    pin and a pin of any other net, e.g. the MC34063's SOIC-8 with its 170 V switch pin 1.27 mm from pin 2. The
    router relaxes the HV gap there and layout.py writes the matching DRC exception for that courtyard."""
    by_ref = {}
    for p in pads:
        by_ref.setdefault(p["ref"], []).append(p)
    out = {}
    for ref, ps in by_ref.items():
        nets = {p["net"] for p in ps if p["net"]}
        if nets & set(hv_names) and len(nets) > 1:  # two HV nets (an anode resistor) need the gap too
            out[ref] = (
                min(p["rect"][0] for p in ps) - pad_margin,
                min(p["rect"][1] for p in ps) - pad_margin,
                max(p["rect"][2] for p in ps) + pad_margin,
                max(p["rect"][3] for p in ps) + pad_margin,
            )
    return out


def _erode_line(line, r):
    """out[i] = all(line[max(0, i-r) : min(n-1, i+r) + 1]), in one pass with a running count of closed cells.
    (The window's upper bound is min(n-1, i+r) inclusive, kept identical to the old slice form.)"""
    n = len(line)
    pre = [0] * (n + 1)
    c = 0
    for i, ok in enumerate(line):
        if not ok:
            c += 1
        pre[i + 1] = c
    out = [False] * n
    for i in range(n):
        lo = i - r if i > r else 0
        hi = i + r
        if hi > n - 1:
            hi = n - 1
        out[i] = pre[hi + 1] - pre[lo] == 0
    return out


def _simplify(pts):
    out = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 1e-9 and abs(out[-1][1] - p[1]) < 1e-9:
            continue
        out.append(p)
    i = 1
    while i < len(out) - 1:
        a, b, c = out[i - 1], out[i], out[i + 1]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) < 1e-9:
            out.pop(i)
        else:
            i += 1
    return out


def net_order(pads, nets):
    """Shortest nets first (half-perimeter of the pad bounding box)."""

    def hpwl(n):
        xs = [p["x"] for p in pads if p["net"] == n]
        ys = [p["y"] for p in pads if p["net"] == n]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    return sorted(nets, key=hpwl)


def route_all(make_router, order, attempts=4, budget_s=None, clock=time.monotonic):
    """Route every net with rip-up and retry, and never hand back less than the best attempt.

    make_router() -> a fresh Router (incremental base copper already added). Each failed attempt is ripped up and
    the failures go first next time, with RETRY_MAX_EXPAND for them. The attempt that left the fewest nets unrouted
    is returned (QA 10-17: the old loop ripped up after the LAST failed attempt too, so one hard net turned
    19 good tracks into 0 and every net was reported unrouted). budget_s is a wall-clock limit checked between
    nets (and each search is capped to the time left): when it runs out the routing so far is kept and
    info["timed_out"] says so. Attempts after the first keep the best attempt's copper away from the failures.

    Returns (router, info) with info = {attempts, timed_out, failed, capped, seconds, new_tracks, new_vias}; the new_*
    lists are the copper this call added on top of the base."""
    t0 = clock()
    best = None  # (failed count, router, failed, capped, base)
    retry = set()
    timed_out = False
    done = 0
    for attempt in range(attempts):
        router = make_router()
        base = (len(router.tracks), len(router.vias))
        if 0 < attempt < attempts - 1 and hasattr(router, "add_existing"):
            # keep the best attempt's copper except the nets near what failed (a wider ring each time); only
            # those get routed again. The last attempt starts over with the failures first.
            keep_t, keep_v = _keep_away(best, RIP_MARGIN_MM * attempt)
            router.add_existing(keep_t, keep_v)
        failed, capped, stuck = [], [], {}
        for n in order:
            left = None if budget_s is None else budget_s - (clock() - t0)
            if left is not None and left <= 0:
                timed_out = True
                failed += [m for m in order if m not in failed and m not in _routed_nets(router)]
                break
            cap = RETRY_MAX_EXPAND if n in retry else MAX_EXPAND
            if left is not None:
                # one search must not run far past the budget (the check above only runs between nets)
                cap = max(1000, min(cap, int(left * EXPANSIONS_PER_S)))
            _, _, rest = router.route_net(n, max_expand=cap)
            if rest:
                failed.append(n)
                stuck[n] = rest
                if getattr(router, "capped", False):
                    capped.append(n)
        done += 1
        if best is None or len(failed) < best[0]:
            best = (len(failed), router, failed, capped, base, stuck)
        if not failed or timed_out:
            break
        retry |= set(failed)
        order = failed + [n for n in order if n not in failed]
    router, base = best[1], best[4]
    return router, {
        "new_tracks": router.tracks[base[0] :],
        "new_vias": router.vias[base[1] :],
        "attempts": done,
        "timed_out": timed_out,
        "failed": best[2],
        "capped": best[3],
        "seconds": round(clock() - t0, 3),
    }


def _keep_away(best, margin):
    """(tracks, vias) of the best attempt's own copper, minus the failed nets and every net with copper within
    margin mm of the pads a failed net could not reach."""
    _, router, failed, _, base, stuck = best
    boxes = []
    for n in failed:
        # the pads the search could not reach (a timed-out net has none recorded: all of its pads)
        ps = [p for p in (stuck.get(n) or router.pads) if p["net"] == n]
        if ps:
            boxes.append(
                (
                    min(p["rect"][0] for p in ps) - margin,
                    min(p["rect"][1] for p in ps) - margin,
                    max(p["rect"][2] for p in ps) + margin,
                    max(p["rect"][3] for p in ps) + margin,
                )
            )

    def inside(x0, y0, x1, y1):
        return any(not (x1 < b[0] or b[2] < x0 or y1 < b[1] or b[3] < y0) for b in boxes)

    tracks, vias = router.tracks[base[0] :], router.vias[base[1] :]
    rip = set(failed)
    for t in tracks:
        for a, b in zip(t["pts"], t["pts"][1:]):
            if inside(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])):
                rip.add(t["net"])
                break
    for v in vias:
        if inside(v["x"], v["y"], v["x"], v["y"]):
            rip.add(v["net"])
    return [t for t in tracks if t["net"] not in rip], [v for v in vias if v["net"] not in rip]


def _routed_nets(router):
    """Nets already joined into one group on this router (so a timed-out attempt does not list them as failed)."""
    return {n for n in router.nets if len(router._groups(n)) <= 1}
