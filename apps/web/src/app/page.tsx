export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-[1120px] flex-1 flex-col px-6 py-10">
      <header className="flex items-baseline justify-between">
        <div className="font-serif text-2xl tracking-tight">
          Méyraki <span className="ms-2 font-mono text-xs uppercase tracking-[0.18em] text-graphite">Insight</span>
        </div>
        <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
          Preview · M0
        </span>
      </header>

      <section className="flex flex-1 flex-col justify-center py-24">
        <div className="dim-line mb-10">
          <span className="dim-label">Spatial intelligence · Hospitality</span>
          <span className="dim-rule" />
        </div>

        <h1 className="max-w-3xl font-serif text-5xl leading-[1.08] sm:text-6xl">
          From floorplan <span className="italic text-viridian">to revenue.</span>
        </h1>

        <p className="mt-8 max-w-xl text-[17px] text-graphite">
          Upload a floorplan, choose an objective, and a pipeline of specialized
          AI agents returns labeled zones, guest-flow heatmaps, optimized layout
          scenarios, and a client-ready report.
        </p>

        <div className="mt-12 flex items-center gap-4">
          <a
            href="/projects"
            className="rounded-sheet bg-ink px-6 py-3 text-[15px] font-medium text-paper transition-colors hover:bg-black"
          >
            Start an analysis
          </a>
          <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
            Pipeline online · 7 agents
          </span>
        </div>
      </section>

      <footer className="dim-line">
        <span className="dim-label">Méyraki studio</span>
        <span className="dim-rule" />
        <span className="dim-label">EN · AR</span>
      </footer>
    </main>
  );
}
