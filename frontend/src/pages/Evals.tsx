import { useState } from "react";
import { api } from "../api";
import { useLoad } from "../ui";
import { IsoBlocks, TickMeter, type IsoItem } from "../charts";

const METHODS = [
  { id: "rules", label: "Rules" },
  { id: "rules_mapped", label: "Rules + LOINC" },
  { id: "single_prompt", label: "Rules + one prompt" },
  { id: "agents", label: "Rules + agents" },
];
const SHORT: Record<string, string> = { rules: "Rules", rules_mapped: "+LOINC", single_prompt: "1 prompt", agents: "Agents" };
const ROWS: [string, string, (v: any) => string][] = [
  ["hard_right", "Hard set: right", (v) => v],
  ["hard_wrong", "Hard set: wrong", (v) => v],
  ["hard_review", "Hard set: left for review", (v) => v],
  ["recall", "Recall of eligible", (v) => (v == null ? "–" : `${Math.round(v * 100)}%`)],
  ["precision", "Precision of eligible", (v) => (v == null ? "–" : `${Math.round(v * 100)}%`)],
  ["missed_as_ineligible", "Eligible called ineligible", (v) => v],
  ["false_eligible", "Ineligible called eligible", (v) => v],
  ["llm_calls", "Model calls", (v) => v],
  ["tokens", "Tokens", (v) => (v ? `${(v / 1000).toFixed(0)}k` : "0")],
  ["seconds", "Time", (v) => (v >= 60 ? `${(v / 60).toFixed(1)} min` : `${v} s`)],
];

function MethodBlocks({ label, id, rep }: { label: string; id: string; rep: any }) {
  const [hover, setHover] = useState<IsoItem | null>(null);
  const o = rep.overall[id];
  const items: IsoItem[] = [];
  for (const [nct, tr] of Object.entries<any>(rep.trials)) {
    const h = tr.methods[id]?.hard;
    if (!h) continue;
    const tag = `${nct.slice(0, 3)}…${nct.slice(-4)}`;
    items.push(...Array.from({ length: h.right }, () => ({ state: "right" as const, label: `${tag} · right` })),
      ...Array.from({ length: h.n - h.right - h.wrong }, () => ({ state: "open" as const, label: `${tag} · left open` })),
      ...Array.from({ length: h.wrong }, () => ({ state: "wrong" as const, label: `${tag} · wrong` })));
  }
  const open = o.hard_n - o.hard_right - o.hard_wrong;
  return (
    <div className="panel evalcard">
      <div className="gauge-head"><span className="k">{label}</span><span className={`pill ${o.hard_wrong ? "red" : "outline"}`}>{o.hard_wrong} wrong</span></div>
      <div className="evalcard-v">{o.hard_right}<span>/{o.hard_n}</span></div>
      <IsoBlocks items={items} onHover={setHover} />
      <div className="evalcard-f">
        {hover ? <span>{hover.label}</span> : <>
          <span><i className="sq green" />{o.hard_right} right</span><span><i className="sq amber" />{open} open</span><span><i className="sq red" />{o.hard_wrong} wrong</span>
        </>}
      </div>
    </div>
  );
}

