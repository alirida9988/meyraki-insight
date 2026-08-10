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
/** `label` is also the button's accessible name, so it is the string the browser tests
 *  assert on — the outcome line is attached with aria-describedby rather than folded into
 *  the name, which keeps the control readable to a screen reader without changing what it
 *  is called. */
const OBJECTIVES = [
  {
    id: "guest_flow",
    label: "Maximize guest flow",
    outcome: "Reduce congestion and improve movement",
  },
  {
    id: "seating_efficiency",
    label: "Seating efficiency",
    outcome: "Find capacity without harming comfort",
  },
  {
    id: "revenue_per_sqm",
    label: "Revenue per sqm",
    outcome: "Prioritise the highest-value use of space",
  },
  {
    id: "ambiance",
    label: "Ambiance",
    outcome: "Improve guest experience and visual direction",
  },
];

/** The pipeline's step names are internal. These are what a designer would call them.
 *  Any step without an entry falls back to its own name rather than inventing one. */
const STEP_COPY: Record<string, string> = {
  intake: "Reading floorplan",
  routing: "Planning the analysis",
  zones: "Mapping zones",
  flow: "Simulating circulation",
  layout: "Testing layout options",
  moodboard: "Composing design direction",
  business: "Calculating impact",
  report: "Preparing the report",
  qa: "Checking consistency",
};

const MAX_UPLOAD_MB = 25;

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

/** A file field that looks like a place to put a plan rather than a form control.
 *
 *  The native <input type="file"> is kept in the DOM and merely visually hidden: it is what
 *  the browser tests drive, it is what makes the whole zone keyboard-operable through its
 *  label, and re-implementing a file picker to look prettier would trade all of that for
 *  nothing. Drag-and-drop is added on top with native HTML5 events, so no dependency.
 */
function UploadZone({
  id,
  accept,
  label,
  hint,
  fileName,
  optional,
  onFile,
}: {
  id: string;
  accept: string;
  label: string;
  hint: string;
  fileName: string | null;
  optional?: boolean;
  onFile: (f: File) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFile(f);
      }}
      // focus-within earns its place here: the <input> is visually hidden so the zone can
      // be styled, which means a keyboard user's focus ring would otherwise land on
      // something invisible. The zone adopts the ring on their behalf.
      className={`rounded-sheet border border-dashed p-4 transition-colors duration-200 focus-within:border-viridian focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-viridian ${
        dragging
          ? "border-viridian bg-viridian-tint"
          : fileName
            ? "border-viridian/50 bg-surface"
            : "border-hairline bg-surface hover:border-graphite"
      }`}
    >
      <label htmlFor={id} className="block cursor-pointer">
        <span className="flex items-baseline justify-between gap-2">
          <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
            {label}
          </span>
          {optional ? (
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-graphite">
              Optional
            </span>
          ) : null}
        </span>

        {fileName ? (
          <span className="reveal mt-2 flex items-center gap-2 text-[15px]">
            {/* The tick is part of the text on purpose: status must never be carried by
                colour alone. */}
            <span className="font-medium text-viridian">{fileName} ✓</span>
          </span>
        ) : (
          <span className="mt-2 block text-[15px] text-graphite">
            Drop a file here, or{" "}
            <span className="text-ink underline underline-offset-4">choose one</span>
          </span>
        )}

        <span className="mt-1 block font-mono text-[11px] text-graphite">{hint}</span>
      </label>

      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(e) => {
          const f = e.currentTarget.files?.[0];
          e.currentTarget.value = ""; // same-file re-pick must re-fire (finding #7)
          if (f) onFile(f);
        }}
      />

      {fileName ? (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-3 rounded-sheet border border-hairline px-3 py-1.5 text-[13px] text-graphite transition-colors duration-200 hover:border-graphite hover:text-ink"
        >
          Replace file
        </button>
      ) : null}
    </div>
  );
}

/** The analysis timeline. Reads the real step list — no invented percentage, because the
 *  API does not report one and a fabricated bar is worse than an honest list. */
