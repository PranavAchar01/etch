import Link from "next/link";
import Studio from "@/components/Studio";

const STEPS = [
  { n: "01", title: "Say what you need", body: "Pick a board type and fill in one sentence: what it does, how it is powered, where it has to fit." },
  { n: "02", title: "Agents write the finish line", body: "Your sentence becomes checks a machine can run: electrical rules, fab rules, a rail simulation, trace heating, parts in stock." },
  { n: "03", title: "The loop runs until every check passes", body: "Each pass adds a part or fixes a failure, and says why in one line. Drift gets caught and rolled back. You can walk away." },
];

const CHECKS = ["KiCad ERC and DRC", "JLCPCB fab rules", "ngspice rail simulation", "IPC-2152 trace heating", "LCSC stock", "DC current flow"];

export default function Home() {
  return (
    <main className="flex flex-col">
      <nav className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-5">
        <span className="font-mono text-sm uppercase tracking-[0.4em] text-amber-200">Etch</span>
        <Link href="/build" className="rounded-lg border border-white/15 px-4 py-2 text-sm text-white/80 hover:border-white/40">
          Open the studio
        </Link>
      </nav>

      <section className="mx-auto w-full max-w-6xl px-6 pb-10 pt-10">
        <div className="font-mono text-xs uppercase tracking-[0.4em] text-white/40">Agents that know when a board is done</div>
        <h1 className="mt-4 max-w-4xl text-5xl font-semibold leading-[1.05] tracking-tight md:text-7xl">
          Describe a circuit board.
          <br />
          <span className="text-amber-200">Get one you can order.</span>
        </h1>
        <p className="mt-6 max-w-2xl text-lg text-white/60">
          A team of Nemotron agents turns one sentence into a checked, manufacturable PCB. They write their own definition of done, then design, verify and fix until every check passes.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/build" className="rounded-xl bg-amber-200 px-6 py-3 text-base font-semibold text-black hover:bg-amber-100">
            Build a board →
          </Link>
          <span className="rounded-xl border border-white/10 px-5 py-3 font-mono text-xs uppercase leading-6 tracking-widest text-white/45">Nemotron 3 on Crusoe Managed Inference</span>
        </div>
      </section>

      <section className="mx-auto w-full max-w-6xl px-6">
        <div className="flex h-[560px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0a0b0e]">
          <Studio autoplay />
        </div>
      </section>

      <section className="mx-auto grid w-full max-w-6xl gap-4 px-6 py-16 md:grid-cols-3">
        {STEPS.map((s) => (
          <div key={s.n} className="rounded-2xl border border-white/10 p-6">
            <div className="font-mono text-xs text-amber-200">{s.n}</div>
            <div className="mt-3 text-xl font-semibold">{s.title}</div>
            <p className="mt-2 text-sm leading-relaxed text-white/55">{s.body}</p>
          </div>
        ))}
      </section>

      <section className="mx-auto w-full max-w-6xl px-6 pb-20">
        <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-white/40">Checked by real engineering tools, not by the model grading itself</div>
        <div className="mt-4 flex flex-wrap gap-2">
          {CHECKS.map((c) => (
            <span key={c} className="rounded-full border border-white/10 px-4 py-2 text-sm text-white/70">
              {c}
            </span>
          ))}
        </div>
      </section>

      <footer className="border-t border-white/[0.07] px-6 py-6 text-center font-mono text-[10px] uppercase tracking-widest text-white/30">Built at the Recursive Agents Hackathon · Crusoe × Cerebral Valley</footer>
    </main>
  );
}
