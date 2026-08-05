"use client";

import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Project = {
  id: string;
  name: string;
  client_name: string | null;
  space_type: string;
};

type StepInfo = { name: string; status: string };

const SPACE_TYPES = ["hotel", "cafe", "restaurant", "coworking", "office", "clinic", "gallery", "other"];
const OBJECTIVES = [
  { id: "guest_flow", label: "Maximize guest flow" },
  { id: "seating_efficiency", label: "Seating efficiency" },
  { id: "revenue_per_sqm", label: "Revenue per sqm" },
  { id: "ambiance", label: "Ambiance" },
];

function DimLine({ label, right }: { label: string; right?: string }) {
  return (
    <div className="dim-line">
      <span className="dim-label">{label}</span>
      <span className="dim-rule" />
      {right ? <span className="dim-label">{right}</span> : null}
    </div>
  );
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [spaceType, setSpaceType] = useState("hotel");
  const [planId, setPlanId] = useState<string | null>(null);
  const [footfallId, setFootfallId] = useState<string | null>(null);
  const [objectives, setObjectives] = useState<string[]>(["guest_flow"]);
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [feed, setFeed] = useState<string[]>([]);
  const [steps, setSteps] = useState<StepInfo[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    fetch(`${API}/projects`).then((r) => r.json()).then(setProjects).catch(() => setProjects([]));
    return () => esRef.current?.close();
  }, []);

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    const r = await fetch(`${API}/projects`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, client_name: client || null, space_type: spaceType }),
    });
    if (r.ok) {
      const p = await r.json();
      setProjects([p, ...projects]);
      setSelected(p);
      setName("");
      setClient("");
    }
  }

  async function upload(kind: "floorplan" | "footfall", file: File) {
    if (!selected) return;
    setUploadErrors([]);
    const form = new FormData();
    form.append("file", file);
    const r = await fetch(`${API}/projects/${selected.id}/uploads?kind=${kind}`, {
      method: "POST",
      body: form,
    });
    const body = await r.json();
    if (!r.ok) {
      const detail = body.detail;
      setUploadErrors(Array.isArray(detail?.errors) ? detail.errors : [String(detail)]);
      return;
    }
    if (kind === "floorplan") setPlanId(body.id);
    else setFootfallId(body.id);
  }

  async function startAnalysis() {
    if (!selected || !planId) return;
    setFeed([]);
    setSteps([]);
    setStatus("queued");
    const r = await fetch(`${API}/projects/${selected.id}/analyses`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        floorplan_upload_id: planId,
        footfall_upload_id: footfallId,
        objectives,
      }),
    });
    if (!r.ok) {
      setStatus(null);
      setUploadErrors([String((await r.json()).detail)]);
      return;
    }
    const { id } = await r.json();
    setStatus("running");
    const es = new EventSource(`${API}/analyses/${id}/events`);
    esRef.current = es;
    es.onmessage = async (ev) => {
      const data = JSON.parse(ev.data) as { kind: string; message: string };
      setFeed((f) => [...f, data.message]);
      if (data.kind === "end") {
        es.close();
        setStatus(data.message);
        const detail = await fetch(`${API}/analyses/${id}`).then((r) => r.json());
        setSteps(detail.steps.map((s: StepInfo) => ({ name: s.name, status: s.status })));
      }
    };
    es.onerror = () => {
      es.close();
      setStatus("failed");
    };
  }

  const inputCls =
    "w-full rounded-sheet border border-hairline bg-surface px-3 py-2 text-[15px] outline-none focus:border-viridian";

  return (
    <main className="mx-auto w-full max-w-[1120px] px-6 py-10">
      <header className="mb-10 flex items-baseline justify-between">
        <a href="/" className="font-serif text-2xl tracking-tight">
          Méyraki <span className="ms-2 font-mono text-xs uppercase tracking-[0.18em] text-graphite">Insight</span>
        </a>
        <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">Projects</span>
      </header>

      <div className="grid gap-10 md:grid-cols-[320px_1fr]">
        {/* left: project list + create */}
        <section>
          <DimLine label="Projects" right={String(projects.length)} />
          <ul className="mt-4 space-y-1">
            {projects.map((p) => (
              <li key={p.id}>
                <button
                  onClick={() => {
                    setSelected(p);
                    setPlanId(null);
                    setFootfallId(null);
                    setFeed([]);
                    setSteps([]);
                    setStatus(null);
                  }}
                  className={`w-full rounded-sheet border px-3 py-2 text-left text-[15px] transition-colors ${
                    selected?.id === p.id
                      ? "border-viridian bg-viridian-tint"
                      : "border-hairline bg-surface hover:border-graphite"
                  }`}
                >
                  <span className="font-medium">{p.name}</span>
                  <span className="ms-2 font-mono text-xs uppercase text-graphite">{p.space_type}</span>
                </button>
              </li>
            ))}
          </ul>

          <form onSubmit={createProject} className="mt-8 space-y-3">
            <DimLine label="New project" />
            <input className={inputCls} placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} required />
            <input className={inputCls} placeholder="Client (optional)" value={client} onChange={(e) => setClient(e.target.value)} />
            <select className={inputCls} value={spaceType} onChange={(e) => setSpaceType(e.target.value)}>
              {SPACE_TYPES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <button className="rounded-sheet bg-ink px-4 py-2 text-[15px] font-medium text-paper hover:bg-black">
              Create project
            </button>
          </form>
        </section>

        {/* right: analysis flow */}
        <section>
          {!selected ? (
            <div className="flex h-full min-h-64 items-center justify-center rounded-sheet border border-hairline">
              <p className="text-graphite">Select or create a project to start an analysis.</p>
            </div>
          ) : (
            <div className="space-y-8">
              <DimLine label={`Analysis · ${selected.name}`} right={status ?? "not started"} />

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                    Floorplan (PNG · JPG · PDF) {planId ? "· uploaded" : ""}
                  </span>
                  <input
                    type="file"
                    accept=".png,.jpg,.jpeg,.pdf"
                    className="mt-2 block w-full text-sm"
                    onChange={(e) => e.target.files?.[0] && upload("floorplan", e.target.files[0])}
                  />
                </label>
                <label className="block">
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                    Footfall CSV (optional) {footfallId ? "· uploaded" : ""}
                  </span>
                  <input
                    type="file"
                    accept=".csv"
                    className="mt-2 block w-full text-sm"
                    onChange={(e) => e.target.files?.[0] && upload("footfall", e.target.files[0])}
                  />
                </label>
              </div>

              {uploadErrors.length > 0 && (
                <ul className="rounded-sheet border border-thermal/40 bg-surface p-3 text-sm text-thermal">
                  {uploadErrors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              )}

              <div>
                <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">Objectives</span>
                <div className="mt-2 flex flex-wrap gap-2">
                  {OBJECTIVES.map((o) => {
                    const on = objectives.includes(o.id);
                    return (
                      <button
                        key={o.id}
                        type="button"
                        onClick={() =>
                          setObjectives(on ? objectives.filter((x) => x !== o.id) : [...objectives, o.id])
                        }
                        className={`rounded-sheet border px-3 py-1.5 text-sm transition-colors ${
                          on ? "border-viridian bg-viridian-tint text-viridian" : "border-hairline bg-surface"
                        }`}
                      >
                        {o.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <button
                onClick={startAnalysis}
                disabled={!planId || objectives.length === 0 || status === "running"}
                className="rounded-sheet bg-ink px-6 py-3 text-[15px] font-medium text-paper transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-40"
              >
                {status === "running" ? "Analyzing…" : "Generate insights"}
              </button>

              {feed.length > 0 && (
                <div>
                  <DimLine label="Pipeline register" right={status ?? ""} />
                  <ul className="mt-3 space-y-1 font-mono text-[13px] text-graphite">
                    {feed.map((m, i) => (
                      <li key={i} className="border-b border-hairline pb-1">
                        {m}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {steps.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {steps.map((s) => (
                    <span
                      key={s.name}
                      className={`rounded-sheet border px-2 py-1 font-mono text-xs uppercase ${
                        s.status === "done"
                          ? "border-viridian text-viridian"
                          : "border-thermal text-thermal"
                      }`}
                    >
                      {s.name} · {s.status}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
