import { BOARD, FINALE, PARTS, POUR, TRACES, VIAS, clamp, ease, partOffset, pathLen, type Part, type Pt, type Trace } from "@/lib/demo";

const COPPER = "#d08a4a";
const PAD = "#e6c07a";
const BAD = "#ff5a5a";
const GOOD = "#5dffa0";

const d = (pts: Pt[]) => pts.map((p, i) => `${i ? "L" : "M"}${p[0]} ${p[1]}`).join(" ");

// Problems the verifier found and the moment they were fixed: drawn red until fixed.
const PROBLEMS: { ref: string; from: number; to: number }[] = [
  { ref: "U1", from: 29.6, to: 37 },
  { ref: "C2", from: 30.8, to: 93 },
  { ref: "J1", from: 82, to: 86 },
];
const FIXES: { ref: string; t: number }[] = [
  { ref: "U1", t: 37 },
  { ref: "D1", t: 57.4 },
  { ref: "C2", t: 93 },
  { ref: "J1", t: 86 },
  { ref: "U3", t: 105.6 },
];

function traceWidth(tr: Trace, T: number) {
  if (!tr.widen) return tr.w;
  return tr.w + (tr.widen.w - tr.w) * ease((T - tr.widen.t) / 2);
}

function PartView({ p, T }: { p: Part; T: number }) {
  const k = clamp((T - p.t) / 0.5);
  if (k <= 0) return null;
  const s = 1 + 0.5 * (1 - ease(k));
  const dx = partOffset(p, T);
  const cx = p.x + p.w / 2;
  const cy = p.y + p.h / 2;
  const problem = PROBLEMS.find((q) => q.ref === p.ref && T >= q.from && T < q.to);
  const fix = FIXES.find((f) => f.ref === p.ref && T >= f.t && T < f.t + 1.6);
  const label = p.swap && T >= p.swap.t ? p.swap.label : p.label;
  const swapFlash = p.swap ? clamp(1 - Math.abs(T - p.swap.t) / 0.6) : 0;
  return (
    <g transform={`translate(${cx + dx} ${cy}) scale(${s}) translate(${-cx} ${-cy})`} opacity={ease(k)}>
      {p.silk ? (
        <rect x={p.x} y={p.y} width={p.w} height={p.h} rx={0.3} fill="none" stroke="#e9ecef" strokeOpacity={0.55} strokeWidth={0.12} />
      ) : (
        <rect x={p.x} y={p.y} width={p.w} height={p.h} rx={0.35} fill={p.ref === "J1" ? "#9aa3ad" : "#14161a"} stroke="#e9ecef" strokeOpacity={0.35} strokeWidth={0.1} />
      )}
      {p.pads.map((pd, i) =>
        pd.round ? (
          <circle key={i} cx={pd.at[0]} cy={pd.at[1]} r={pd.w / 2} fill={PAD} />
        ) : (
          <rect key={i} x={pd.at[0] - pd.w / 2} y={pd.at[1] - pd.h / 2} width={pd.w} height={pd.h} rx={0.08} fill={PAD} />
        ),
      )}
      {p.silk && p.pads.map((pd, i) => <circle key={`h${i}`} cx={pd.at[0]} cy={pd.at[1]} r={0.45} fill="#0b0d10" />)}
      <text x={p.x + p.w / 2} y={p.silk ? p.y - 0.45 : p.y - 0.5} textAnchor="middle" fontSize={p.silk ? 0.95 : 1.05} fill="#f2f4f8" fillOpacity={0.85} fontFamily="var(--font-geist-mono)">
        {p.ref} {label}
      </text>
      {swapFlash > 0 && <rect x={p.x - 0.8} y={p.y - 0.8} width={p.w + 1.6} height={p.h + 1.6} rx={0.6} fill="#fff" opacity={0.25 * swapFlash} />}
      {problem && <rect x={p.x - 0.6} y={p.y - 0.6} width={p.w + 1.2} height={p.h + 1.2} rx={0.5} fill="none" stroke={BAD} strokeWidth={0.22} strokeDasharray="0.6 0.4" opacity={0.6 + 0.4 * Math.sin(T * 6)} />}
      {fix && <rect x={p.x - 0.6 - (T - fix.t)} y={p.y - 0.6 - (T - fix.t)} width={p.w + 1.2 + 2 * (T - fix.t)} height={p.h + 1.2 + 2 * (T - fix.t)} rx={0.6} fill="none" stroke={GOOD} strokeWidth={0.2} opacity={1 - (T - fix.t) / 1.6} />}
    </g>
  );
}

