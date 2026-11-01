import { useState } from "react";
import { ArrowUpRight, Bot, CloudOff, Hourglass, KeyRound, Link2, ShieldAlert, ShieldCheck, TriangleAlert } from "lucide-react";
import { api } from "../api";
import { go } from "../App";
import { CountUp, Skeleton, ago, useInterval, useLoad } from "../ui";
import { FlowBars, Meter } from "../charts";
import PatientDrawer from "./PatientDrawer";
import { describe } from "./Audit";

const EVENT_LABEL: Record<string, string> = {
  injection: "Instruction removed", tool_blocked: "Tool call blocked", egress_blocked: "Patient data kept local",
  referral_blocked: "Referral draft blocked",
};
const PENDING_LABEL: Record<string, string> = { referrals: "Referrals waiting", lessons: "Lessons proposed", mappings: "LOINC mappings proposed" };
const short = (nct?: string) => (nct ? `NCT…${nct.slice(-4)}` : "");
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function SecKpi({ icon: Icon, label, value, total, parts, left, right }: {
  icon: any; label: string; value: number; total?: number; parts: { value: number; tone: string; striped?: boolean }[];
  left: string; right: string;
}) {
  return (
    <div className="panel seckpi">
      <div className="seckpi-l"><Icon />{label}</div>
      <div className="seckpi-v"><CountUp value={value} />{total !== undefined && <span>/{total}</span>}</div>
      <Meter parts={parts} />
      <div className="seckpi-f"><span>{left}</span><span>{right}</span></div>
    </div>
  );
}

function activityTone(a: any) {
  if (a.kind === "security") return a.outcome === "blocked" ? "red" : "green";
  if (/approve|start|saved|chosen/.test(a.type)) return "green";
  if (/reject|removed|cloud_phi/.test(a.type)) return "amber";
  return "blue";
}