function Timeline({ steps, status }: { steps: StepInfo[]; status: string | null }) {
  const activeIndex = steps.findIndex((s) => s.status === "running" || s.status === "pending");
  return (
    <ol className="mt-3 space-y-0">
      {steps.map((s, i) => {
        const done = s.status === "done";
        const failed = s.status === "failed";
        const active = i === activeIndex && (status === "running" || status === "queued");
        return (
          <li
            key={s.name}
            className={`flex items-start gap-3 border-b border-hairline py-2 last:border-b-0 ${
              done ? "step-settle" : ""
            }`}
          >
            <span
              aria-hidden="true"
              className={`mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full transition-colors duration-200 ${
                failed
                  ? "bg-thermal"
                  : done
                    ? "bg-viridian"
                    : active
                      ? "bg-ink"
                      : "bg-hairline"
              }`}
            />
            <span className="flex-1">
              <span
                className={`text-[15px] ${done || active ? "text-ink" : "text-graphite"}`}
              >
                {STEP_COPY[s.name] ?? s.name}
              </span>
              {/* Kept verbatim — "qa · done" is the machine-readable register the browser
                  tests and the founder's demo both read. */}
              <span className="ms-2 font-mono text-xs uppercase text-graphite">
                {s.name} · {s.status}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
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

  // Ready to hand over: everything the results panel needs has arrived.
  const finished = status === "done";
  const best = results?.scenarios?.length
    ? [...results.scenarios].sort(
        (a, b) =>
          Number(b.solver_feasible) - Number(a.solver_feasible) || b.confidence - a.confidence,
      )[0]
    : null;

  return (
    <main className="mx-auto w-full max-w-[1240px] px-4 py-8 sm:px-6 sm:py-10">
      <header className="mb-8 flex flex-wrap items-baseline justify-between gap-4">
        <Link href="/" className="font-serif text-[28px] tracking-tight">
          Méyraki{" "}
          <span className="ms-2 font-mono text-xs uppercase tracking-[0.18em] text-graphite">
            Insight
          </span>
        </Link>
        <div className="flex items-center gap-4">
          <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
            Projects
          </span>
          <button
            onClick={async () => {
              esRef.current?.close();
              stopPolling();
              await apiFetch("/auth/logout", { method: "POST" });
              router.push("/login");
            }}
            className="rounded-sheet border border-hairline px-3 py-1.5 text-sm text-graphite transition-colors duration-200 hover:border-graphite hover:text-ink"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="grid gap-8 lg:grid-cols-[300px_1fr] lg:gap-10">
        {/* ---------------------------------------------------------------- left rail */}
        <aside className="lg:sticky lg:top-8 lg:self-start">
          <DimLine label="Projects" right={loadState === "ready" ? String(projects.length) : "…"} />

          {loadState === "error" ? (
            <div className="mt-4 rounded-sheet border border-hairline bg-surface p-3 text-sm">
              <p className="text-graphite">
                Couldn&apos;t reach the server — your projects are safe, but we can&apos;t load
                them right now.
              </p>
              <button
                onClick={() => {
                  setLoadState("loading"); // an event handler is the right place for this
                  fetchProjects().then(applyProjects).catch(handleLoadError);
                }}
                className="mt-2 min-h-10 rounded-sheet border border-hairline px-3 py-1.5 text-sm transition-colors duration-200 hover:border-graphite"
              >
                Try again
              </button>
            </div>
          ) : loadState === "loading" ? (
            <div className="mt-4 space-y-2" aria-busy="true" aria-live="polite">
              <span className="sr-only">Loading projects…</span>
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton h-10 w-full" />
              ))}
            </div>
          ) : projects.length === 0 ? (
            <p className="mt-4 text-sm text-graphite">
              No projects yet. Create one below to begin.
            </p>
          ) : (
            <ul className="mt-4 space-y-1">
              {projects.map((p) => (
                <li key={p.id}>
                  <button
                    onClick={() => selectProject(p)}
                    aria-current={selected?.id === p.id ? "true" : undefined}
                    className={`w-full rounded-sheet border px-3 py-2 text-left text-[15px] transition-colors duration-200 ${
                      selected?.id === p.id
                        ? "border-viridian bg-viridian-tint"
                        : "border-hairline bg-surface hover:border-graphite"
                    }`}
                  >
                    <span className="font-medium">{p.name}</span>
                    <span className="ms-2 font-mono text-xs uppercase text-graphite">
                      {p.space_type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={createProject} className="mt-8 space-y-3">
            <DimLine label="New project" />
            <input
              className={inputCls}
              placeholder="Project name"
              value={name}
              maxLength={200}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <input
              className={inputCls}
              placeholder="Client (optional)"
              value={client}
              maxLength={200}
              onChange={(e) => setClient(e.target.value)}
            />
            <label className="block">
              <span className="sr-only">Space type</span>
              <select
                className={inputCls}
                value={spaceType}
                onChange={(e) => setSpaceType(e.target.value)}
              >
                {SPACE_TYPES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            {createError ? <p className="text-sm text-thermal-text">{createError}</p> : null}
            <button className="min-h-10 rounded-sheet bg-ink px-4 py-2 text-[15px] font-medium text-paper transition-colors duration-200 hover:bg-black">
              Create project
            </button>
          </form>

          {/* History belongs beside the projects it describes, not inside the workspace. */}
          {selected && history.length > 0 && (
            <div data-testid="analysis-history" className="mt-8">
              <DimLine label="Previous analyses" right={String(history.length)} />
              <ul className="mt-3 space-y-1">
                {history.map((h) => (
                  <li key={h.id}>
                    <button
                      type="button"
                      onClick={() => void openAnalysis(h)}
                      aria-current={results?.analysisId === h.id ? "true" : undefined}
                      className={`w-full rounded-sheet border px-3 py-2 text-left transition-colors duration-200 ${
                        results?.analysisId === h.id
                          ? "border-viridian bg-viridian-tint"
                          : "border-hairline bg-surface hover:border-graphite"
                      }`}
                    >
                      <span className="block font-mono text-[11px] uppercase tracking-[0.06em] text-graphite">
                        {new Date(h.created_at).toLocaleString()}
                      </span>
                      <span className="mt-0.5 block font-mono text-xs">
                        {h.status}
                        {h.report_language === "ar" ? " · AR" : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </aside>

        {/* ---------------------------------------------------------------- workspace */}
        <section>
          {!selected ? (
            <div className="rounded-sheet border border-hairline bg-surface p-8 sm:p-12">
              <div className="dim-line mb-6">
                <span className="dim-label">Workspace</span>
                <span className="dim-rule" />
              </div>
              <h2 className="max-w-lg font-serif text-[26px] leading-tight sm:text-[30px]">
                Select a project, or create one, to start an analysis.
              </h2>
              <p className="mt-4 max-w-lg text-[15px] text-graphite">
                Each project holds one venue and every analysis you run against it. You will
                upload a floorplan, choose which decisions to improve, and receive zones, a
                circulation heatmap, layout scenarios and a client-ready report.
              </p>
            </div>
          ) : (
            <div className="space-y-8">
              <DimLine label={`Analysis · ${selected.name}`} right={status ?? "not started"} />

              {/* ------------------------------------------------------ 1 · the inputs */}
              {!finished && (
                <div className="space-y-6">
                  {!planId && !busy && (
                    <p className="max-w-2xl text-[15px] text-graphite">
                      Start by uploading the venue&apos;s floorplan. Footfall data is optional —
                      without it, circulation is simulated rather than measured, and the report
                      says which was used.
                    </p>
                  )}

                  <div className="grid gap-4 sm:grid-cols-2">
                    <UploadZone
                      id="floorplan-upload"
                      accept=".png,.jpg,.jpeg,.pdf"
                      label="Floorplan"
                      hint={`PNG · JPG · PDF, up to ${MAX_UPLOAD_MB} MB`}
                      fileName={planName}
                      onFile={(f) => void upload("floorplan", f)}
                    />
                    <UploadZone
                      id="footfall-upload"
                      accept=".csv"
                      label="Footfall data"
                      hint="CSV · zone_name, timestamp, traffic_count"
                      fileName={footfallName}
                      optional
                      onFile={(f) => void upload("footfall", f)}
                    />
                  </div>

                  {uploadErrors.length > 0 && (
                    <ul
                      role="alert"
                      className="reveal rounded-sheet border border-thermal/40 bg-surface p-3 text-sm text-thermal-text"
                    >
                      {uploadErrors.map((e, i) => (
                        <li key={i}>{e}</li>
                      ))}
                    </ul>
                  )}

                  <div>
                    <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                      What should this analysis improve?
                    </span>
                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                      {OBJECTIVES.map((o) => {
                        const on = objectives.includes(o.id);
                        return (
                          <button
                            key={o.id}
                            type="button"
                            aria-label={o.label}
                            aria-describedby={`obj-${o.id}-outcome`}
                            aria-pressed={on}
                            onClick={() =>
                              setObjectives(
                                on ? objectives.filter((x) => x !== o.id) : [...objectives, o.id],
                              )
                            }
                            className={`rounded-sheet border p-3 text-left transition-colors duration-200 ${
                              on
                                ? "border-viridian bg-viridian-tint"
                                : "border-hairline bg-surface hover:border-graphite"
                            }`}
                          >
                            <span className="flex items-baseline justify-between gap-2">
                              <span
                                className={`text-[15px] font-medium ${on ? "text-viridian" : ""}`}
                              >
                                {o.label}
                              </span>
                              {/* Selection is carried by a word, not only by colour. */}
                              <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-graphite">
                                {on ? "On" : "Off"}
                              </span>
                            </span>
                            <span
                              id={`obj-${o.id}-outcome`}
                              className="mt-1 block text-[13px] leading-snug text-graphite"
                            >
                              {o.outcome}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    {objectives.length === 0 && (
                      <p className="mt-2 text-[13px] text-thermal-text">
                        Choose at least one objective.
                      </p>
                    )}
                  </div>

                  {/* The language decision belongs next to the button that acts on it. */}
                  <div className="rounded-sheet border border-hairline bg-surface p-4">
                    <div className="flex flex-wrap items-end justify-between gap-4">
                      <div>
                        <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                          Report language
                        </span>
                        <div className="mt-2 flex gap-2">
                          {(["en", "ar"] as const).map((lang) => (
                            <button
                              key={lang}
                              type="button"
                              data-testid={`report-lang-${lang}`}
                              aria-pressed={reportLanguage === lang}
                              onClick={() => setReportLanguage(lang)}
                              className={`min-h-10 rounded-sheet border px-3 py-1.5 text-sm transition-colors duration-200 ${
                                reportLanguage === lang
                                  ? "border-viridian bg-viridian-tint text-viridian"
                                  : "border-hairline bg-surface hover:border-graphite"
                              }`}
                            >
                              {lang === "en" ? "English" : "العربية"}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="flex flex-col items-start gap-2">
                        <button
                          onClick={startAnalysis}
                          disabled={!planId || objectives.length === 0 || busy}
                          className="min-h-10 rounded-sheet bg-ink px-6 py-3 text-[15px] font-medium text-paper transition-colors duration-200 hover:bg-black disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {busy ? "Analyzing…" : "Generate insights"}
                        </button>
                        {!planId && !busy && (
                          <span className="font-mono text-[11px] text-graphite">
                            Upload a floorplan first
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {(status === "rejected" || status === "failed") && analysisError && (
                <p
                  data-testid="analysis-error"
                  role="alert"
                  className="reveal rounded-sheet border border-thermal/40 bg-surface p-3 text-sm text-thermal-text"
                >
                  {analysisError}
                </p>
              )}

              {/* ---------------------------------------------------- 2 · in progress */}
              {steps.length > 0 && (
                <div className="reveal rounded-sheet border border-hairline bg-surface p-4 sm:p-5">
                  <DimLine label="Analysis timeline" right={status ?? ""} />
                  <Timeline steps={steps} status={status} />
                </div>
              )}

              {feed.length > 0 && (
                <div className="rounded-sheet border border-hairline bg-surface p-4">
                  {/* Deliberately not collapsed once the run ends. It is the honest record of
                      what degraded — a missing render, a fallback provider — and hiding it
                      the moment the analysis succeeds is how a silent downgrade stays silent. */}
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                    Pipeline register
                  </span>
                  <ul className="mt-3 max-h-64 space-y-1 overflow-y-auto font-mono text-[13px] text-graphite">
                    {feed.map((m, i) => (
                      <li key={i} className="border-b border-hairline pb-1 last:border-b-0">
                        {m}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* ------------------------------------------------ 3 · decision summary */}
              {finished && results && (
                <div className="reveal rounded-sheet border border-viridian/30 bg-viridian-tint p-5 sm:p-6">
                  <DimLine label="Decision summary" right={selected.space_type} />

                  {best ? (
                    <>
                      <h2 className="mt-4 max-w-3xl font-serif text-[24px] leading-tight sm:text-[28px]">
                        {best.name}
                      </h2>
                      <p className="mt-2 max-w-3xl text-[15px] text-graphite">
                        {best.moves[0]?.description}
                        {best.moves[0]?.rationale ? ` — ${best.moves[0].rationale}` : ""}
                      </p>
                    </>
                  ) : (
                    <p className="mt-4 text-[15px] text-graphite">
                      The analysis finished. The evidence below is ready to review.
                    </p>
                  )}

                  <dl className="mt-5 flex flex-wrap gap-x-10 gap-y-4">
                    {results.flowScore !== null && (
                      <div data-testid="flow-score">
                        <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-graphite">
                          Flow efficiency
                        </dt>
                        <dd className="mt-1">
                          {/* The score stands alone in this <p>: the browser test reads its
                              text and parses it as a number, so a suffix inside it would
                              turn a passing assertion into NaN. */}
                          <p className="font-serif text-[34px] leading-none text-viridian">
                            {results.flowScore}
                          </p>
                          <span className="font-mono text-[11px] text-graphite">out of 100</span>
                        </dd>
                      </div>
                    )}
                    {best && (
                      <div>
                        <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-graphite">
                          Confidence
                        </dt>
                        <dd className="mt-1 font-serif text-[34px] leading-none">
                          {Math.round(best.confidence * 100)}%
                        </dd>
                      </div>
                    )}
                    {best && (
                      <div>
                        <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-graphite">
                          Physically fits
                        </dt>
                        <dd className="mt-1 font-serif text-[34px] leading-none">
                          {best.solver_feasible ? "Yes" : "No"}
                        </dd>
                      </div>
                    )}
                  </dl>

                  <p className="mt-5 max-w-2xl text-[14px] text-graphite">
                    Area-weighted flow intensity across guest-facing zones. Every assumption
                    behind these numbers is printed in the report.
                  </p>

                  <div className="mt-5 flex flex-wrap items-center gap-3">
                    <a
                      href="#evidence"
                      className="rounded-sheet border border-viridian px-4 py-2 text-[14px] text-viridian transition-colors duration-200 hover:bg-viridian hover:text-paper"
                    >
                      Review the evidence
                    </a>
                    <span className="text-[14px] text-graphite">
                      Then download the report, or send a client link.
                    </span>
                  </div>
                </div>
              )}

              {/* --------------------------------------------------------- 4 · evidence */}
              {results && (heatmapKey || results.scenarios.length > 0 || results.moodboard) && (
                <div id="evidence" className="space-y-8 scroll-mt-8">
                  {heatmapKey && (
                    <section className="reveal reveal-1">
                      <DimLine label="Flow heatmap" right="cool → hot" />
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`${API}/analyses/${results.analysisId}/files/${heatmapKey}`}
                        alt="Guest-flow heatmap over the uploaded floorplan"
                        className="mt-3 w-full rounded-sheet border border-hairline"
                      />
                      <p className="mt-2 text-[14px] text-graphite">
                        Warmer areas carry more simulated guest traffic. Bottlenecks and dead
                        zones are called out in the report.
                      </p>
                    </section>
                  )}

                  {results.scenarios.length > 0 && (
                    <section data-testid="scenarios" className="reveal reveal-2">
                      <DimLine label="Layout scenarios" right={`${results.scenarios.length}`} />
                      <div className="mt-3 space-y-3">
                        {results.scenarios.map((s) => (
                          <article
                            key={s.id}
                            className={`rounded-sheet border bg-surface p-4 transition-colors duration-200 ${
                              best?.id === s.id ? "border-viridian" : "border-hairline"
                            }`}
                          >
                            <div className="flex flex-wrap items-baseline justify-between gap-3">
                              <h3 className="font-medium">
                                {s.name}
                                {best?.id === s.id ? (
                                  <span className="ms-2 rounded-sheet border border-viridian px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-viridian">
                                    Recommended
                                  </span>
                                ) : null}
                              </h3>
                              <span className="whitespace-nowrap font-mono text-xs uppercase text-graphite">
                                confidence {Math.round(s.confidence * 100)}%
                              </span>
                            </div>

                            <p
                              data-testid="solver-verdict"
                              className={`mt-2 text-[13px] ${
                                s.solver_feasible ? "text-viridian" : "text-thermal-text"
                              }`}
                              title={(s.solver_notes ?? []).join("\n")}
                            >
                              {s.solver_feasible
                                ? "✓ Fits the available floor area — checked by the constraint solver"
                                : "✕ Does not fit as proposed — the constraint solver rejected it against the usable area"}
                            </p>

                            <ul className="mt-3 space-y-2">
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
                                  {k.replace(/_/g, " ")}: {v}
                                </span>
                              ))}
                            </div>
                          </article>
                        ))}
                      </div>
                    </section>
                  )}

                  {results.moodboard && (
                    <section data-testid="moodboard" className="reveal reveal-3">
                      <DimLine label="Design direction" right={results.moodboard.style_name} />
                      {(results.moodboard.image_keys ?? []).length > 0 && (
                        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
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
                          Draft renders
                          {results.moodboard.render_provider
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
                            <p className="mt-1 font-mono text-[11px] uppercase text-graphite">
                              {hex}
                            </p>
                          </div>
                        ))}
                      </div>
                      <p className="mt-3 text-sm">
                        <span className="font-mono text-xs uppercase tracking-[0.08em] text-graphite">
                          Materials ·{" "}
                        </span>
                        {results.moodboard.materials.join(", ")}
                      </p>
                      {results.moodboard.lighting_concept && (
                        <p className="mt-1 text-sm text-graphite">
                          {results.moodboard.lighting_concept}
                        </p>
                      )}
                    </section>
                  )}
                </div>
              )}

              {/* ------------------------------------------------------- 5 · delivery */}
              {results?.reportReady && (
                <section className="reveal rounded-sheet border border-hairline bg-surface p-4 sm:p-5">
                  <DimLine label="Deliver to the client" />
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <a
                      data-testid="report-download"
                      href={`${API}/analyses/${results.analysisId}/report`}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-block min-h-10 rounded-sheet bg-viridian px-6 py-3 text-[15px] font-medium text-paper transition-opacity duration-200 hover:opacity-90"
                    >
                      Download insight report (PDF)
                    </a>
                    <button
                      type="button"
                      data-testid="share-report"
                      onClick={() => void shareReport(results.analysisId)}
                      className="min-h-10 rounded-sheet border border-hairline px-5 py-2.5 text-[14px] transition-colors duration-200 hover:border-viridian"
                    >
                      {shareState.status === "working" ? "Creating link…" : "Copy client link"}
                    </button>
                  </div>

                  <p className="mt-3 max-w-2xl text-[13px] text-graphite">
                    A client link opens the report without an account and expires on its own.
                  </p>

                  {shareState.status === "ready" && (
                    <p
                      data-testid="share-link"
                      role="status"
                      className="reveal mt-3 break-all rounded-sheet border border-viridian bg-viridian-tint px-3 py-2 font-mono text-[11px] text-viridian"
                    >
                      {shareState.message}
                    </p>
                  )}
                  {shareState.status === "error" && (
                    <p
                      data-testid="share-error"
                      role="alert"
                      className="reveal mt-3 font-mono text-[11px] text-thermal"
                    >
                      {shareState.message}
                    </p>
                  )}
                </section>
              )}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
