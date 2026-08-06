"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const body =
        mode === "login" ? { email, password } : { email, password, org_name: orgName };
      const r = await fetch(`${API}${path}`, {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const detail = (await r.json()).detail;
        setError(typeof detail === "string" ? detail : "Check your details and try again.");
        return;
      }
      router.push("/projects");
    } catch {
      setError("Couldn't reach the server — is the API running?");
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "w-full rounded-sheet border border-hairline bg-surface px-3 py-2 text-[15px] outline-none focus:border-viridian";

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-sm flex-col justify-center px-6">
      <div className="mb-10 text-center font-serif text-[28px] tracking-tight">
        Méyraki <span className="ms-2 font-mono text-xs uppercase tracking-[0.18em] text-graphite">Insight</span>
      </div>

      <div className="dim-line mb-8">
        <span className="dim-label">{mode === "login" ? "Sign in" : "Create your studio"}</span>
        <span className="dim-rule" />
      </div>

      <form onSubmit={submit} className="space-y-3">
        {mode === "register" && (
          <input
            className={inputCls}
            placeholder="Studio / organization name"
            value={orgName}
            maxLength={200}
            onChange={(e) => setOrgName(e.target.value)}
            required
          />
        )}
        <input
          className={inputCls}
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          className={inputCls}
          type="password"
          placeholder="Password (8+ characters)"
          value={password}
          minLength={8}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <p className="text-sm text-thermal-text">{error}</p>}
        <button
          disabled={busy}
          className="min-h-10 w-full rounded-sheet bg-ink px-4 py-2.5 text-[15px] font-medium text-paper transition-colors hover:bg-black disabled:opacity-40"
        >
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>

      <button
        type="button"
        onClick={() => {
          setMode(mode === "login" ? "register" : "login");
          setError(null);
        }}
        className="mt-6 text-center text-sm text-graphite underline-offset-4 hover:underline"
      >
        {mode === "login" ? "New here? Create your studio" : "Already have an account? Sign in"}
      </button>
    </main>
  );
}