export default function Board({ T, showFlow = true }: { T: number; showFlow?: boolean }) {
  const outline = clamp((T - 7.5) / 1.5);
  const pour = clamp((T - POUR.t) / POUR.dur);
  const flowK = showFlow ? clamp((T - FINALE) / 2) : 0;
  const ft = T - FINALE;
  return (
    <svg viewBox={`-2 -2 ${BOARD.w + 4} ${BOARD.h + 4}`} className="h-full w-full" role="img" aria-label="Circuit board being designed">
      <defs>
        <pattern id="grid" width="2" height="2" patternUnits="userSpaceOnUse">
          <path d="M2 0H0V2" fill="none" stroke="#ffffff" strokeOpacity={0.05} strokeWidth={0.05} />
        </pattern>
        <pattern id="hatch" width="0.9" height="0.9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width="0.9" height="0.9" fill="#13304d" />
          <line x1="0" y1="0" x2="0" y2="0.9" stroke="#2f5f8f" strokeWidth={0.25} />
        </pattern>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="0.55" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id="soft" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="1.4" />
        </filter>
        <clipPath id="pourclip">
          <rect x={0} y={0} width={BOARD.w * ease(pour)} height={BOARD.h} />
        </clipPath>
        <clipPath id="boardclip">
          <rect x={0} y={0} width={BOARD.w} height={BOARD.h} rx={2} />
        </clipPath>
      </defs>

      <rect x={-2} y={-2} width={BOARD.w + 4} height={BOARD.h + 4} fill="url(#grid)" />

      {outline > 0 && (
        <>
          <rect x={0} y={0} width={BOARD.w} height={BOARD.h} rx={2} fill="#0c1813" opacity={ease(outline)} />
          <rect x={0} y={0} width={BOARD.w} height={BOARD.h} rx={2} fill="none" stroke="#e9ecef" strokeOpacity={0.7} strokeWidth={0.18} pathLength={1} strokeDasharray={`${ease(outline)} 1`} />
        </>
      )}

      {pour > 0 && (
        <g clipPath="url(#boardclip)">
          <rect x={0.8} y={0.8} width={BOARD.w - 1.6} height={BOARD.h - 1.6} rx={1.4} fill="url(#hatch)" opacity={0.75} clipPath="url(#pourclip)" />
          {flowK > 0 && <rect x={0.8} y={0.8} width={BOARD.w - 1.6} height={BOARD.h - 1.6} rx={1.4} fill="#3d7bd1" opacity={flowK * (0.1 + 0.06 * Math.sin(ft * 3))} />}
        </g>
      )}

      {TRACES.map((tr) => {
        const p = clamp((T - tr.t) / tr.dur);
        if (p <= 0) return null;
        const w = traceWidth(tr, T);
        const hot = tr.id === "servo" && T >= 30 && T < 47;
        const widenFlash = tr.widen ? clamp(1 - Math.abs(T - tr.widen.t - 1) / 1.2) : 0;
        return (
          <g key={tr.id}>
            {widenFlash > 0 && <path d={d(tr.pts)} fill="none" stroke="#fff" strokeOpacity={0.35 * widenFlash} strokeWidth={w + 1.2} strokeLinecap="round" strokeLinejoin="round" filter="url(#soft)" />}
            <path d={d(tr.pts)} fill="none" stroke={hot ? BAD : COPPER} strokeOpacity={hot ? 0.65 + 0.35 * Math.sin(T * 6) : 1} strokeWidth={w} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={`${p} 1`} />
            {p < 1 && <circle cx={pointAt(tr.pts, p)[0]} cy={pointAt(tr.pts, p)[1]} r={0.55} fill="#fff" filter="url(#glow)" />}
          </g>
        );
      })}

      {pour > 0.3 && VIAS.map((v, i) => <circle key={i} cx={v[0]} cy={v[1]} r={0.38} fill={PAD} stroke="#0b0d10" strokeWidth={0.12} opacity={clamp((pour - 0.3) * 2)} />)}

      {T >= 30.4 && T < 57 && (
        <g opacity={0.55 + 0.35 * Math.sin(T * 5)}>
          <rect x={10.1} y={11.7} width={2.8} height={1.9} rx={0.4} fill="none" stroke={BAD} strokeWidth={0.18} strokeDasharray="0.5 0.35" />
          <text x={11.5} y={11.2} textAnchor="middle" fontSize={0.9} fill={BAD} fontFamily="var(--font-geist-mono)">no surge part</text>
        </g>
      )}

      {PARTS.map((p) => (
        <PartView key={p.ref} p={p} T={T} />
      ))}

      {flowK > 0 && (
        <g opacity={flowK}>
          {TRACES.filter((tr) => tr.flow && T >= tr.t).map((tr, i) => {
            const f = tr.flow!;
            const w = traceWidth(tr, T);
            const L = pathLen(tr.pts);
            const on = clamp((ft - i * 0.12) / 0.8);
            return (
              <g key={tr.id} opacity={on}>
                <path d={d(tr.pts)} fill="none" stroke={f.color} strokeOpacity={0.35} strokeWidth={w + 0.5} strokeLinecap="round" strokeLinejoin="round" filter="url(#soft)" />
                <path
                  d={d(tr.pts)}
                  fill="none"
                  stroke={f.color}
                  strokeWidth={Math.max(w * 0.7, 0.18)}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeDasharray={`${Math.max(w, 0.5) * 1.6} ${Math.max(w, 0.5) * 2.4}`}
                  strokeDashoffset={-((ft * f.speed) % L)}
                  filter="url(#glow)"
                />
              </g>
            );
          })}
          <circle cx={17.4} cy={33.6} r={1.6} fill="#5dffa0" opacity={0.25 + 0.2 * Math.sin(ft * 8)} filter="url(#soft)" />
          <rect x={23.5} y={15.5} width={9} height={9} rx={1} fill="#7fe0ff" opacity={0.08 + 0.06 * Math.sin(ft * 5)} filter="url(#soft)" />
          {TRACES.filter((tr) => tr.flow?.amps && tr.flow.at).map((tr) => (
            <g key={`l${tr.id}`} transform={`translate(${tr.flow!.at![0]} ${tr.flow!.at![1]})`}>
              <rect x={0.8} y={-1.6} width={tr.flow!.amps!.length * 0.62 + 1} height={1.6} rx={0.4} fill="#07080b" fillOpacity={0.85} stroke={tr.flow!.color} strokeOpacity={0.6} strokeWidth={0.08} />
              <text x={1.3} y={-0.48} fontSize={1} fill={tr.flow!.color} fontFamily="var(--font-geist-mono)">
                {tr.flow!.amps}
              </text>
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}

function pointAt(pts: Pt[], f: number): Pt {
  const L = pathLen(pts) * f;
  let acc = 0;
  for (let i = 1; i < pts.length; i++) {
    const seg = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    if (acc + seg >= L) {
      const u = seg ? (L - acc) / seg : 0;
      return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * u, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * u];
    }
    acc += seg;
  }
  return pts[pts.length - 1];
}
