"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Board from "@/components/Board";
import {
  AGENTS,
  BOARD,
  BOARD_TYPES,
  CHECKS,
  END,
  FINALE,
  ITERS,
  OUTPUTS,
  activeStep,
  activity,
  checkState,
  clamp,
  ease,
  type AgentId,
  type Step,
} from "@/lib/demo";

type Sentence = { type: string; does: string; power: string; size: string };
const SPEEDS = [1, 4, 20] as const;

function useClock(playing: boolean, speed: number, loop: boolean) {
  const [T, setT] = useState(0);
  const last = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) {
      last.current = null;
      return;
    }
    let raf = 0;
    const tick = (now: number) => {
      const dt = last.current === null ? 0 : Math.min(0.1, (now - last.current) / 1000);
      last.current = now;
      setT((t) => {
        const n = t + dt * speed;
        if (n >= END) return loop ? 0 : END;
        return n;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, loop]);
  return [T, setT] as const;
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

const KIND_TONE: Record<Step["kind"], string> = {
  plan: "text-white/60 border-white/20",
  build: "text-amber-200 border-amber-200/40",
  check: "text-rose-300 border-rose-300/40",
  fix: "text-emerald-300 border-emerald-300/40",
  add: "text-cyan-300 border-cyan-300/40",
  drift: "text-rose-300 border-rose-300/40",
  done: "text-emerald-300 border-emerald-300/40",
};
const KIND_LABEL: Record<Step["kind"], string> = {
  plan: "plan",
  build: "build",
  check: "verify",
  fix: "fix",
  add: "new feature",
  drift: "drift caught",
  done: "done",
};

function FocusDot({ step, T }: { step: Step; T: number }) {
  const k = Math.min(ease((T - step.t) / 0.45), ease((step.t + step.dur - T) / 0.4));
  const left = ((step.focus[0] + 2) / (BOARD.w + 4)) * 100;
  const top = ((step.focus[1] + 2) / (BOARD.h + 4)) * 100;
  return (
    <div className="pointer-events-none absolute inset-0">
      <div className="absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white" style={{ left: `${left}%`, top: `${top}%`, opacity: k, boxShadow: `0 0 0 ${6 + 4 * Math.sin(T * 5)}px rgba(255,255,255,0.12)` }} />
    </div>
  );
}

function StepCard({ step, T }: { step: Step; T: number }) {
  const k = Math.min(ease((T - step.t) / 0.45), ease((step.t + step.dur - T) / 0.4));
  return (
    <div
      className="pointer-events-none absolute inset-x-0 bottom-4 mx-auto flex w-[min(720px,calc(100%-32px))] items-start gap-4 rounded-xl border border-white/15 bg-[#0d0f13]/95 px-4 py-3 shadow-2xl shadow-black/60 backdrop-blur"
      style={{ opacity: k, transform: `translateY(${(1 - k) * 12}px) scale(${0.97 + 0.03 * k})` }}
    >
      <div className="flex shrink-0 flex-col items-start gap-1 pt-0.5">
        <span className={`rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest ${KIND_TONE[step.kind]}`}>{KIND_LABEL[step.kind]}</span>
        <span className="font-mono text-[10px] uppercase tracking-widest text-white/40">{AGENTS[step.agent].name} agent</span>
      </div>
      <div className="min-w-0">
        <div className="text-[16px] font-semibold leading-snug">{step.title}</div>
        <p className="mt-0.5 text-sm leading-relaxed text-white/65">{step.reason}</p>
      </div>
    </div>
  );
}

function CheckRow({ c, T }: { c: (typeof CHECKS)[number]; T: number }) {
  const s = checkState(c, T);
  if (s.state === "unwritten") return null;
  const k = ease((T - c.writtenT) / 0.4);
  const tone = s.state === "pass" ? "text-emerald-300" : s.state === "fail" ? "text-rose-300" : "text-white/40";
  return (
    <li className="flex items-start gap-3 rounded-lg border border-white/[0.07] bg-white/[0.02] px-3 py-2.5" style={{ opacity: k, transform: `translateX(${(1 - k) * 12}px)` }}>
      <span className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border text-[11px] ${s.state === "pass" ? "border-emerald-300/60 bg-emerald-300/10 text-emerald-300" : s.state === "fail" ? "border-rose-300/60 bg-rose-300/10 text-rose-300" : "border-white/20 text-white/40"}`}>
        {s.state === "pass" ? "✓" : s.state === "fail" ? "✕" : s.state === "running" ? <span className="size-2 animate-ping rounded-full bg-white/70" /> : ""}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm">{c.label}</span>
          {s.value && <span className={`shrink-0 font-mono text-xs tabular-nums ${tone}`}>{s.value}</span>}
        </div>
        <div className="font-mono text-[10px] uppercase tracking-wide text-white/35">{c.spec}</div>
      </div>
    </li>
  );
}

function Compose({ onBuild }: { onBuild: (s: Sentence) => void }) {
  const [pick, setPick] = useState<string>("arm");
  const bt = BOARD_TYPES.find((b) => b.id === pick) ?? BOARD_TYPES[0];
  const [s, setS] = useState<Sentence>({ type: bt.name, ...bt.sentence });
  const choose = (id: string) => {
    const b = BOARD_TYPES.find((x) => x.id === id);
    if (!b) return;
    setPick(id);
    setS({ type: b.name, ...b.sentence });
  };
  const blank = "mx-1 inline-block border-b-2 border-dashed border-amber-200/50 bg-transparent px-1 text-amber-100 outline-none focus:border-amber-200";
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center gap-10 px-6 py-12">
      <div>
        <div className="font-mono text-xs uppercase tracking-[0.4em] text-amber-200">New board</div>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight md:text-5xl">What are we building?</h1>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5">
        {BOARD_TYPES.map((b) => (
          <button key={b.id} type="button" onClick={() => choose(b.id)} className={`rounded-xl border px-3 py-3 text-left transition ${pick === b.id ? "border-amber-200/60 bg-amber-200/[0.07]" : "border-white/10 bg-white/[0.02] hover:border-white/25"}`}>
            <div className="text-sm font-medium">{b.name}</div>
            <div className="mt-0.5 font-mono text-[10px] uppercase tracking-wide text-white/40">{b.blurb}</div>
          </button>
        ))}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onBuild(s);
        }}
        className="rounded-2xl border border-white/10 bg-white/[0.02] p-6"
      >
        <p className="text-2xl leading-[2.2] text-white/80 md:text-3xl md:leading-[2.2]">
          Build me a<input aria-label="Board type" className={blank} style={{ width: `${Math.max(s.type.length, 5) + 1}ch` }} value={s.type} onChange={(e) => setS({ ...s, type: e.target.value })} />
          that<input aria-label="What it does" className={blank} style={{ width: `${Math.max(s.does.length, 5) + 1}ch` }} value={s.does} onChange={(e) => setS({ ...s, does: e.target.value })} />
          , powered by<input aria-label="Power" className={blank} style={{ width: `${Math.max(s.power.length, 5) + 1}ch` }} value={s.power} onChange={(e) => setS({ ...s, power: e.target.value })} />
          , that fits in<input aria-label="Size" className={blank} style={{ width: `${Math.max(s.size.length, 5) + 1}ch` }} value={s.size} onChange={(e) => setS({ ...s, size: e.target.value })} />.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
          <p className="font-mono text-[11px] uppercase tracking-widest text-white/35">Agents write the checks, then loop until every one passes</p>
          <button type="submit" className="rounded-xl bg-amber-200 px-6 py-3 text-base font-semibold text-black transition hover:bg-amber-100">
            Build it →
          </button>
        </div>
      </form>
    </div>
  );
}

export default function Studio({ autoplay = false }: { autoplay?: boolean }) {
  const [sentence, setSentence] = useState<Sentence | null>(autoplay ? { type: "Robot arm controller", ...BOARD_TYPES[0].sentence } : null);
  const [playing, setPlaying] = useState(autoplay);
  const [speed, setSpeed] = useState<number>(autoplay ? 4 : 1);
  const [T, setT] = useClock(playing, speed, autoplay);

  const build = useCallback(
    (s: Sentence) => {
      setSentence(s);
      setT(0);
      setPlaying(true);
    },
    [setT],
  );

  useEffect(() => {
    if (autoplay || !sentence) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
      if (e.key === "1") setSpeed(1);
      if (e.key === "2") setSpeed(4);
      if (e.key === "3") setSpeed(20);
      if (e.key === "r") setT(0);
      if (e.key === "ArrowRight") setT((t) => Math.min(END, t + 5));
      if (e.key === "ArrowLeft") setT((t) => Math.max(0, t - 5));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [autoplay, sentence, setT]);

  if (!sentence) return <Compose onBuild={build} />;

  const step = activeStep(T);
  const fin = ease((T - FINALE) / 3);
  const passed = CHECKS.filter((c) => checkState(c, T).state === "pass").length;
  const written = CHECKS.filter((c) => T >= c.writtenT).length;
  const iter = [...ITERS].reverse().find((i) => i.t <= T);
  const phase = T < 7 ? "Writing the definition of done" : T < 28 ? "Building the baseline" : T < 113 ? `Loop · iteration ${iter?.label ?? ""}` : T < FINALE ? "Done" : "Power-on simulation";
  const outputsK = ease((T - 121) / 1);

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${autoplay ? "" : "h-screen"}`}>
      {!autoplay && (
        <header className="flex items-center justify-between gap-4 border-b border-white/[0.07] px-5 py-3">
          <div className="flex min-w-0 items-center gap-4">
            <Link href="/" className="font-mono text-sm uppercase tracking-[0.4em] text-amber-200">
              Etch
            </Link>
            <span className="truncate text-sm text-white/60">
              {sentence.type} that {sentence.does}, powered by {sentence.power}, {sentence.size}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="hidden font-mono text-[10px] uppercase tracking-widest text-white/30 md:inline">recorded run</span>
            <button type="button" onClick={() => setPlaying((p) => !p)} className="rounded-md border border-white/15 px-3 py-1 font-mono text-xs text-white/70 hover:border-white/40">
              {playing ? "pause" : "play"}
            </button>
            {SPEEDS.map((s) => (
              <button key={s} type="button" onClick={() => setSpeed(s)} className={`rounded-md border px-2.5 py-1 font-mono text-xs ${speed === s ? "border-amber-200/60 text-amber-200" : "border-white/15 text-white/50 hover:border-white/40"}`}>
                {s}×
              </button>
            ))}
            <button type="button" onClick={() => setSentence(null)} className="rounded-md border border-white/15 px-3 py-1 font-mono text-xs text-white/50 hover:border-white/40">
              new
            </button>
          </div>
        </header>
      )}

      <div className={`grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)] gap-4 p-4 ${autoplay ? "lg:grid-cols-[1fr_300px]" : "lg:grid-cols-[230px_1fr_330px]"}`}>
        {!autoplay && (
          <aside className="hidden min-h-0 flex-col gap-2 lg:flex">
            <h2 className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/40">Agent team</h2>
            {(Object.keys(AGENTS) as AgentId[]).map((id) => {
              const a = activity(id, T);
              const busy = a !== "idle" && a !== "waiting" && !a.startsWith("stopped") && !a.includes("pass");
              return (
                <div key={id} className={`rounded-lg border px-3 py-2.5 transition ${step?.agent === id ? "border-amber-200/50 bg-amber-200/[0.05]" : "border-white/[0.07]"}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{AGENTS[id].name}</span>
                    <span className={`size-2 rounded-full ${busy ? "animate-pulse bg-amber-200" : "bg-white/20"}`} />
                  </div>
                  <div className="font-mono text-[10px] uppercase tracking-wide text-white/35">{AGENTS[id].model}</div>
                  <div className="mt-1 truncate text-xs text-white/60">{a}</div>
                </div>
              );
            })}
            <div className="mt-auto rounded-lg border border-white/[0.07] p-3 font-mono text-[10px] uppercase leading-relaxed tracking-wide text-white/35">
              Crusoe Managed Inference
              <br />
              NVIDIA Nemotron 3
            </div>
          </aside>
        )}

        <section className="relative flex min-h-[360px] min-w-0 flex-col overflow-hidden rounded-2xl border border-white/[0.07] bg-[#090a0d]">
          <div className="flex items-center justify-between px-4 pt-3">
            <div className="font-mono text-xs uppercase tracking-[0.3em] text-amber-200">{phase}</div>
            <div className="font-mono text-xs tabular-nums text-white/40">{fmt(T)}</div>
          </div>
          <div className="relative flex min-h-0 flex-1 items-center justify-center p-4" style={{ perspective: "1600px", paddingBottom: 16 + 96 * (1 - fin) }}>
            <div
              className="relative aspect-[64/44] max-h-full w-full max-w-[1100px]"
              style={{
                transform: `translateY(${36 * fin}px) rotateX(${42 * fin}deg) rotateZ(${-16 * fin}deg) scale(${1 - 0.1 * fin})`,
                transformStyle: "preserve-3d",
                filter: fin > 0 ? `drop-shadow(0 ${30 * fin}px ${40 * fin}px rgba(0,0,0,0.7))` : undefined,
              }}
            >
              <Board T={T} />
              {step && T < FINALE && <FocusDot step={step} T={T} />}
            </div>
            {step && T < FINALE && <StepCard step={step} T={T} />}
            {T >= FINALE && (
              <div className="pointer-events-none absolute left-6 top-4 max-w-sm" style={{ opacity: ease((T - FINALE - 0.5) / 0.8) }}>
                <div className="text-2xl font-semibold">Power on</div>
                <p className="mt-1 text-sm text-white/60">Current from the solver, running on the finished copper. Brighter means more current.</p>
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] uppercase tracking-wide">
                  <span className="text-[#ffe2a8]">■ 5 V servo power</span>
                  <span className="text-[#7fe0ff]">■ 3.3 V logic</span>
                  <span className="text-[#9dffb4]">■ signals</span>
                  <span className="text-[#c8a6ff]">■ data</span>
                  <span className="text-[#6f9fe0]">■ ground return</span>
                </div>
              </div>
            )}
          </div>
          {!autoplay && outputsK > 0 && (
            <div className="grid grid-cols-2 gap-2 border-t border-white/[0.07] p-3 md:grid-cols-5" style={{ opacity: outputsK, transform: `translateY(${(1 - outputsK) * 16}px)` }}>
              {OUTPUTS.map((o) => (
                <div key={o.file} className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
                  <div className="text-sm font-medium">{o.name}</div>
                  <div className="truncate font-mono text-[10px] text-white/40">{o.file}</div>
                  <div className="font-mono text-[10px] uppercase text-emerald-300/80">{o.note}</div>
                </div>
              ))}
            </div>
          )}
        </section>

        <aside className="flex min-h-0 flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <h2 className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/40">Definition of done</h2>
            <span className="font-mono text-xs tabular-nums text-white/50">
              {written ? `${passed}/${written}` : ""}
            </span>
          </div>
          <ul className="flex min-h-0 flex-col gap-1.5 overflow-hidden">
            {CHECKS.map((c) => (
              <CheckRow key={c.id} c={c} T={T} />
            ))}
          </ul>
        </aside>
      </div>

      {!autoplay && (
        <footer className="border-t border-white/[0.07] px-5 py-3">
          <div className="relative h-8">
            <input
              aria-label="Scrub the run"
              type="range"
              min={0}
              max={END}
              step={0.1}
              value={T}
              onChange={(e) => setT(Number(e.target.value))}
              className="absolute inset-x-0 top-0 w-full accent-amber-200"
            />
            {ITERS.map((i) => (
              <button key={i.label} type="button" onClick={() => setT(i.t)} className={`absolute top-5 -translate-x-1/2 font-mono text-[10px] uppercase ${T >= i.t ? "text-amber-200" : "text-white/30"}`} style={{ left: `${(i.t / END) * 100}%` }}>
                {i.label}
              </button>
            ))}
          </div>
        </footer>
      )}
      {autoplay && <div className="h-1 bg-white/5"><div className="h-full bg-amber-200/60" style={{ width: `${clamp(T / END) * 100}%` }} /></div>}
    </div>
  );
}
