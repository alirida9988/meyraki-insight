"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
/** Pure fetch (no state) so an effect can call it without a lint escape hatch. */
const fetchProjects = (): Promise<Project[]> =>
  apiFetch("/projects").then((r) => r.json());

const apiFetch = async (path: string, init?: RequestInit) => {
  const r = await fetch(`${API}${path}`, { credentials: "include", ...init });
  if (r.status === 401) {
    // A dead cookie must never leave the page silently spinning or polling
    // forever against 401s (review m10). A hard navigation is deliberate here:
    // router.push() would keep this component — and its intervals and event
    // stream — alive with state belonging to a session that no longer exists.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = "/login";
    throw new Error("unauthenticated");
  }
  return r;
};

type Project = {
  id: string;
  name: string;
  client_name: string | null;
  space_type: string;
};

type StepInfo = { name: string; status: string };

type LayoutMove = { description: string; zone_ids: string[]; rationale: string };
type ScenarioOut = {
  id: string;
  name: string;
  moves: LayoutMove[];
  predicted_effects: Record<string, string>;
  confidence: number;
  solver_feasible: boolean;
  solver_notes: string[];
};
type MoodboardOut = {
  style_name: string;
  palette: string[];
  materials: string[];
  furniture_notes: string[];
  lighting_concept: string | null;
  image_keys: string[];
  render_provider: string | null;
  renders_are_draft: boolean;
};
type AnalysisSummary = {
  id: string;
  status: string;
  objectives: string[];
  report_language: string;
  created_at: string;
};

type Results = {
  scenarios: ScenarioOut[];
  moodboard: MoodboardOut | null;
  flowScore: number | null;
  reportReady: boolean;
  analysisId: string;
};

const SPACE_TYPES = ["hotel", "cafe", "restaurant", "coworking", "office", "clinic", "gallery", "other"];
const OBJECTIVES = [
  { id: "guest_flow", label: "Maximize guest flow" },
  { id: "seating_efficiency", label: "Seating efficiency" },
  { id: "revenue_per_sqm", label: "Revenue per sqm" },
  { id: "ambiance", label: "Ambiance" },
];

