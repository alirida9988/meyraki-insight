import Link from "next/link";

/** The three things a visitor needs before they will upload a client's floorplan:
 *  what it does, what they get back, and what it will ask of them. */
const STEPS = [
  {
    n: "01",
    title: "Upload a plan",
    body: "PNG, JPG or a PDF export. Architect drawings and scans both work.",
  },
  {
    n: "02",
    title: "Choose the decision to improve",
    body: "Guest flow, seating efficiency, revenue per sqm, or ambiance.",
  },
  {
    n: "03",
    title: "Receive validated recommendations",
    body: "Zones, circulation, layout scenarios and a client-ready report.",
  },
];

/** Capabilities that exist today. Nothing aspirational belongs on this row — it is the
 *  line a prospective client will quote back, and the product has to survive the quote. */
const CAPABILITIES = ["Flow analysis", "Constraint-checked scenarios", "Client-ready reports"];

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-[1120px] flex-1 flex-col px-6 py-10">
      <header className="flex items-baseline justify-between">
        <div className="font-serif text-[28px] tracking-tight">
          Méyraki{" "}
          <span className="ms-2 font-mono text-xs uppercase tracking-[0.18em] text-graphite">
            Insight
          </span>
        </div>
        <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
          Spatial intelligence
        </span>
      </header>

      <section className="flex flex-1 flex-col justify-center py-16 sm:py-24">
        <div className="dim-line mb-10">
          <span className="dim-label">For hospitality architects &amp; interior designers</span>
          <span className="dim-rule" />
        </div>

        <h1 className="max-w-4xl font-serif text-[34px] leading-[1.1] sm:text-[52px]">
          Turn a floorplan into clear{" "}
          <span className="italic text-viridian">operational and revenue decisions.</span>
        </h1>

        <p className="mt-8 max-w-2xl text-[17px] text-graphite">
          Meyraki reads an architectural drawing, simulates how guests will actually move
          through the space, and proposes layout changes a constraint solver has checked
          will physically fit — delivered as a branded report in English or Arabic.
        </p>

        <ol className="mt-14 grid gap-px overflow-hidden rounded-sheet border border-hairline bg-hairline sm:grid-cols-3">
          {STEPS.map((s) => (
            <li key={s.n} className="bg-surface p-6">
              <span className="font-mono text-xs uppercase tracking-[0.18em] text-viridian">
                {s.n}
              </span>
              <h2 className="mt-3 font-serif text-[20px] leading-tight">{s.title}</h2>
              <p className="mt-2 text-[14px] leading-relaxed text-graphite">{s.body}</p>
            </li>
          ))}
        </ol>

        <div className="mt-12 flex flex-wrap items-center gap-x-6 gap-y-4">
          <Link
            href="/projects"
            className="min-h-10 rounded-sheet bg-ink px-6 py-3 text-[15px] font-medium text-paper transition-colors duration-200 hover:bg-black"
          >
            Start an analysis
          </Link>
          <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
            {CAPABILITIES.map((c) => (
              <li
                key={c}
                className="font-mono text-xs uppercase tracking-[0.08em] text-graphite"
              >
                {c}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <footer className="dim-line">
        <span className="dim-label">Méyraki studio</span>
        <span className="dim-rule" />
        <span className="dim-label">MENA-first</span>
      </footer>
    </main>
  );
}
