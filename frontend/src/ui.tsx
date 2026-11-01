import { useEffect, useRef, useState } from "react";
import type { Atom, Run, Status, Verdict } from "./api";

export function useInterval(fn: () => void, ms: number | null) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const reload = () => load().then((d) => { setData(d); setError(""); }).catch((e) => setError(String(e.message || e)));
  useEffect(() => { reload(); }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, reload, setData };
}

export const VERDICT: Record<Verdict, { label: string; tone: string }> = {
  eligible: { label: "Eligible", tone: "green" },
  needs_review: { label: "Needs review", tone: "amber" },
  ineligible: { label: "Ineligible", tone: "red" },
};

export function VerdictPill({ v }: { v: Verdict }) {
  const x = VERDICT[v] || { label: v, tone: "" };
  return <span className={`pill ${x.tone}`}>{x.label}</span>;
}

export const RUN_TONE: Record<string, string> = {
  queued: "outline", running: "blue", awaiting_approval: "amber", completed: "green", failed: "red",
};

export function RunPill({ run }: { run: Pick<Run, "status"> }) {
  const label = run.status === "awaiting_approval" ? "Awaiting approval" : run.status[0].toUpperCase() + run.status.slice(1);
  return (
    <span className={`pill ${RUN_TONE[run.status] || ""}`}>
      {run.status === "running" && <span className="dot blue pulse" />}
      {label}
    </span>
  );
}

export function StatusCell({ id, status, kind }: { id: string; status: Status; kind: string }) {
  const pass = (kind === "inclusion" && status === "met") || (kind === "exclusion" && status === "not_met");
  const fail = (kind === "inclusion" && status === "not_met") || (kind === "exclusion" && status === "met");
  return <span className={`cstat ${pass ? "pass" : fail ? "fail" : "open"}`} title={`${id}: ${status}`}>{id}</span>;
}

export function statusTone(status: Status, kind: string) {
  if (status === "unknown") return "amber";
  const good = kind === "exclusion" ? status === "not_met" : status === "met";
  return good ? "green" : "red";
}

export function Kpi({ v, l, d, tone }: { v: React.ReactNode; l: string; d?: React.ReactNode; tone?: string }) {
  return (
    <div className="panel kpi">
      <div className={`v ${tone || ""}`}>{typeof v === "number" ? <CountUp value={v} /> : v === "–" ? <span className="skel" style={{ width: 54, height: 26 }} /> : v}</div>
      <div className="l">{l}</div>
      {d && <div className="d">{d}</div>}
    </div>
  );
}

export function FunnelBars({ rows }: { rows: { label: string; value: number; color: string }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="funnel">
      {rows.map((r) => (
        <div className="fbar" key={r.label}>
          <span className="lab">{r.label}</span>
          <div className="track"><div className="fill" style={{ width: `${(r.value / max) * 100}%`, background: r.color }} /></div>
          <span className="val">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export function atomText(a: Atom): string {
  if (a.type === "group") return (a.all_of || []).map(atomText).join(" + ");
  const parts = [(a.negate ? "no " : "") + a.concept];
  if (a.min != null || a.max != null) {
    const lo = a.min == null ? "" : `${a.min_exclusive ? ">" : "≥"}${a.min}`;
    const hi = a.max == null ? "" : `${a.max_exclusive ? "<" : "≤"}${a.max}`;
    parts.push([lo, hi].filter(Boolean).join(" ") + (a.unit && a.type !== "age" ? ` ${a.unit}` : ""));
  }
  if (a.min_duration_days) parts.push(`≥${a.min_duration_days} d`);
  if (a.lookback_days) parts.push(`in ${a.lookback_days} d`);
  if (a.ignore_courses_up_to_days) parts.push(`courses ≤${a.ignore_courses_up_to_days} d allowed`);
  return parts.join(" · ");
}

export function ago(iso?: string) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function clock(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function secs(ms: number) {
  return ms >= 60000 ? `${(ms / 60000).toFixed(1)} min` : `${(ms / 1000).toFixed(1)} s`;
}

export function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => { const t = setTimeout(onDone, 3200); return () => clearTimeout(t); }, [text, onDone]);
  return <div className="toast">{text}</div>;
}

/* Numbers count up to their value (and stay still with reduced motion). */
export function CountUp({ value, ms = 650 }: { value: number; ms?: number }) {
  const [shown, setShown] = useState(value);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setShown(value); return; }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ms);
      setShown(Math.round(a + (value - a) * (1 - Math.pow(1 - t, 3))));
      if (t < 1) raf = requestAnimationFrame(step); else from.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, ms]);
  return <>{shown}</>;
}

/* Shimmering placeholder lines while data loads. */
export function Skeleton({ rows = 4, height = 14 }: { rows?: number; height?: number }) {
  return (
    <div className="skel-wrap">
      {Array.from({ length: rows }).map((_, i) => (
        <span key={i} className="skel" style={{ height, width: `${92 - ((i * 17) % 35)}%` }} />
      ))}
    </div>
  );
}

/* Dark (default) or light theme, kept in this browser. */
export function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => {
    try { return localStorage.getItem("trialiq.theme") || "dark"; } catch { return "dark"; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f6f6f4" : "#0a0a0a");
    try { localStorage.setItem("trialiq.theme", theme); } catch { /* private mode */ }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}