export default function Evals() {
  const r = useLoad(api.evals);
  const [trial, setTrial] = useState<string>("");
  const rep = r.data;
  if (!rep) return <><div className="page-head"><h1>Evaluation</h1></div><div className="panel empty">{r.error || "No report. Run backend/evals/run_evals.py"}</div></>;
  const methods = METHODS.filter((m) => rep.overall[m.id]);
  const best = (key: string, higher: boolean) => {
    const vals = methods.map((m) => rep.overall[m.id][key]).filter((x) => x != null);
    return higher ? Math.max(...vals) : Math.min(...vals);
  };
  const hardN = methods.length ? rep.overall[methods[0].id].hard_n : 0;
  const t = trial || Object.keys(rep.trials)[0];
  const tr = rep.trials[t];
  const traps = [...new Set(methods.flatMap((m) => Object.keys(tr.methods[m.id]?.traps || {})))].sort();
  return (
    <>
      <div className="page-head">
        <div><h1>Evaluation</h1><div className="sub">{rep.patients} patients · 3 trials · {hardN} hard patient-trial pairs · {rep.created.replace("T", " ")}</div></div>
        <span className="pill outline mono">{rep.model?.chart?.join(" > ")}</span>
      </div>
      <div className="gauges" style={{ marginBottom: 14 }}>
        {methods.map((m) => <MethodBlocks key={m.id} label={m.label} id={m.id} rep={rep} />)}
      </div>
      <div className="panel flush" style={{ marginBottom: 14 }}>
        <table>
          <thead><tr><th />{methods.map((m) => <th key={m.id} className="num">{m.label}</th>)}</tr></thead>
          <tbody>
            {ROWS.map(([key, label, fmt]) => {
              const higher = ["hard_right", "recall", "precision"].includes(key);
              const lower = ["hard_wrong", "missed_as_ineligible", "false_eligible"].includes(key);
              const b = higher || lower ? best(key, higher) : null;
              return (
                <tr key={key}>
                  <td><strong>{label}</strong></td>
                  {methods.map((m) => {
                    const v = rep.overall[m.id][key];
                    return <td key={m.id} className="num" style={b != null && v === b ? { color: "var(--green-text)", fontWeight: 650 } : undefined}>{fmt(v)}</td>;
                  })}
                </tr>
              );
            })}
            <tr>
              <td><strong>Eligible found, by clinic</strong></td>
              {methods.map((m) => (
                <td key={m.id} className="num small">
                  {Object.entries(rep.overall[m.id].recall_by_clinic).map(([k, v]) => {
                    const [found, all] = String(v).split("/").map(Number);
                    return <div key={k} className="row" style={{ justifyContent: "flex-end", gap: 8, marginBottom: 3 }}>
                      <span className="faint">{k.split(" ")[0]}</span><TickMeter value={found} max={all} ticks={10} label={String(v)} />
                    </div>;
                  })}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <div className="tabs">
        {Object.keys(rep.trials).map((k) => <button key={k} className={`tab ${t === k ? "on" : ""}`} onClick={() => setTrial(k)}>{k}</button>)}
      </div>
      <div className="grid g2" style={{ alignItems: "start" }}>
        <div className="panel flush">
          <div className="panel-head"><h3>{t}</h3><span className="muted small">gold {Object.entries(tr.gold).map(([k, v]) => `${k} ${v}`).join(" · ")}</span></div>
          <table>
            <thead><tr><th />{methods.map((m) => <th key={m.id} className="num" title={m.label}>{SHORT[m.id]}</th>)}</tr></thead>
            <tbody>
              {[["hard", "Hard set right / n", (v: any) => `${v.right}/${v.n}`], ["recall", "Recall", (v: any) => v ?? "–"], ["precision", "Precision", (v: any) => v ?? "–"],
                ["criterion_accuracy", "Criterion accuracy", (v: any) => v ?? "–"], ["seconds_per_open_patient", "s / open patient", (v: any) => v ?? "–"],
                ["guard_problems", "Guard catches", (v: any) => v], ["reviewer_rejections", "Reviewer rejections", (v: any) => v]].map(([k, label, fmt]: any) => (
                <tr key={k}><td><strong>{label}</strong></td>{methods.map((m) => <td key={m.id} className="num">{tr.methods[m.id] ? fmt(tr.methods[m.id][k]) : "–"}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel flush">
          <div className="panel-head"><h3>Trap patients in the hard set</h3></div>
          <table>
            <thead><tr><th>Trap</th>{methods.map((m) => <th key={m.id} className="num" title={m.label}>{SHORT[m.id]}</th>)}</tr></thead>
            <tbody>
              {traps.map((k) => (
                <tr key={k}>
                  <td className="small">{k.replace(/_/g, " ")}</td>
                  {methods.map((m) => {
                    const v = tr.methods[m.id]?.traps?.[k];
                    const tone = !v ? "" : v.right === v.n ? "var(--green-text)" : v.right === 0 ? "var(--red-text)" : "var(--amber-text)";
                    return <td key={m.id} className="num" style={{ color: tone }}>{v ? `${v.right}/${v.n}` : "–"}</td>;
                  })}
                </tr>
              ))}
              {!traps.length && <tr><td colSpan={5} className="empty">No trap patients</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