export default function Security() {
  const s = useLoad(api.security);
  const [patient, setPatient] = useState<{ run: string; pid: string } | null>(null);
  useInterval(s.reload, 8000);
  const d = s.data;

  if (!d) return (
    <>
      <div className="page-head"><div><h1>Security</h1><div className="sub">Agents · permissions · guards · audit</div></div></div>
      <div className="grid g4" style={{ marginBottom: 14 }}>{[0, 1, 2, 3].map((i) => <div key={i} className="panel"><Skeleton rows={3} /></div>)}</div>
      <div className="panel"><Skeleton rows={8} /></div>
    </>
  );

  const phiAgents = d.agents.filter((a: any) => a.data !== "protocol").length;
  const pendingTotal = d.pending.reduce((n: number, p: any) => n + p.count, 0);
  const local = d.egress.chart_calls - d.egress.cloud;
  const unused = d.agents.filter((a: any) => a.calls).reduce((n: number, a: any) => n + a.unused.length, 0);
  const attention: any[] = [
    ...(d.audit.ok ? [] : [{ tone: "red", title: "Audit chain broken", body: [`Entry ${d.audit.broken_at.position} · ${d.audit.broken_at.action}`, d.audit.broken_at.at], cta: "Open audit log", to: () => go({ page: "audit" }) }]),
    ...(d.injected.length ? [{ tone: "red", title: "Instructions found in patient notes", body: ["Removed before any model read them", `${d.injected.length} patient chart(s)`], patients: d.injected }] : []),
    ...d.pending.map((p: any) => ({
      tone: "amber", title: `${PENDING_LABEL[p.type]}${p.nct_id ? ` · ${short(p.nct_id)}` : ""}`,
      body: [`${p.count} waiting · a person decides`, p.run_id ? `Run ${p.run_id}${p.at ? ` · ${ago(p.at)}` : ""}` : "Policy · human approval required"],
      cta: p.type === "referrals" ? "Review referrals" : p.type === "lessons" ? "Review lessons" : "Review mappings",
      to: () => p.type === "mappings" ? go({ page: "terminology" }) : go({ page: "trial", id: p.nct_id, tab: p.type === "referrals" ? "referrals" : "learning", run: p.run_id }),
    })),
    ...d.keys.filter((k: any) => k.check_ok === false || (k.age_days ?? 0) > 90).map((k: any) => ({
      tone: "amber", title: `${k.name} key needs attention`, body: [k.check_ok === false ? "Last check failed" : `Saved ${k.age_days} days ago · rotate`, k.hint],
      cta: "Open models", to: () => go({ page: "models" }),
    })),
  ];

  return (
    <>
      <div className="page-head">
        <div><h1>Security</h1><div className="sub">Agents · permissions · guards · audit</div></div>
        <span className={`pill ${d.audit.ok ? "green" : "red"}`}>{d.audit.ok ? <ShieldCheck /> : <ShieldAlert />}Audit chain {d.audit.ok ? "verified" : "broken"}</span>
      </div>

      <div className="grid g4" style={{ marginBottom: 14 }}>
        <SecKpi icon={Bot} label="Agents" value={d.agents.length} left={`${phiAgents} read patient data`} right={`${unused} unused tools`}
          parts={[{ value: phiAgents, tone: "fg", striped: true }, { value: d.agents.length - phiAgents, tone: "fg" }]} />
        <SecKpi icon={ShieldAlert} label="Answers stopped" value={d.totals.stopped} total={d.totals.answers}
          left={`${d.totals.stopped} stopped`} right={`${d.totals.accepted} accepted`}
          parts={[{ value: d.totals.stopped, tone: "red", striped: true }, { value: d.totals.answers - d.totals.stopped, tone: "fg" }]} />
        <SecKpi icon={Hourglass} label="Pending approvals" value={pendingTotal}
          left={`${d.pending.filter((p: any) => p.type === "referrals").reduce((n: number, p: any) => n + p.count, 0)} referrals`}
          right={`${d.pending.filter((p: any) => p.type !== "referrals").reduce((n: number, p: any) => n + p.count, 0)} rule changes`}
          parts={d.pending.map((p: any, i: number) => ({ value: p.count, tone: "amber", striped: i % 2 === 0 }))} />
        <SecKpi icon={CloudOff} label="Patient data to cloud" value={d.egress.cloud} left={`${local} local calls`} right={d.cloud_phi ? "cloud allowed" : "cloud blocked"}
          parts={[{ value: d.egress.cloud, tone: "red", striped: true }, { value: local, tone: "green" }]} />
      </div>

      <div className="grid sec-row" style={{ marginBottom: 14 }}>
        <div className="panel">
          <div className="panel-head">
            <h2>Checks per run</h2>
            <div className="legend"><span><i className="sq fg" />Accepted</span><span><i className="sq red" />Stopped</span></div>
          </div>
          <FlowBars bars={d.checks.filter((c: any) => c.answers).map((c: any) => ({
            label: `${day(c.at)} · ${short(c.nct_id)}`, a: c.accepted, b: c.stopped, total: c.answers, title: `Run ${c.run_id}: ${c.patients} patients`,
          }))} />
        </div>
        <div className="panel">
          <div className="panel-head"><h2>Integrity</h2><span className="pill outline">{d.cloud_phi ? "notes may go to cloud" : "notes stay local"}</span></div>
          <div className="seck-big">{d.audit.entries}<span>/{d.audit.entries}</span></div>
          <div className="lbl">Audit entries verified</div>
          <div className={`chain-track ${d.audit.ok ? "" : "bad"}`}><i style={{ width: d.audit.ok ? "100%" : `${(100 * (d.audit.broken_at.position - 1)) / d.audit.entries}%` }}><Link2 /></i></div>
          <div className="sec-list">
            {Object.entries(d.events).map(([k, n]: any) => (
              <div key={k}><span>{EVENT_LABEL[k]}</span><span className="chip">{n}</span></div>
            ))}
            {d.keys.length ? d.keys.map((k: any) => (
              <div key={k.provider}><span><KeyRound size={12} /> {k.name} {k.hint}</span>
                <span className={`pill ${k.check_ok === false ? "red" : k.reads_notes ? "amber" : "outline"}`}>
                  {k.check_ok === false ? "check failed" : k.reads_notes ? "reads notes" : k.age_days != null ? `${k.age_days} d` : k.source}
                </span>
              </div>
            )) : <div><span><KeyRound size={12} /> Cloud keys</span><span className="pill outline">none · local only</span></div>}
          </div>
        </div>
      </div>

      <div className="grid sec-row">
        <div className="panel flush">
          <div className="panel-head"><h2>Agents</h2><span className="pill outline">{d.agents.length} agents</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Agent</th><th>Access</th><th>Model</th><th>Gate</th><th className="num">Calls</th><th>Status</th></tr></thead>
              <tbody>
                {d.agents.map((a: any) => (
                  <tr key={a.id}>
                    <td><strong>{a.name}</strong><div className="small muted">{a.data_label}</div></td>
                    <td><div className="scope">
                      {a.tools.length ? a.tools.slice(0, 2).map((t: string) => <span className="chip" key={t} title={`${a.tool_uses[t] || 0} call(s)`}>{t}</span>) : <span className="chip faint">no tools</span>}
                      {a.tools.length > 2 && <span className="chip" title={a.tools.slice(2).join(", ")}>+{a.tools.length - 2}</span>}
                    </div></td>
                    <td className="mono small">{a.models[0] || "–"}</td>
                    <td className="small">{a.gate}</td>
                    <td className="num">{a.calls}</td>
                    <td>{a.blocked ? <span className="pill red">{a.blocked} blocked</span>
                      : !a.calls ? <span className="pill outline">Idle</span>
                        : a.unused.length ? <span className="pill amber" title={`Never called: ${a.unused.join(", ")}`}>{a.unused.length} unused</span>
                          : <span className="pill green">Least privilege</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="sec-side">
          <div className="panel">
            <div className="panel-head"><h2>Need attention</h2><span className="chip">{String(attention.length).padStart(2, "0")}</span></div>
            {!attention.length && <div className="empty">Nothing</div>}
            {attention.map((x, i) => (
              <div className={`attn ${x.tone}`} key={i}>
                <div className="attn-h"><span className="attn-i"><TriangleAlert /></span>{x.title}</div>
                <div className="attn-b">
                  {x.body.map((b: string, j: number) => <div key={j} className={j ? "muted small" : ""}>{b}</div>)}
                  {x.patients && <div className="row" style={{ marginTop: 8 }}>{x.patients.map((p: any) => (
                    <button key={p.run_id + p.patient} className="chip btnchip" onClick={() => setPatient({ run: p.run_id, pid: p.patient })}>{p.patient}</button>
                  ))}</div>}
                  {x.cta && <button className="attn-cta" onClick={x.to}>{x.cta}<ArrowUpRight /></button>}
                </div>
              </div>
            ))}
          </div>
          <div className="panel">
            <div className="panel-head"><h2>Recent activity</h2><span className="chip">{String(Math.min(8, d.activity.length)).padStart(2, "0")}</span></div>
            <div className="act">
              {d.activity.slice(0, 8).map((a: any, i: number) => (
                <div className="act-row" key={i}>
                  <i className={`sq ${activityTone(a)}`} />
                  <div>
                    <div>{a.kind === "security" ? EVENT_LABEL[a.type] || a.type : <><b className="act-who">{a.agent}</b> {describe(a.type, a.text)}</>}</div>
                    <div className="muted small">{a.kind === "security" ? [a.agent, a.patient, a.text.slice(0, 80)].filter(Boolean).join(" · ") : a.text.slice(0, 80)}</div>
                    <div className="faint small">{ago(a.at)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
      {patient && <PatientDrawer runId={patient.run} personId={patient.pid} onClose={() => setPatient(null)} />}
    </>
  );
}
