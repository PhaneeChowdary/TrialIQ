import { useMemo, useState } from "react";
import type { RunEvent } from "./api";

/* Trace waterfall: each model call, tool call, guard and reviewer decision of one patient on one time axis.
   Bars are durations ; diamonds are instant decisions . */

type Row = { start: number; end: number; lane: string; label: string; detail: string; kind: "call" | "tool" | "guard" | "review" | "stage" | "error" | "security"; tone: string };

const TONE: Record<string, string> = {
  chart: "var(--trace-chart)", evidence_reviewer: "var(--violet)", reflection: "var(--blue)", outreach: "var(--green)",
  tool: "var(--trace-tool)", guard: "var(--amber)", review: "var(--violet)", error: "var(--red)", stage: "var(--faint)",
  security: "var(--red)",
};

function rows(calls: any[], events: RunEvent[]): Row[] {
  const out: Row[] = [];
  for (const c of calls) {
    const start = new Date(c.at ?? c.started_at).getTime();
    out.push({ start, end: start + c.ms, lane: c.agent, kind: "call", tone: TONE[c.agent] || "var(--text-2)",
      label: `${c.agent} · ${c.model || c.provider || "model"}`,
      detail: `${(c.ms / 1000).toFixed(1)} s · ${c.tokens_in}+${c.tokens_out} tokens${c.ok ? "" : " · failed: " + c.error}` });
  }
  for (const e of events) {
    const at = new Date(e.at).getTime();
    if (e.kind === "tool") {
      const ms = e.data?.ms || 0;
      out.push({ start: at - ms, end: at + 400, lane: "tool", kind: "tool", tone: TONE.tool, label: e.text.split("(")[0], detail: e.text });
    } else if (e.kind === "guard" || e.kind === "review" || e.kind === "error" || e.kind === "security") {
      const label = { guard: "guard", review: "reviewer rejects", error: "error", security: "security" }[e.kind];
      out.push({ start: at, end: at, lane: e.kind, kind: e.kind as Row["kind"], tone: TONE[e.kind], label, detail: e.text });
    } else if (e.kind === "stage") {
      out.push({ start: at, end: at, lane: "stage", kind: "stage", tone: TONE.stage, label: e.agent, detail: e.text });
    }
  }
  return out.sort((a, b) => a.start - b.start || b.end - a.end);
}

function niceStep(totalMs: number) {
  const s = totalMs / 1000;
  for (const step of [1, 2, 5, 10, 15, 30, 60, 120, 300]) if (s / step <= 8) return step * 1000;
  return 600000;
}

export default function TraceWaterfall({ calls, events }: { calls: any[]; events: RunEvent[] }) {
  const data = useMemo(() => rows(calls, events), [calls, events]);
  const [hover, setHover] = useState<number | null>(null);
  if (!data.length) return <div className="empty">No trace for this patient: the rules decided without a model</div>;
  const t0 = Math.min(...data.map((r) => r.start));
  const t1 = Math.max(...data.map((r) => r.end), t0 + 1000);
  const span = t1 - t0;
  const W = 640, LABEL = 170, ROW = 24, H = data.length * ROW + 34;
  const x = (t: number) => LABEL + ((t - t0) / span) * (W - LABEL - 12);
  const step = niceStep(span);
  const ticks = Array.from({ length: Math.floor(span / step) + 1 }, (_, i) => i * step);
  const totals = {
    calls: calls.length, tokens: calls.reduce((n, c) => n + c.tokens_in + c.tokens_out, 0),
    tools: data.filter((r) => r.kind === "tool").length, guards: data.filter((r) => r.kind === "guard").length,
    reviews: data.filter((r) => r.kind === "review").length,
  };
  const h = hover != null ? data[hover] : null;
  return (
    <div className="trace">
      <div className="legend" style={{ marginBottom: 10 }}>
        <span>{(span / 1000).toFixed(1)} s</span><span>{totals.calls} model calls</span><span>{(totals.tokens / 1000).toFixed(1)}k tokens</span>
        <span>{totals.tools} tool calls</span><span style={{ color: "var(--amber)" }}>{totals.guards} guard</span>
        <span style={{ color: "var(--violet)" }}>{totals.reviews} reviewer rejections</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="trace-svg" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t0 + t)} x2={x(t0 + t)} y1={0} y2={H - 18} className="trace-grid" />
            <text x={x(t0 + t)} y={H - 4} className="trace-tick" textAnchor="middle">{t / 1000}s</text>
          </g>
        ))}
        {data.map((r, i) => {
          const y = i * ROW + 4;
          const instant = r.end <= r.start;
          return (
            <g key={i} onMouseEnter={() => setHover(i)} className={hover === i ? "trace-row on" : "trace-row"}>
              <rect x={0} y={y - 2} width={W} height={ROW} className="trace-hit" />
              <text x={4} y={y + 13} className="trace-label">{r.label.length > 26 ? r.label.slice(0, 25) + "…" : r.label}</text>
              {instant ? (
                <rect x={x(r.start) - 5} y={y + 4} width={10} height={10} fill={r.tone} transform={`rotate(45 ${x(r.start)} ${y + 9})`} />
              ) : (
                <rect x={x(r.start)} y={y + 4} width={Math.max(3, x(r.end) - x(r.start))} height={11} rx={3} fill={r.tone}
                  opacity={r.kind === "tool" ? 0.55 : 0.9} />
              )}
            </g>
          );
        })}
      </svg>
      <div className="trace-detail">{h ? <><b>{h.label}</b> · +{((h.start - t0) / 1000).toFixed(1)} s · {h.detail}</> : "Point to a row"}</div>
    </div>
  );
}
