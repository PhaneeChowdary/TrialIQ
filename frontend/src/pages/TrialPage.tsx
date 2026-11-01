import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ExternalLink, Play, RefreshCw, Send, Sparkles, X } from "lucide-react";
import { api, type CriteriaSet, type Outreach, type ResultRow, type Run, type RunEvent } from "../api";
import { go } from "../App";
import { CountUp, FunnelBars, RunPill, Skeleton, StatusCell, Toast, VerdictPill, ago, atomText, clock, secs, useInterval, useLoad } from "../ui";
import PatientDrawer from "./PatientDrawer";
import { useConfirm } from "../Confirm";
import { AreaChart, Gauge, TickMeter } from "../charts";
import { type Lesson } from "../api";

const TABS = [
  { id: "screening", label: "Screening" },
  { id: "candidates", label: "Candidates" },
  { id: "equity", label: "Equity" },
  { id: "referrals", label: "Referrals" },
  { id: "criteria", label: "Criteria" },
  { id: "learning", label: "Learning" },
  { id: "compare", label: "Compare" },
  { id: "protocol", label: "Protocol" },
];

export default function TrialPage({ id, tab, runId }: { id: string; tab: string; runId?: string }) {
  const t = useLoad(() => api.trial(id), [id]);
  const [toast, setToast] = useState("");
  const screens = (t.data?.runs || []).filter((r) => r.kind === "screen");
  const selected = runId ? screens.find((r) => r.id === runId) || screens[0] : screens[0];
  const run = useLoad(() => (selected ? api.run(selected.id) : Promise.resolve(null)), [selected?.id]);
  const live = run.data && ["queued", "running"].includes(run.data.status);
  useInterval(() => { run.reload(); if (live) t.reload(); }, live ? 2000 : null);
  useEffect(() => { if (run.data && !live) t.reload(); }, [run.data?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const confirm = useConfirm();
  const start = async () => {
    let est: any = null;
    try { est = await api.estimate(id); } catch { /* the run still asks */ }
    const ok = await confirm({
      title: "Run screening", tone: est && est.minutes > 15 ? "warning" : "primary", confirmLabel: "Start screening",
      message: est ? <>The rules screen all {est.funnel.pool} patients at once. The chart agent then reviews <b>{est.funnel.open} patients</b> ({est.questions} questions): about <b>{est.minutes} min</b> on {est.chart_models.join(" > ")}.</>
        : "The chart agent reviews every patient that the rules cannot decide.",
      details: est ? [`Criteria version ${est.criteria_version} · ${est.mappings} LOINC mapping(s) · frozen for this run`,
        `${est.funnel.excluded} excluded and ${est.funnel.eligible} eligible by the rules, without a model`,
        "Referral drafts wait for your approval: nothing is sent"] : undefined,
    });
    if (!ok) return;
    try {
      const { run_id } = await api.screen(id);
      await t.reload();
      go({ page: "trial", id, tab: "screening", run: run_id });
    } catch (e: any) { setToast(e.message); }
  };
  const trial = t.data?.trial;
  const r = run.data;
  const counts = r?.summary?.verdicts;
  return (
    <>
      <div className="crumb" onClick={() => go({ page: "trials" })}>Trials /</div>
      <div className="page-head">
        <div style={{ maxWidth: 900 }}>
          <div className="row" style={{ marginBottom: 6 }}>
            <span className="pill teal mono">{id}</span>
            {trial?.phases?.map((p) => <span key={p} className="pill outline">{p.replace("PHASE", "Phase ")}</span>)}
            {trial && <span className="pill blue">{trial.status}</span>}
          </div>
          <h1 style={{ lineHeight: 1.3 }}>{trial?.title || id}</h1>
          <div className="sub">{trial?.sponsor} · {trial?.indiana_sites?.join(", ")}</div>
        </div>
        <div className="row">
          {screens.length > 1 && (
            <select value={selected?.id} onChange={(e) => go({ page: "trial", id, tab, run: e.target.value })}>
              {screens.map((s) => <option key={s.id} value={s.id}>{s.id} · {ago(s.created_at)}</option>)}
            </select>
          )}
          <button className="btn primary" disabled={!t.data?.criteria || !!live} onClick={start}><Play />Run screening</button>
        </div>
      </div>
      <div className="tabs">
        {TABS.map((x) => (
          <button key={x.id} className={`tab ${tab === x.id ? "on" : ""}`} onClick={() => go({ page: "trial", id, tab: x.id, run: selected?.id })}>
            {x.label}
            {x.id === "candidates" && counts && <span className="n">{counts.eligible + counts.needs_review}</span>}
            {x.id === "referrals" && r?.status === "awaiting_approval" && <span className="dot amber" />}
          </button>
        ))}
      </div>
      {tab === "screening" && (r ? <Screening run={r} /> : <div className="panel empty">No screening run</div>)}
      {tab === "candidates" && r && <Candidates run={r} criteria={r.criteria || t.data?.criteria || null} />}
      {tab === "equity" && r && <Equity run={r} />}
      {tab === "referrals" && r && <Referrals run={r} onDone={() => { run.reload(); setToast("Decisions sent"); }} onError={setToast} />}
      {tab === "criteria" && t.data && <Criteria id={id} data={t.data} onChange={t.reload} toast={setToast} />}
      {tab === "learning" && t.data && <Learning id={id} data={t.data} onChange={t.reload} toast={setToast} />}
      {tab === "compare" && <Compare runs={screens} />}
      {tab === "protocol" && trial && <Protocol trial={trial} />}
      {["candidates", "equity", "referrals"].includes(tab) && !r && <div className="panel empty">No screening run</div>}
      {toast && <Toast text={toast} onDone={() => setToast("")} />}
    </>
  );
}

const STAGES = ["prescreen", "chart_review", "adjudicate", "outreach", "approval", "done"];
const STAGE_LABEL: Record<string, string> = {
  prescreen: "Prescreen", chart_review: "Chart review", adjudicate: "Adjudicate", outreach: "Referral drafts", approval: "Approval", done: "Done",
};

function Screening({ run }: { run: Run }) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const last = useRef(0);
  const feed = useRef<HTMLDivElement>(null);
  const live = ["queued", "running"].includes(run.status);
  useEffect(() => { setEvents([]); last.current = 0; }, [run.id]);
  const pull = () => api.events(run.id, last.current).then((e) => {
    if (!e.length) return;
    last.current = e[e.length - 1].seq;
    setEvents((old) => {
      const seen = new Set(old.map((x) => x.seq));
      return [...old, ...e.filter((x) => !seen.has(x.seq))].slice(-600);
    });
  });
  useEffect(() => { pull(); }, [run.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useInterval(pull, live ? 1500 : null);
  useEffect(() => { feed.current?.scrollTo({ top: feed.current.scrollHeight }); }, [events.length]);

  const s = run.summary || {};
  const f = s.funnel;
  const v = s.verdicts;
  const stageAt = run.status === "completed" ? STAGES.length - 1 : Math.max(0, STAGES.indexOf(run.stage === "finalize" ? "approval" : run.stage));
  const usage = run.usage;
  const agents = Object.entries(usage?.per_agent || {});
  const noteEligible = v && f ? v.eligible - f.eligible : 0;
  const started = new Date(run.created_at).getTime();
  const reviewed = new Set<string>();
  const done = events.filter((e) => (e.kind === "stage" && e.agent === "evidence_reviewer") || (e.kind === "error" && e.agent === "chart"))
    .filter((e) => !reviewed.has(e.patient) && !!reviewed.add(e.patient));
  const curve = [{ x: 0, y: 0 }, ...done.map((e, i) => ({ x: (new Date(e.at).getTime() - started) / 1000, y: i + 1 }))];
  const gaps = done.map((e, i) => (new Date(e.at).getTime() - (i ? new Date(done[i - 1].at).getTime() : started)) / 1000).sort((a, b) => a - b);
  const median = gaps.length ? gaps[Math.floor(gaps.length / 2)] : null;
  return (
    <div className="stack">
      {f && f.open > 0 && (
        <div className="grid g12">
          <div className="panel">
            <div className="panel-head"><h2>Chart review</h2><span className="legend">{live ? "live" : "done"}</span></div>
            <Gauge value={s.reviewed ?? done.length} max={f.open} label="patients chart-reviewed" />
          </div>
          <div className="panel">
            <div className="panel-head"><h2>Patients resolved over time</h2><span className="legend">{median != null ? `median ${median.toFixed(0)} s / patient` : ""}</span></div>
            <div className="row" style={{ alignItems: "baseline", marginBottom: 6 }}>
              <span className="stat-big">{done.length}</span><span className="lbl">of {f.open} · {usage ? `${(usage.tokens / 1000).toFixed(1)}k tokens` : ""}</span>
            </div>
            <AreaChart points={curve} height={130} format={(v) => `${v} patients`} xLabel={(x) => `${Math.round(x)} s`} />
          </div>
        </div>
      )}
      <div className="panel">
        <div className="steps">
          {STAGES.map((st, i) => {
            const cls = i < stageAt ? "done" : i === stageAt ? (run.status === "awaiting_approval" ? "wait" : run.status === "completed" ? "done" : "now") : "";
            const sub = st === "chart_review" && f ? `${s.reviewed ?? 0}/${f.open} patients` :
              st === "prescreen" && f ? `${f.pool} patients` : st === "outreach" && v ? `${v.eligible} drafts` : "";
            return <div key={st} className={`step ${cls}`}><b>{STAGE_LABEL[st]}</b>{sub || (i === stageAt && live ? "running" : "")}</div>;
          })}
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <RunPill run={run} />
          <span className="muted small">Run {run.id} · {run.actor} · started {ago(run.created_at)}</span>
          {run.error && <span className="err">{run.error}</span>}
        </div>
      </div>
      <div className="split">
        <div className="stack">
          <div className="panel">
            <div className="panel-head"><h2>Funnel</h2>{s.mappings != null && <span className="pill outline">{s.mappings} LOINC mappings applied</span>}</div>
            {f ? (
              <FunnelBars rows={[
                { label: "Patients", value: f.pool, color: "rgb(var(--fg) / 0.28)" },
                { label: "Excluded by rules", value: f.excluded, color: "rgb(238 154 140 / 0.45)" },
                { label: "Eligible by rules", value: f.eligible, color: "rgb(143 220 171 / 0.45)" },
                { label: "Chart review", value: f.open, color: "rgb(var(--fg) / 0.7)" },
                { label: "Missing data", value: f.missing, color: "rgb(var(--fg) / 0.12)" },
                ...(v ? [
                  { label: "Eligible", value: v.eligible, color: "var(--green)" },
                  { label: "Needs review", value: v.needs_review, color: "var(--amber)" },
                ] : []),
              ]} />
            ) : <div className="empty">Waiting for prescreen</div>}
            {v && f && (
              <div className="grid g3" style={{ marginTop: 16 }}>
                <div><div style={{ fontSize: 26, fontWeight: 300, color: "var(--green)" }}><CountUp value={v.eligible} /></div><div className="lbl">eligible</div></div>
                <div><div style={{ fontSize: 26, fontWeight: 300 }}>+<CountUp value={noteEligible} /></div><div className="lbl">found only through notes</div></div>
                <div><div style={{ fontSize: 26, fontWeight: 300 }}>{s.questions ?? "–"}</div><div className="lbl">chart questions</div></div>
              </div>
            )}
          </div>
          {usage && usage.calls > 0 && (
            <div className="panel flush">
              <div className="panel-head"><h2>Agents</h2><span className="muted small">{usage.calls} calls · {(usage.tokens / 1000).toFixed(1)}k tokens · {usage.failed} failed</span></div>
              <table>
                <thead><tr><th>Agent</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">Time</th><th className="num">Failed</th></tr></thead>
                <tbody>
                  {agents.map(([name, a]) => (
                    <tr key={name}><td><strong>{name}</strong></td><td className="num">{a.calls}</td>
                      <td className="num">{((a.tokens_in + a.tokens_out) / 1000).toFixed(1)}k</td><td className="num">{secs(a.ms)}</td>
                      <td className="num">{a.failed || "–"}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="row small muted" style={{ padding: "10px 18px" }}>
                {Object.entries(usage.models).map(([m, n]) => <span key={m} className="chip">{m} × {n}</span>)}
              </div>
            </div>
          )}
        </div>
        <div className="panel">
          <div className="panel-head"><h2>Activity</h2><span className="muted small">{events.length} events</span></div>
          <div className="feed" ref={feed}>
            {events.map((e) => (
              <div key={e.seq} className={`ev ${e.kind}`} title={e.data?.output || ""}>
                <span className="t">{clock(e.at)}</span>
                <span className="a">{e.agent}{e.patient ? <span className="faint mono"> {e.patient}</span> : null}</span>
                <span className="x">{e.text}</span>
              </div>
            ))}
            {!events.length && <div className="empty">No events</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

function Candidates({ run, criteria }: { run: Run; criteria: CriteriaSet | null }) {
  const rows = useLoad(() => api.results(run.id), [run.id, run.status]);
  const [filter, setFilter] = useState<string>("eligible");
  const [open, setOpen] = useState<string | null>(null);
  const kinds = useMemo(() => Object.fromEntries((criteria?.criteria || []).map((c) => [c.id, c.kind])), [criteria]);
  const all = rows.data || [];
  const shown = all.filter((r) => filter === "all" ? true : filter === "charted" ? r.stage === "open" : r.verdict === filter);
  const n = (k: string) => all.filter((r) => r.verdict === k).length;
  const ids = criteria?.criteria.filter((c) => c.screenable !== false && c.atoms.length).map((c) => c.id) || [];
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        {[["eligible", `Eligible ${n("eligible")}`], ["needs_review", `Needs review ${n("needs_review")}`],
          ["charted", `Chart-reviewed ${all.filter((r) => r.stage === "open").length}`], ["ineligible", `Ineligible ${n("ineligible")}`], ["all", `All ${all.length}`]]
          .map(([k, label]) => <button key={k} className={`btn sm ${filter === k ? "primary" : ""}`} onClick={() => setFilter(k)}>{label}</button>)}
        <span className="grow" />
        <div className="legend"><span><span className="cstat pass">I1</span>passes</span><span><span className="cstat fail">E1</span>fails</span><span><span className="cstat open">I4</span>open</span></div>
      </div>
      <div className="panel flush">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Patient</th><th>Age</th><th>Clinic</th><th>Verdict</th><th>Passing</th><th>Criteria</th><th className="num">From notes</th><th className="num">Flags</th></tr></thead>
            <tbody>
              {shown.map((r: ResultRow) => (
                <tr key={r.person_id} className="hover" onClick={() => setOpen(r.person_id)}>
                  <td><strong>{r.person.name}</strong><div className="faint mono">{r.person_id}</div></td>
                  <td>{r.person.age} {r.person.sex}</td>
                  <td className="small">{r.person.clinic.split(" ").slice(0, 2).join(" ")}</td>
                  <td><VerdictPill v={r.verdict} /></td>
                  <td>{(() => { const pass = ids.filter((id) => (kinds[id] === "inclusion" ? r.criteria[id] === "met" : r.criteria[id] === "not_met")).length;
                    return <TickMeter value={pass} max={ids.length} ticks={ids.length} label={`${pass}/${ids.length}`} title="criteria passing" />; })()}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{ids.map((id) => <StatusCell key={id} id={id} status={r.criteria[id]} kind={kinds[id]} />)}</td>
                  <td className="num">{r.notes_used || "–"}</td>
                  <td className="num">{r.flags ? <span className="pill amber">{r.flags}</span> : "–"}</td>
                </tr>
              ))}
              {!shown.length && <tr><td colSpan={8}>{rows.data ? <div className="empty">None</div> : <div style={{ padding: 16 }}><Skeleton rows={6} /></div>}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      {open && <PatientDrawer runId={run.id} personId={open} onClose={() => setOpen(null)} />}
    </>
  );
}

const GROUP_LABEL: Record<string, string> = { race: "Race", ethnicity: "Ethnicity", sex: "Sex", clinic: "Clinic", age_band: "Age" };

function Equity({ run }: { run: Run }) {
  const [group, setGroup] = useState("clinic");
  const eq = run.summary?.equity;
  if (!eq) return <div className="panel empty">Available after adjudication</div>;
  const rows = Object.entries(eq[group] || {}) as [string, any][];
  const max = Math.max(1, ...rows.map(([, r]) => r.pool));
  return (
    <div className="panel flush">
      <div className="panel-head">
        <div className="tabs" style={{ marginBottom: 0 }}>
          {Object.keys(GROUP_LABEL).map((g) => <button key={g} className={`tab ${group === g ? "on" : ""}`} onClick={() => setGroup(g)}>{GROUP_LABEL[g]}</button>)}
        </div>
        <div className="legend">
          <span><span className="dot" style={{ background: "rgb(143 220 171 / 0.45)" }} />eligible by rules</span>
          <span><span className="dot" style={{ background: "var(--teal)" }} />eligible through notes</span>
          <span><span className="dot amber" />needs review</span>
        </div>
      </div>
      <table>
        <thead><tr><th>{GROUP_LABEL[group]}</th><th className="num">Patients</th><th style={{ width: "34%" }} /><th className="num">Eligible</th>
          <th className="num">By rules only</th><th className="num">Through notes</th><th className="num">Needed notes</th><th className="num">Needs review</th></tr></thead>
        <tbody>
          {rows.map(([k, r]) => {
            const viaNotes = Math.max(0, (r.eligible || 0) - (r.structured_eligible || 0));
            return (
              <tr key={k}>
                <td><strong>{k}</strong></td>
                <td className="num">{r.pool}</td>
                <td>
                  <div className="bar-mini" style={{ height: 10, width: `${(r.pool / max) * 100}%` }}>
                    <span style={{ width: `${((r.structured_eligible || 0) / r.pool) * 100}%`, background: "rgb(143 220 171 / 0.45)" }} />
                    <span style={{ width: `${(viaNotes / r.pool) * 100}%`, background: "var(--teal)" }} />
                    <span style={{ width: `${((r.needs_review || 0) / r.pool) * 100}%`, background: "var(--amber)" }} />
                  </div>
                </td>
                <td className="num"><strong>{r.eligible || 0}</strong></td>
                <td className="num">{r.structured_eligible || 0}</td>
                <td className="num" style={{ color: viaNotes ? "var(--teal)" : undefined }}>{viaNotes || "–"}</td>
                <td className="num">{r.needed_notes || 0} <span className="faint">({Math.round(((r.needed_notes || 0) / r.pool) * 100)}%)</span></td>
                <td className="num">{r.needs_review || "–"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Referrals({ run, onDone, onError }: { run: Run; onDone: () => void; onError: (t: string) => void }) {
  const list = useLoad(() => api.outreach(run.id), [run.id, run.status]);
  const [edits, setEdits] = useState<Record<string, { decision?: string; subject?: string; body?: string; reason?: string }>>({});
  const [sending, setSending] = useState(false);
  const waiting = run.status === "awaiting_approval";
  const set = (pid: string, patch: object) => setEdits((e) => ({ ...e, [pid]: { ...e[pid], ...patch } }));
  const items = list.data || [];
  const decided = items.filter((o) => edits[o.person_id]?.decision).length;
  const open = items.filter((o) => o.status === "proposed");
  const ready = open.length > 0 && open.every((o) => edits[o.person_id]?.decision);
  const confirm = useConfirm();
  const submit = async () => {
    const approve = open.filter((o) => edits[o.person_id]?.decision === "approve").length;
    const reject = open.length - approve;
    const ok = await confirm({
      title: "Submit decisions", tone: approve ? "warning" : "primary", confirmLabel: `Submit ${open.length} decision(s)`,
      message: <><b>{approve}</b> referral(s) approved and <b>{reject}</b> rejected. The decisions cannot be changed after this.</>,
      details: [approve ? "Approved referrals go to the outbox: in dry-run mode they are recorded, not sent" : "No referral is approved",
        "Each decision is in the audit log with your name", ...(reject ? ["Rejection reasons become feedback for the learning loop"] : [])],
    });
    if (!ok) return;
    setSending(true);
    try {
      await api.decide(run.id, open.map((o) => ({
        person_id: o.person_id, decision: edits[o.person_id].decision, subject: edits[o.person_id].subject, body: edits[o.person_id].body,
        reason: edits[o.person_id].reason,
      })));
      onDone();
    } catch (e: any) {
      onError(e.message);
    } finally { setSending(false); }
  };
  if (!items.length) return <div className="panel empty">{run.status === "completed" || waiting ? "No eligible patients" : "Drafts appear after adjudication"}</div>;
  return (
    <>
      {waiting && (
        <div className="panel row" style={{ marginBottom: 14 }}>
          <span className="pill amber">Awaiting approval</span>
          <span className="muted small">{decided}/{open.length} decided{ready ? "" : " · decide every draft"}</span>
          <span className="grow" />
          <button className="btn" onClick={() => setEdits(Object.fromEntries(items.map((o) => [o.person_id, { ...edits[o.person_id], decision: "approve" }])))}>
            <Check />Approve all
          </button>
          <button className="btn primary" disabled={!ready || sending} onClick={submit}><Send />Submit decisions</button>
        </div>
      )}
      <div className="grid g2">
        {items.map((o: Outreach) => {
          const e = edits[o.person_id] || {};
          return (
            <div key={o.person_id} className="panel">
              <div className="row" style={{ marginBottom: 10 }}>
                <span className="mono faint">{o.person_id}</span>
                <span className="muted small">to {o.to_name}</span>
                <span className="grow" />
                {o.status !== "proposed" ? <span className={`pill ${o.status.startsWith("approved") || o.status === "sent" ? "green" : "red"}`}>{o.status}</span> : waiting && (
                  <>
                    <button className={`btn sm ${e.decision === "approve" ? "good" : ""}`} onClick={() => set(o.person_id, { decision: "approve" })}><Check />Approve</button>
                    <button className={`btn sm ${e.decision === "reject" ? "bad" : ""}`} onClick={() => set(o.person_id, { decision: "reject" })}><X />Reject</button>
                  </>
                )}
              </div>
              {waiting && o.status === "proposed" ? (
                <>
                  <input style={{ width: "100%", marginBottom: 8 }} value={e.subject ?? o.subject} onChange={(x) => set(o.person_id, { subject: x.target.value })} />
                  <textarea rows={9} value={e.body ?? o.body} onChange={(x) => set(o.person_id, { body: x.target.value })} />
                  {e.decision === "reject" && (
                    <input style={{ width: "100%", marginTop: 8 }} placeholder="Why reject? The learning loop uses this (optional)"
                      value={e.reason ?? ""} onChange={(x) => set(o.person_id, { reason: x.target.value })} />
                  )}
                </>
              ) : (
                <>
                  <h3 style={{ marginBottom: 8 }}>{o.subject}</h3>
                  <div className="letter">{o.body}</div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

function Criteria({ id, data, onChange, toast }: { id: string; data: any; onChange: () => void; toast: (t: string) => void }) {
  const cset: CriteriaSet | null = data.criteria;
  const builds = (data.runs || []).filter((r: Run) => r.kind === "criteria");
  const building = builds.some((r: Run) => ["queued", "running"].includes(r.status));
  useInterval(onChange, building ? 3000 : null);
  const [view, setView] = useState<"approved" | string>("approved");
  const draft = data.drafts.find((d: any) => d.id === view);
  const shown: CriteriaSet | null = draft ? draft.data : cset;
  const confirm = useConfirm();
  const build = async () => {
    if (!(await confirm({ title: "Build criteria with agents", confirmLabel: "Build draft",
      message: "The criteria, terminology and reviewer agents read the protocol and draft computable criteria. This makes many model calls.",
      details: ["The draft does not replace the approved criteria until you approve it"] }))) return;
    try { await api.buildCriteria(id); onChange(); } catch (e: any) { toast(e.message); }
  };
  const approve = async () => {
    if (!(await confirm({ title: "Approve criteria", tone: "warning", confirmLabel: `Approve as version ${(cset?.version ?? 0) + 1}`,
      message: <>This draft becomes the approved criteria of {id}. Every new screening run decides with it.</>,
      details: [`${draft.warnings.length} warning(s) in the draft: read them first`, "The current version stays in the history; running screens keep their copy"] }))) return;
    try { await api.approveCriteria(id, draft.id); toast("Criteria approved"); setView("approved"); onChange(); }
    catch (e: any) { toast(e.message); }
  };
  return (
    <div className="stack">
      <div className="panel row">
        <select value={view} onChange={(e) => setView(e.target.value)}>
          <option value="approved">{data.criteria_source ? `Approved${data.criteria_source.includes("library") ? " · library" : ""}` : "No approved criteria"}</option>
          {data.drafts.map((d: any) => <option key={d.id} value={d.id}>Agent draft {d.id} · {ago(d.created_at)}</option>)}
        </select>
        {shown?.approved_by && !draft && <span className="muted small">by {shown.approved_by} · {shown.approved_at}</span>}
        {draft && <span className="pill amber">{draft.warnings.length} warnings</span>}
        <span className="grow" />
        {building && <span className="row muted small"><RefreshCw size={14} className="spin" />Building</span>}
        {draft && <button className="btn good" onClick={approve}><Check />Approve draft</button>}
        <button className="btn" disabled={building} onClick={build}><Sparkles />Build with agents</button>
      </div>
      {draft && draft.warnings.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h3>Warnings</h3></div>
          {draft.warnings.map((w: string, i: number) => <div key={i} className="small" style={{ color: "var(--amber-text)", padding: "3px 0" }}>{w}</div>)}
        </div>
      )}
      {shown ? (
        <div className="panel flush">
          <table>
            <thead><tr><th>ID</th><th style={{ width: "42%" }}>Protocol text</th><th>Computable</th><th>Codes</th></tr></thead>
            <tbody>
              {shown.criteria.map((c) => (
                <tr key={c.id}>
                  <td><span className={`pill ${c.screenable === false ? "outline" : c.kind === "inclusion" ? "green" : "red"}`}>{c.id}</span></td>
                  <td className="small">{c.text.replace(/\\/g, "")}</td>
                  <td className="small">
                    {c.screenable === false ? <span className="faint">At the study visit</span> :
                      c.atoms.map((a, i) => <div key={i}>{i > 0 && <span className="faint">{c.combine === "any" ? "or " : "and "}</span>}{atomText(a)}</div>)}
                  </td>
                  <td>{[...new Set(c.atoms.flatMap((a) => [...(a.codes || []), ...(a.all_of || []).flatMap((b) => b.codes || [])]))].slice(0, 8)
                    .map((x) => <span key={x} className="chip">{x}</span>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="panel empty">No approved criteria</div>}
    </div>
  );
}

function Protocol({ trial }: { trial: any }) {
  return (
    <div className="split">
      <div className="panel">
        <div className="panel-head"><h2>Eligibility criteria</h2>
          <a className="btn sm" href={`https://clinicaltrials.gov/study/${trial.nct_id}`} target="_blank" rel="noreferrer"><ExternalLink />ClinicalTrials.gov</a></div>
        <div className="letter" style={{ maxHeight: 640, overflowY: "auto" }}>{trial.eligibility?.replace(/\\/g, "")}</div>
      </div>
      <div className="panel">
        <dl className="kv">
          <dt>Official title</dt><dd>{trial.official_title}</dd>
          <dt>Conditions</dt><dd>{trial.conditions?.join(", ")}</dd>
          <dt>Ages</dt><dd>{trial.min_age} – {trial.max_age || "no limit"}</dd>
          <dt>Sex</dt><dd>{trial.sex}</dd>
          <dt>Indiana sites</dt><dd>{trial.indiana_sites?.join(", ")}</dd>
          <dt>Last update</dt><dd>{trial.last_update}</dd>
          <dt>Fetched</dt><dd>{trial.fetched}</dd>
        </dl>
      </div>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  add_keyword: "Add keyword", remove_keyword: "Remove keyword", add_code: "Add code", hint: "Reading hint",
  data_gap: "Data gap", protocol_check: "Protocol check",
};

function Learning({ id, data, onChange, toast }: { id: string; data: any; onChange: () => void; toast: (t: string) => void }) {
  const fb = useLoad(() => api.trialFeedback(id), [id]);
  const ls = useLoad(() => api.lessons(id), [id]);
  const learning = (data.runs || []).find((r: Run) => r.kind === "learning" && ["queued", "running"].includes(r.status));
  useInterval(() => { fb.reload(); ls.reload(); onChange(); }, learning ? 2500 : null);
  const open = (fb.data || []).filter((f) => !f.lesson_id);
  const lessons = ls.data || [];
  const confirm = useConfirm();
  const learn = async () => {
    if (!(await confirm({ title: "Learn from feedback", confirmLabel: "Start reflection",
      message: <>The reflection agent reads {open.length} correction(s) with their notes and proposes lessons. It changes nothing by itself.</>,
      details: ["It reads patient notes: it runs on the chart model chain (local unless cloud models may read notes)"] }))) return;
    try { await api.learn(id); onChange(); } catch (e: any) { toast(e.message); }
  };
  const decide = async (l: Lesson, approve: boolean) => {
    if (approve) {
      const changes = !["data_gap", "protocol_check"].includes(l.kind);
      const warn = (l.impact?.warnings || []).length > 0;
      const ok = await confirm({
        title: "Approve lesson", tone: warn ? "danger" : changes ? "warning" : "primary",
        confirmLabel: changes ? `Approve as criteria v${(data.criteria?.version ?? 1) + 1}` : "Record lesson",
        message: changes ? <><b>{KIND_LABEL[l.kind]}</b> on {l.criterion_id}: “{l.value}”. New screening runs use the new criteria version.</>
          : <>This lesson changes no rule. It is recorded for the team.</>,
        details: [
          ...(l.impact ? [`${l.impact.changed} patient(s) change prescreen stage · open facts ${l.impact.open_facts[0]} → ${l.impact.open_facts[1]}`] : []),
          ...(l.impact?.warnings || []).map((w) => `⚠ ${w}`),
          ...(changes ? ["The old version stays in the history"] : []),
        ],
      });
      if (!ok) return;
    }
    try {
      const r = await api.decideLesson(l.id, approve);
      toast(approve ? (r.version ? `Criteria version ${r.version} approved` : "Lesson recorded") : "Lesson rejected");
      ls.reload(); onChange();
    } catch (e: any) { toast(e.message); }
  };
  const cset: CriteriaSet | null = data.criteria;
  return (
    <div className="stack">
      <div className="grid g4">
        <div className="panel kpi"><div className="v"><CountUp value={(fb.data || []).length} /></div><div className="l">Feedback</div><div className="d">{open.length} open</div></div>
        <div className="panel kpi"><div className="v amber"><CountUp value={lessons.filter((l) => l.status === "proposed").length} /></div><div className="l">Lessons to decide</div></div>
        <div className="panel kpi"><div className="v green"><CountUp value={lessons.filter((l) => l.status === "approved").length} /></div><div className="l">Approved</div></div>
        <div className="panel kpi"><div className="v">v{cset?.version ?? 1}</div><div className="l">Criteria version</div></div>
      </div>
      <div className="split">
        <div className="stack">
          <div className="panel flush">
            <div className="panel-head">
              <h2>Lessons</h2>
              <button className="btn sm primary" disabled={!open.length || !!learning} onClick={learn}>
                {learning ? <RefreshCw className="spin" /> : <Sparkles />}{learning ? "Reflecting" : `Learn from ${open.length} feedback`}
              </button>
            </div>
            <div style={{ padding: "4px 20px 18px", display: "grid", gap: 10 }}>
              {!ls.data && <Skeleton rows={4} />}
              {ls.data && !lessons.length && <div className="empty">No lessons yet · correct a patient, then learn</div>}
              {lessons.map((l) => (
                <div key={l.id} className={`lesson ${l.status === "blocked" ? "blocked" : ""}`}>
                  <div className="row" style={{ marginBottom: 6 }}>
                    <span className={`pill ${l.kind === "hint" ? "blue" : l.kind.startsWith("add") || l.kind.startsWith("remove") ? "teal" : "outline"}`}>{KIND_LABEL[l.kind] || l.kind}</span>
                    {l.criterion_id && <span className="pill outline">{l.criterion_id}{l.atom_index ? ` · fact ${l.atom_index}` : ""}</span>}
                    <span className="grow" />
                    <span className={`pill ${l.status === "approved" ? "green" : l.status === "proposed" ? "amber" : "red"}`}>{l.status}</span>
                  </div>
                  <div style={{ fontWeight: 500, marginBottom: 4 }}>{l.value}</div>
                  <div className="small muted">{l.rationale}</div>
                  {l.problems.map((x, i) => <div key={i} className="small" style={{ color: "var(--red)", marginTop: 4 }}>blocked: {x}</div>)}
                  {l.impact && (
                    <div className="small" style={{ marginTop: 8 }}>
                      <span className="lbl">Impact on all patients · no model</span>
                      <div className="moves" style={{ marginTop: 4 }}>
                        <span className="chip">{l.impact.changed} change stage</span>
                        <span className="chip">open facts {l.impact.open_facts[0]} → {l.impact.open_facts[1]}</span>
                        {Object.entries(l.impact.moves).map(([k, v]) => <span key={k} className="chip">{k} × {v}</span>)}
                        {Object.entries(l.impact.focus).map(([p, [x, y]]) => <span key={p} className="chip">{p}: {x}{x !== y ? ` → ${y}` : " (same)"}</span>)}
                      </div>
                      {(l.impact.warnings || []).map((w, i) => <div key={i} className="small" style={{ color: "var(--amber)", marginTop: 6 }}>⚠ {w}</div>)}
                    </div>
                  )}
                  {l.kind === "hint" && l.status === "proposed" && <div className="small faint" style={{ marginTop: 6 }}>A hint changes what the chart agent reads: the next screening run measures it.</div>}
                  {l.status === "proposed" && (
                    <div className="row" style={{ marginTop: 10 }}>
                      <button className="btn sm good" onClick={() => decide(l, true)}><Check />Approve</button>
                      <button className="btn sm bad" onClick={() => decide(l, false)}><X />Reject</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="stack">
          <div className="panel flush">
            <div className="panel-head"><h2>Feedback</h2></div>
            <table>
              <thead><tr><th>Patient</th><th>On</th><th>System → right</th><th>Why</th></tr></thead>
              <tbody>
                {(fb.data || []).map((f) => (
                  <tr key={f.id}>
                    <td className="mono">{f.person_id}</td>
                    <td>{f.criterion_id || "verdict"}</td>
                    <td className="small">{f.system_value.replace("_", " ")} → <strong>{f.correct_value.replace("_", " ")}</strong></td>
                    <td className="small muted">{f.reason || "–"}{f.lesson_id && <span className="pill outline" style={{ marginLeft: 6 }}>used</span>}</td>
                  </tr>
                ))}
                {fb.data && !fb.data.length && <tr><td colSpan={4} className="empty">Open a patient and press Correct</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="panel">
            <div className="panel-head"><h2>Versions</h2></div>
            {(cset?.changes || []).length ? (cset?.changes || []).slice().reverse().map((c, i) => <div key={i} className="small" style={{ padding: "4px 0" }}>{c}</div>)
              : <div className="small muted">v1 · as approved</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

function Compare({ runs }: { runs: Run[] }) {
  const done = runs.filter((r) => r.summary?.verdicts);
  // The runs can arrive after the first render, so the defaults are read on each render.
  const [pickA, setA] = useState("");
  const [pickB, setB] = useState("");
  const a = pickA || done[1]?.id || "";
  const b = pickB || done[0]?.id || "";
  const cmp = useLoad(() => (a && b && a !== b ? api.compare(a, b) : Promise.resolve(null)), [a, b]);
  if (done.length < 2) return <div className="panel empty">Run the screening two times to compare</div>;
  const d = cmp.data;
  const pick = (v: string, set: (x: string) => void) => (
    <select value={v} onChange={(e) => set(e.target.value)}>
      {done.map((r) => <option key={r.id} value={r.id}>{r.id} · {ago(r.created_at)}</option>)}
    </select>
  );
  return (
    <div className="stack">
      <div className="panel row">
        <span className="lbl">Before</span>{pick(a, setA)}<span className="lbl">After</span>{pick(b, setB)}
      </div>
      {!d ? <div className="panel"><Skeleton rows={5} /></div> : (
        <>
          <div className="grid g4">
            {(["eligible", "needs_review", "ineligible"] as const).map((k) => (
              <div key={k} className="panel kpi">
                <div className="v">{d.verdicts[0]?.[k] ?? "–"} → <span className={k === "eligible" ? "green" : k === "needs_review" ? "amber" : ""}>{d.verdicts[1]?.[k] ?? "–"}</span></div>
                <div className="l">{k.replace("_", " ")}</div>
              </div>
            ))}
            <div className="panel kpi"><div className="v"><CountUp value={d.moved.length} /></div><div className="l">Verdicts changed</div></div>
          </div>
          <div className="split">
            <div className="panel flush">
              <div className="panel-head"><h2>Patients that changed</h2><span className="legend">{Object.entries(d.moves).map(([k, v]) => `${k.replace(/_/g, " ")} × ${v}`).join(" · ")}</span></div>
              <table>
                <thead><tr><th>Patient</th><th>Before</th><th>After</th></tr></thead>
                <tbody>
                  {d.moved.map((m: any) => <tr key={m.person_id}><td className="mono">{m.person_id}</td><td><VerdictPill v={m.a} /></td><td><VerdictPill v={m.b} /></td></tr>)}
                  {!d.moved.length && <tr><td colSpan={3} className="empty">No verdict changed</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="panel">
              <div className="panel-head"><h2>What was different</h2></div>
              <dl className="kv">
                <dt>Criteria</dt><dd>v{d.versions[0]} → v{d.versions[1]}{d.criteria.length ? ` · ${d.criteria.map((c: any) => `${c.id} ${c.change}`).join(", ")}` : " · same"}</dd>
                <dt>LOINC mappings</dt><dd>{Object.keys(d.mappings[0]).length} → {Object.keys(d.mappings[1]).length}</dd>
                <dt>Models</dt><dd>{Object.keys(d.models[0]).join(", ") || "–"} → {Object.keys(d.models[1]).join(", ") || "–"}</dd>
                <dt>Started</dt><dd>{ago(d.a.created_at)} → {ago(d.b.created_at)}</dd>
              </dl>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