/** API `detail` payloads arrive as string | {errors: string[]} | pydantic array. */
function detailToMessages(detail: unknown): string[] {
  if (typeof detail === "string") return [detail];
  if (Array.isArray(detail)) {
    return detail.map((d) =>
      d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)
    );
  }
  if (detail && typeof detail === "object" && Array.isArray((detail as { errors?: unknown }).errors)) {
    return (detail as { errors: string[] }).errors;
  }
  return ["Something went wrong — please try again."];
}

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
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [selected, setSelected] = useState<Project | null>(null);
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [spaceType, setSpaceType] = useState("hotel");
  const [createError, setCreateError] = useState<string | null>(null);
  const [planId, setPlanId] = useState<string | null>(null);
  const [planName, setPlanName] = useState<string | null>(null);
  const [footfallId, setFootfallId] = useState<string | null>(null);
  const [footfallName, setFootfallName] = useState<string | null>(null);
  const [objectives, setObjectives] = useState<string[]>(["guest_flow"]);
  const [reportLanguage, setReportLanguage] = useState<"en" | "ar">("en");
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [feed, setFeed] = useState<string[]>([]);
  const [steps, setSteps] = useState<StepInfo[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [heatmapKey, setHeatmapKey] = useState<string | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [shareState, setShareState] = useState<{
    status: "idle" | "working" | "ready" | "error";
    message: string;
  }>({ status: "idle", message: "" });
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const esRef = useRef<EventSource | null>(null);
  // Guards async responses against project switches mid-flight (review finding #2).
  const selectedIdRef = useRef<string | null>(null);
  // Truth-poll while an analysis is busy — covers a permanently unreachable
  // event stream (review residual R2): the server, not the stream, is authoritative.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function applyProjects(list: Project[]) {
    setProjects(list);
    setLoadState("ready");
  }

  function handleLoadError(err: unknown) {
    // A 401 already navigated to /login; anything else is a real load failure.
    if ((err as Error)?.message !== "unauthenticated") setLoadState("error");
  }

  useEffect(() => {
    // setState lives in the async callbacks, never in the effect body, and the
    // cancelled flag keeps a late response from writing to an unmounted page.
    let cancelled = false;
    fetchProjects()
      .then((list) => {
        if (!cancelled) applyProjects(list);
      })
      .catch((err) => {
        if (!cancelled) handleLoadError(err);
      });
    return () => {
      cancelled = true;
      esRef.current?.close();
      stopPolling();
    };
  }, []);

  /** Populate the results panel from the server for any analysis id. */
  async function loadAnalysis(id: string, pid: string) {
    try {
      const detail = await apiFetch(`/analyses/${id}`).then((res) => res.json());
      if (selectedIdRef.current !== pid) return detail.status as string;
      const output = (name: string) =>
        detail.steps.find((s: { name: string }) => s.name === name)?.output;
      setStatus(detail.status);
      setSteps(detail.steps.map((s: StepInfo) => ({ name: s.name, status: s.status })));
      setAnalysisError(detail.error ?? null);
      setHeatmapKey(output("flow")?.heatmap_key ?? null);
      setResults({
        scenarios: output("layout")?.scenarios ?? [],
        moodboard: output("moodboard") ?? null,
        flowScore: output("business")?.flow_efficiency_score ?? null,
        reportReady: Boolean(output("report")?.report_key),
        analysisId: id,
      });
      return detail.status as string;
    } catch {
      return null; // transient — the stream, the poll, or a retry will resync
    }
  }

  async function shareReport(analysisId: string) {
    setShareState({ status: "working", message: "" });
    try {
      const r = await apiFetch(`/analyses/${analysisId}/share`, { method: "POST" });
      if (!r.ok) {
        // 503 means the server has no signing secret configured — say that rather than
        // "something went wrong", because it is an operator fix, not a user one.
        const detail = await r.json().catch(() => ({}));
        setShareState({
          status: "error",
          message: detail?.detail ?? `Could not create a link (HTTP ${r.status})`,
        });
        return;
      }
      const { url, expires_at } = await r.json();
      const expires = new Date(expires_at * 1000).toLocaleDateString();
      // The clipboard needs a user gesture and a secure context; over plain HTTP or in a
      // browser that refuses, the link is still shown so it can be copied by hand.
      try {
        await navigator.clipboard.writeText(url);
        setShareState({ status: "ready", message: `Copied — expires ${expires}. ${url}` });
      } catch {
        setShareState({ status: "ready", message: `Expires ${expires}. ${url}` });
      }
    } catch {
      setShareState({ status: "error", message: "Could not reach the server." });
    }
  }

  async function loadHistory(pid: string) {
    try {
      const rows = await apiFetch(`/projects/${pid}/analyses`).then((r) => r.json());
      if (selectedIdRef.current === pid) setHistory(rows);
    } catch {
      /* history is a convenience; never block the page on it */
    }
  }

  /** Reopen a past analysis — the whole point of the history list. */
  async function openAnalysis(summary: AnalysisSummary) {
    if (!selected) return;
    esRef.current?.close();
    stopPolling();
    setFeed([]);
    await loadAnalysis(summary.id, selected.id);
    if (summary.status === "running" || summary.status === "queued") {
      watchAnalysis(summary.id, selected.id); // still in flight: re-attach the live feed
    }
  }

  function selectProject(p: Project) {
    esRef.current?.close(); // never let another project's stream write here (finding #1)
    esRef.current = null;
    stopPolling();
    setSelected(p);
    selectedIdRef.current = p.id;
    setPlanId(null);
    setPlanName(null);
    setFootfallId(null);
    setFootfallName(null);
    setUploadErrors([]);
    setFeed([]);
    setSteps([]);
    setStatus(null);
    setHeatmapKey(null);
    setResults(null);
    setAnalysisError(null);
    setHistory([]);
    void loadHistory(p.id);
  }

  async function createProject(e: React.FormEvent) {
    e.preventDefault();
    setCreateError(null);
    try {
      const r = await apiFetch("/projects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, client_name: client || null, space_type: spaceType }),
      });
      if (!r.ok) {
        setCreateError(detailToMessages((await r.json()).detail).join(" "));
        return;
      }
      const p = await r.json();
      setProjects([p, ...projects]);
      selectProject(p);
      setName("");
      setClient("");
    } catch {
      setCreateError("Couldn't reach the server — is the API running?");
    }
  }

  async function upload(kind: "floorplan" | "footfall", file: File) {
    if (!selected) return;
    const pid = selected.id;
    setUploadErrors([]);
    const form = new FormData();
    form.append("file", file);
    let r: Response;
    let body: { id?: string; detail?: unknown };
    try {
      r = await apiFetch(`/projects/${pid}/uploads?kind=${kind}`, { method: "POST", body: form });
      body = await r.json();
    } catch {
      if (selectedIdRef.current === pid) setUploadErrors(["Upload failed — couldn't reach the server."]);
      return;
    }
    if (selectedIdRef.current !== pid) return; // user switched projects mid-upload
    if (!r.ok) {
      setUploadErrors(detailToMessages(body.detail));
      return;
    }
    if (kind === "floorplan") {
      setPlanId(body.id ?? null);
      setPlanName(file.name);
    } else {
      setFootfallId(body.id ?? null);
      setFootfallName(file.name);
    }
  }

  async function startAnalysis() {
    if (!selected || !planId) return;
    const pid = selected.id;
    esRef.current?.close(); // one live stream at a time (finding #4)
    setUploadErrors([]);
    setFeed([]);
    setSteps([]);
    setHeatmapKey(null);
    setResults(null);
    setAnalysisError(null);
    setStatus("queued");
    let r: Response;
    try {
      r = await apiFetch(`/projects/${pid}/analyses`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          floorplan_upload_id: planId,
          footfall_upload_id: footfallId,
          objectives,
          report_language: reportLanguage,
        }),
      });
    } catch {
      if (selectedIdRef.current === pid) {
        setStatus(null);
        setUploadErrors(["Couldn't reach the server — the analysis was not started."]);
      }
      return;
    }
    if (selectedIdRef.current !== pid) return;
    if (!r.ok) {
      setStatus(null);
      setUploadErrors(detailToMessages((await r.json()).detail));
      return;
    }
    const { id } = await r.json();
    setStatus("running");
    void loadHistory(pid);
    watchAnalysis(id, pid);
  }

  /** Live feed + truth-poll for an in-flight analysis. */
  function watchAnalysis(id: string, pid: string) {
    const sync = async () => {
      const status = await loadAnalysis(id, pid);
      if (status && ["done", "failed", "rejected"].includes(status)) {
        stopPolling();
        esRef.current?.close();
        void loadHistory(pid);
      }
    };

    // Safety net (residual R2): the server is the source of truth even if the
    // event stream never connects. Cheap poll, cleared on terminal status.
    stopPolling();
    pollRef.current = setInterval(() => void sync(), 5000);

    const es = new EventSource(`${API}/analyses/${id}/events`, { withCredentials: true });
    esRef.current = es;
    es.onmessage = (ev) => {
      const data = JSON.parse(ev.data) as { kind: string; message: string };
      if (selectedIdRef.current !== pid) return;
      setFeed((f) => [...f, data.message]);
      if (data.kind === "end") {
        es.close();
        void sync();
      }
    };
    es.onerror = () => {
      // EventSource auto-reconnects on transient errors; only when it has given up
      // do we resync the truth from the server — never guess "failed" (finding #3).
      if (es.readyState === EventSource.CLOSED) void sync();
    };
  }

  const busy = status === "queued" || status === "running";
  const inputCls =
    "w-full rounded-sheet border border-hairline bg-surface px-3 py-2 text-[15px] outline-none focus:border-viridian";

  return (
    <main className="mx-auto w-full max-w-[1120px] px-6 py-10">
      <header className="mb-10 flex items-baseline justify-between">
        <Link href="/" className="font-serif text-[28px] tracking-tight">
          Méyraki <span className="ms-2 font-mono text-xs uppercase tracking-[0.18em] text-graphite">Insight</span>
        </Link>
        <div className="flex items-center gap-4">
          <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">Projects</span>
          <button
            onClick={async () => {
              esRef.current?.close();
              stopPolling();
              await apiFetch("/auth/logout", { method: "POST" });
              router.push("/login");
            }}
            className="rounded-sheet border border-hairline px-3 py-1.5 text-sm text-graphite transition-colors hover:border-graphite"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="grid gap-10 md:grid-cols-[320px_1fr]">
        {/* left: project list + create */}
        <section>
          <DimLine label="Projects" right={loadState === "ready" ? String(projects.length) : "…"} />
          {loadState === "error" ? (
            <div className="mt-4 rounded-sheet border border-hairline bg-surface p-3 text-sm">
              <p className="text-graphite">Couldn&apos;t reach the server — your projects are safe, but we can&apos;t load them right now.</p>
              <button
                onClick={() => {
                  setLoadState("loading"); // an event handler is the right place for this
                  fetchProjects().then(applyProjects).catch(handleLoadError);
                }}
                className="mt-2 min-h-10 rounded-sheet border border-hairline px-3 py-1.5 text-sm hover:border-graphite"
              >
                Try again
              </button>
            </div>
          ) : loadState === "loading" ? (
            <p className="mt-4 text-sm text-graphite">Loading projects…</p>
          ) : (
            <ul className="mt-4 space-y-1">
              {projects.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => selectProject(p)}
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
          )}

          <form onSubmit={createProject} className="mt-8 space-y-3">
            <DimLine label="New project" />
            <input className={inputCls} placeholder="Project name" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} required />
            <input className={inputCls} placeholder="Client (optional)" value={client} maxLength={200} onChange={(e) => setClient(e.target.value)} />
            <select className={inputCls} value={spaceType} onChange={(e) => setSpaceType(e.target.value)}>
              {SPACE_TYPES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            {createError ? <p className="text-sm text-thermal-text">{createError}</p> : null}
            <button className="min-h-10 rounded-sheet bg-ink px-4 py-2 text-[15px] font-medium text-paper hover:bg-black">
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

              {history.length > 0 && (
                <div data-testid="analysis-history">
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                    Previous analyses
                  </span>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {history.map((h) => (
                      <button
                        key={h.id}
                        type="button"
                        onClick={() => void openAnalysis(h)}
                        className={`min-h-10 rounded-sheet border px-3 py-1.5 text-left font-mono text-xs transition-colors ${
                          results?.analysisId === h.id
                            ? "border-viridian bg-viridian-tint text-viridian"
                            : "border-hairline bg-surface hover:border-graphite"
                        }`}
                      >
                        {new Date(h.created_at).toLocaleString()} · {h.status}
                        {h.report_language === "ar" ? " · AR" : ""}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block">
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                    Floorplan (PNG · JPG · PDF){planName ? ` · ${planName} ✓` : ""}
                  </span>
                  <input
                    type="file"
                    accept=".png,.jpg,.jpeg,.pdf"
                    className="mt-2 block w-full text-sm"
                    onChange={(e) => {
                      const f = e.currentTarget.files?.[0];
                      e.currentTarget.value = ""; // same-file re-pick must re-fire (finding #7)
                      if (f) void upload("floorplan", f);
                    }}
                  />
                </label>
                <label className="block">
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                    Footfall CSV (optional){footfallName ? ` · ${footfallName} ✓` : ""}
                  </span>
                  <input
                    type="file"
                    accept=".csv"
                    className="mt-2 block w-full text-sm"
                    onChange={(e) => {
                      const f = e.currentTarget.files?.[0];
                      e.currentTarget.value = "";
                      if (f) void upload("footfall", f);
                    }}
                  />
                </label>
              </div>

              {uploadErrors.length > 0 && (
                <ul className="rounded-sheet border border-thermal/40 bg-surface p-3 text-sm text-thermal-text">
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
                        className={`min-h-10 rounded-sheet border px-3 py-1.5 text-sm transition-colors ${
                          on ? "border-viridian bg-viridian-tint text-viridian" : "border-hairline bg-surface"
                        }`}
                      >
                        {o.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">Report language</span>
                <div className="mt-2 flex gap-2">
                  {(["en", "ar"] as const).map((lang) => (
                    <button
                      key={lang}
                      type="button"
                      data-testid={`report-lang-${lang}`}
                      onClick={() => setReportLanguage(lang)}
                      className={`min-h-10 rounded-sheet border px-3 py-1.5 text-sm transition-colors ${
                        reportLanguage === lang
                          ? "border-viridian bg-viridian-tint text-viridian"
                          : "border-hairline bg-surface"
                      }`}
                    >
                      {lang === "en" ? "English" : "العربية"}
                    </button>
                  ))}
                </div>
              </div>

              <button
                onClick={startAnalysis}
                disabled={!planId || objectives.length === 0 || busy}
                className="min-h-10 rounded-sheet bg-ink px-6 py-3 text-[15px] font-medium text-paper transition-colors hover:bg-black disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "Analyzing…" : "Generate insights"}
              </button>

              {(status === "rejected" || status === "failed") && analysisError && (
                <p
                  data-testid="analysis-error"
                  className="rounded-sheet border border-thermal/40 bg-surface p-3 text-sm text-thermal-text"
                >
                  {analysisError}
                </p>
              )}

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
                          : "border-thermal text-thermal-text"
                      }`}
                    >
                      {s.name} · {s.status}
                    </span>
                  ))}
                </div>
              )}

              {heatmapKey && results && (
                <div>
                  <DimLine label="Flow heatmap" right="cool → hot" />
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={`${API}/analyses/${results.analysisId}/files/${heatmapKey}`}
                    alt="Guest-flow heatmap over the uploaded floorplan"
                    className="mt-3 w-full rounded-sheet border border-hairline"
                  />
                </div>
              )}

              {results && results.flowScore !== null && (
                <div data-testid="flow-score">
                  <DimLine label="Flow efficiency score" right="0 – 100" />
                  <p className="mt-3 font-serif text-[40px] leading-none text-viridian">
                    {results.flowScore}
                  </p>
                  <p className="mt-1 text-sm text-graphite">
                    Area-weighted flow intensity across guest-facing zones. Assumptions
                    ship with the report.
                  </p>
                </div>
              )}

              {results && results.scenarios.length > 0 && (
                <div data-testid="scenarios">
                  <DimLine label="Layout scenarios" right={`${results.scenarios.length}`} />
                  <div className="mt-3 space-y-3">
                    {results.scenarios.map((s) => (
                      <div key={s.id} className="rounded-sheet border border-hairline bg-surface p-4">
                        <div className="flex items-baseline justify-between gap-3">
                          <h3 className="font-medium">{s.name}</h3>
                          <span className="whitespace-nowrap font-mono text-xs uppercase text-graphite">
                            confidence {Math.round(s.confidence * 100)}%
                          </span>
                        </div>
                        <p
                          data-testid="solver-verdict"
                          className={`mt-1 font-mono text-[11px] uppercase tracking-[0.08em] ${
                            s.solver_feasible ? "text-viridian" : "text-thermal-text"
                          }`}
                          title={(s.solver_notes ?? []).join("\n")}
                        >
                          {s.solver_feasible
                            ? "✓ fits the floor area (constraint solver)"
                            : "✕ does not fit as proposed (constraint solver)"}
                        </p>
                        <ul className="mt-2 space-y-2">
                          {s.moves.map((m, i) => (
                            <li key={i} className="text-sm">
                              <span className="font-medium">{m.description}</span>
                              <span className="text-graphite"> — {m.rationale}</span>
                            </li>
                          ))}
                        </ul>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {Object.entries(s.predicted_effects).map(([k, v]) => (
                            <span
                              key={k}
                              className="rounded-sheet border border-viridian px-2 py-1 font-mono text-xs text-viridian"
                            >
                              {k}: {v}
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {results?.reportReady && (
                <a
                  data-testid="report-download"
                  href={`${API}/analyses/${results.analysisId}/report.pdf`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block min-h-10 rounded-sheet bg-viridian px-6 py-3 text-[15px] font-medium text-paper transition-opacity hover:opacity-90"
                >
                  Download insight report (PDF)
                </a>
              )}

              {results?.reportReady && (
                <div className="mt-3">
                  <button
                    type="button"
                    data-testid="share-report"
                    onClick={() => void shareReport(results.analysisId)}
                    className="min-h-10 rounded-sheet border border-hairline px-5 py-2.5 text-[14px] transition-colors hover:border-viridian"
                  >
                    {shareState.status === "working" ? "Creating link…" : "Copy client link"}
                  </button>
                  {shareState.status === "ready" && (
                    <p
                      data-testid="share-link"
                      className="mt-2 break-all font-mono text-[11px] text-graphite"
                    >
                      {shareState.message}
                    </p>
                  )}
                  {shareState.status === "error" && (
                    <p
                      data-testid="share-error"
                      className="mt-2 font-mono text-[11px] text-thermal"
                    >
                      {shareState.message}
                    </p>
                  )}
                </div>
              )}

              {results?.moodboard && (
                <div data-testid="moodboard">
                  <DimLine label="Moodboard" right={results.moodboard.style_name} />
                  {(results.moodboard.image_keys ?? []).length > 0 && (
                    <div className="mt-3 grid grid-cols-3 gap-2">
                      {results.moodboard.image_keys.map((key) => (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img
                          key={key}
                          data-testid="moodboard-render"
                          src={`${API}/analyses/${results.analysisId}/files/${key}`}
                          alt={`${results.moodboard!.style_name} interior render`}
                          className="aspect-square w-full rounded-sheet border border-hairline object-cover"
                        />
                      ))}
                    </div>
                  )}
                  {results.moodboard.renders_are_draft && (
                    <p
                      data-testid="moodboard-draft-note"
                      className="mt-2 font-mono text-[11px] uppercase tracking-wide text-graphite"
                    >
                      Draft renders{results.moodboard.render_provider
                        ? ` · ${results.moodboard.render_provider}`
                        : ""}{" "}
                      — mood and material direction, not final visuals
                    </p>
                  )}
                  <div className="mt-3 flex gap-2">
                    {results.moodboard.palette.map((hex) => (
                      <div key={hex} className="flex-1">
                        <div
                          className="h-14 rounded-sheet border border-hairline"
                          style={{ backgroundColor: hex }}
                        />
                        <p className="mt-1 font-mono text-[11px] uppercase text-graphite">{hex}</p>
                      </div>
                    ))}
                  </div>
                  <p className="mt-3 text-sm">
                    <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">Materials · </span>
                    {results.moodboard.materials.join(", ")}
                  </p>
                  {results.moodboard.lighting_concept && (
                    <p className="mt-1 text-sm text-graphite">{results.moodboard.lighting_concept}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
