import { useMemo, useState } from "react";
import { BookOpenCheck, ChevronDown, FlaskConical, KeyRound, Lightbulb, Link2, ListChecks, MessageSquareText, Play, Search, Send, ShieldAlert, ShieldCheck } from "lucide-react";
import { api } from "../api";
import { CountUp, Skeleton, ago, useLoad } from "../ui";
import { Meter } from "../charts";

type Entry = { seq: number; at: string; actor: string; action: string; detail: string; prev: string; hash: string };
type Group = { key: string; actor: string; action: string; entries: Entry[]; at: string };

const CAT: Record<string, { label: string; icon: any; tone: string }> = {
  screen: { label: "Screening", icon: Play, tone: "blue" },
  outreach: { label: "Referrals", icon: Send, tone: "green" },
  criteria: { label: "Criteria", icon: ListChecks, tone: "violet" },
  mapping: { label: "Terminology", icon: BookOpenCheck, tone: "violet" },
  lesson: { label: "Learning", icon: Lightbulb, tone: "amber" },
  feedback: { label: "Feedback", icon: MessageSquareText, tone: "amber" },
  models: { label: "Models", icon: KeyRound, tone: "blue" },
  trial: { label: "Trials", icon: FlaskConical, tone: "blue" },
};
const VERB: Record<string, [string, string]> = {
  "screen.start": ["started a screening run", "started {n} screening runs"],
  "outreach.approve": ["approved a referral", "approved {n} referrals"],
  "outreach.reject": ["rejected a referral", "rejected {n} referrals"],
  "mapping.approve": ["approved a LOINC mapping", "approved {n} LOINC mappings"],
  "mapping.reject": ["rejected a LOINC mapping", "rejected {n} LOINC mappings"],
  "lesson.approve": ["approved a lesson", "approved {n} lessons"],
  "lesson.reject": ["rejected a lesson", "rejected {n} lessons"],
  "criteria.approve": ["approved criteria", "approved criteria {n} times"],
  "feedback.add": ["corrected a result", "corrected {n} results"],
  "models.key_saved": ["saved an API key", "saved {n} API keys"],
  "models.key_removed": ["removed an API key", "removed {n} API keys"],
  "models.model_chosen": ["chose a model", "chose {n} models"],
  "models.routes_saved": ["changed the model chains", "changed the model chains {n} times"],
  "trial.add": ["imported a trial", "imported {n} trials"],
};

const cat = (action: string) => CAT[action.split(".")[0]] || { label: action.split(".")[0], icon: ListChecks, tone: "blue" };
const tone = (e: Entry) => /approve/.test(e.action) ? "green" : /reject|removed/.test(e.action) ? "red"
  : e.action === "models.cloud_phi" ? (e.detail === "on" ? "red" : "green") : cat(e.action).tone;
export function describe(action: string, detail: string, n = 1) {
  if (action === "models.cloud_phi") return detail === "on" ? "allowed cloud models to read notes" : "kept patient notes on the local model";
  const v = VERB[action];
  return v ? (n > 1 ? v[1].replace("{n}", String(n)) : v[0]) : action.replace(".", " ");
}
const sentence = (g: Group) => describe(g.action, g.entries[0].detail, g.entries.length);

const NCT = /NCT\d{8}/g, RUN = /\brun ([0-9a-f]{10})\b/g, PID = /\bP\d{4}\b/g;
function parse(detail: string) {
  const ncts = [...new Set(detail.match(NCT) || [])];
  const runs = [...new Set([...detail.matchAll(RUN)].map((m) => m[1]))];
  const pids = [...new Set(detail.match(PID) || [])];
  const rest = detail.replace(NCT, "").replace(RUN, "").replace(PID, "").replace(/\bpatient\b/g, "").replace(/\s+/g, " ").trim();
  return { ncts, runs, pids, rest };
}
const initials = (name: string) => name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
const local = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayKey = (iso: string) => local(new Date(iso));  // the reader's own day, not the UTC day
const dayLabel = (key: string) => new Date(key + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
const clockOf = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

function group(entries: Entry[]): Group[] {
  const out: Group[] = [];
  for (const e of entries) {  // newest first; one card for a burst of the same action by the same person on the same run
    const last = out[out.length - 1];
    const run = (e.detail.match(/\brun ([0-9a-f]{10})\b/) || [])[1];
    const lastRun = last && (last.entries[0].detail.match(/\brun ([0-9a-f]{10})\b/) || [])[1];
    const close = last && Math.abs(new Date(last.entries[last.entries.length - 1].at).getTime() - new Date(e.at).getTime()) < 5 * 60_000;
    if (last && close && last.actor === e.actor && last.action === e.action && run === lastRun && dayKey(last.at) === dayKey(e.at)) last.entries.push(e);
    else out.push({ key: String(e.seq), actor: e.actor, action: e.action, entries: [e], at: e.at });
  }
  return out;
}

function DayStrip({ entries }: { entries: Entry[] }) {
  const days = useMemo(() => {
    const today = new Date();
    return Array.from({ length: 14 }, (_, i) => {
      const d = new Date(today);
      d.setDate(today.getDate() - 13 + i);
      const key = local(d);
      return { key, d, list: entries.filter((e) => dayKey(e.at) === key) };
    });
  }, [entries]);
  return (
    <div className="daystrip">
      {days.map((x) => (
        <div key={x.key} className="ds-col" title={`${dayLabel(x.key)}: ${x.list.length} entr${x.list.length === 1 ? "y" : "ies"}`}>
          <div className="ds-beads">
            {x.list.slice(0, 14).map((e) => <i key={e.seq} className={`bead ${tone(e)}`} />)}
            {x.list.length > 14 && <span className="ds-more">+{x.list.length - 14}</span>}
          </div>
          <span className="ds-n">{x.list.length || ""}</span>
          <span className="ds-d">{x.d.toLocaleDateString(undefined, { weekday: "narrow" })}<b>{x.d.getDate()}</b></span>
        </div>
      ))}
    </div>
  );
}

function Card({ g, broken }: { g: Group; broken: number | null }) {
  const [open, setOpen] = useState(false);
  const first = g.entries[g.entries.length - 1], last = g.entries[0];
  const c = cat(g.action);
  const t = tone(last);
  const parsed = g.entries.map((e) => parse(e.detail));
  const ncts = [...new Set(parsed.flatMap((p) => p.ncts))], runs = [...new Set(parsed.flatMap((p) => p.runs))], pids = [...new Set(parsed.flatMap((p) => p.pids))];
  const rest = g.entries.length === 1 ? parsed[0].rest : "";
  const bad = broken != null && g.entries.some((e) => e.seq >= broken);
  return (
    <div className={`ae ${open ? "open" : ""} ${bad ? "bad" : ""}`}>
      <div className="ae-rail"><span className={`ae-node ${t}`}><c.icon /></span></div>
      <div className="ae-card" onClick={() => setOpen(!open)}>
        <div className="ae-top">
          <span className="ae-avatar" title={g.actor}>{initials(g.actor)}</span>
          <div className="ae-text"><b>{g.actor}</b> {sentence(g)}</div>
          <span className="ae-time" title={new Date(last.at).toLocaleString()}>{clockOf(last.at)}</span>
          <ChevronDown className="ae-chev" />
        </div>
        <div className="ae-chips">
          <span className={`pill ${t === "violet" ? "purple" : t}`}>{c.label}</span>
          {ncts.map((n) => <span key={n} className="chip">{n}</span>)}
          {runs.map((r) => <span key={r} className="chip">run {r}</span>)}
          {pids.slice(0, 6).map((p) => <span key={p} className="chip">{p}</span>)}
          {pids.length > 6 && <span className="chip" title={pids.slice(6).join(", ")}>+{pids.length - 6}</span>}
          {rest && <span className="ae-rest">{rest}</span>}
        </div>
        <div className="ae-hash"><Link2 />{last.hash.slice(0, 10)}<span>←</span>{first.prev.slice(0, 10)}{g.entries.length > 1 && <em>{g.entries.length} links</em>}</div>
        {open && (
          <div className="ae-detail" onClick={(e) => e.stopPropagation()}>
            {g.entries.map((e) => (
              <div key={e.seq} className={broken != null && e.seq >= broken ? "bad" : ""}>
                <span className="mono">#{e.seq}</span><span>{new Date(e.at).toLocaleString()}</span><span className="ae-raw">{e.detail}</span>
                <code title="hash of this entry">{e.hash}</code><code className="faint" title="hash of the entry before">{e.prev}</code>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Audit() {
  const a = useLoad(api.audit);
  const v = useLoad(api.auditVerify);
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const entries: Entry[] = (a.data as Entry[]) || [];
  const ascending = useMemo(() => [...entries].sort((x, y) => x.seq - y.seq), [entries]);
  const brokenSeq = v.data && !v.data.ok ? ascending[(v.data.broken_at?.position || 1) - 1]?.seq ?? null : null;
  const counts = useMemo(() => {
    const m: Record<string, number> = {};
    entries.forEach((e) => { const k = e.action.split(".")[0]; m[k] = (m[k] || 0) + 1; });
    return m;
  }, [entries]);
  const shown = entries.filter((e) => (!filter || e.action.startsWith(filter + ".")) &&
    (!q || `${e.actor} ${e.action} ${e.detail}`.toLowerCase().includes(q.toLowerCase())));
  const groups = group(shown);
  const byDay: [string, Group[]][] = [];
  groups.forEach((g) => {
    const k = dayKey(g.at);
    if (!byDay.length || byDay[byDay.length - 1][0] !== k) byDay.push([k, []]);
    byDay[byDay.length - 1][1].push(g);
  });
  const approvals = entries.filter((e) => /approve/.test(e.action)).length;
  const rejections = entries.filter((e) => /reject/.test(e.action)).length;
  const actors = [...new Set(entries.map((e) => e.actor))];

  return (
    <>
      <div className="page-head">
        <div><h1>Audit log</h1><div className="sub">Decisions · runs · settings · hash chain</div></div>
        {v.data && <span className={`pill ${v.data.ok ? "green" : "red"}`}>{v.data.ok ? <ShieldCheck /> : <ShieldAlert />}
          {v.data.ok ? `Chain verified · ${v.data.entries}` : `Chain broken at entry ${v.data.broken_at?.position}`}</span>}
      </div>

      {!a.data ? <div className="panel"><Skeleton rows={8} /></div> : <>
        <div className="grid g4" style={{ marginBottom: 14 }}>
          <div className="panel seckpi">
            <div className="seckpi-l">Entries</div>
            <div className="seckpi-v"><CountUp value={entries.length} /></div>
            <Meter parts={Object.entries(counts).sort((x, y) => y[1] - x[1]).map(([k, n], i) => ({ value: n, tone: cat(k + ".x").tone, striped: i === 0 }))} />
            <div className="seckpi-f"><span>{Object.keys(counts).length} kinds</span><span>{entries.length ? ago(entries[0].at) : "–"}</span></div>
          </div>
          <div className="panel seckpi">
            <div className="seckpi-l">Decisions</div>
            <div className="seckpi-v"><CountUp value={approvals + rejections} /></div>
            <Meter parts={[{ value: approvals, tone: "green", striped: true }, { value: rejections, tone: "red" }]} />
            <div className="seckpi-f"><span>{approvals} approved</span><span>{rejections} rejected</span></div>
          </div>
          <div className="panel seckpi">
            <div className="seckpi-l">People</div>
            <div className="seckpi-v"><CountUp value={actors.length} /></div>
            <div className="avatars">{actors.slice(0, 6).map((x) => <span key={x} className="ae-avatar" title={x}>{initials(x)}</span>)}</div>
            <div className="seckpi-f"><span>{actors.slice(0, 2).join(", ")}</span><span>{actors.length > 2 ? `+${actors.length - 2}` : ""}</span></div>
          </div>
          <div className="panel seckpi">
            <div className="seckpi-l">Chain head</div>
            <div className="seckpi-v mono-v">{v.data?.ok ? (v.data as any).head?.slice(0, 8) : "broken"}</div>
            <div className={`chain-track ${v.data && !v.data.ok ? "bad" : ""}`} style={{ margin: "4px 6px 2px" }}><i style={{ width: "100%" }}><Link2 /></i></div>
            <div className="seckpi-f"><span>SHA-256</span><span>{v.data?.entries ?? 0} links</span></div>
          </div>
        </div>

        <div className="panel" style={{ marginBottom: 14 }}>
          <div className="panel-head">
            <h2>Activity, last 14 days</h2>
            <div className="legend"><span><i className="sq green" />Approved</span><span><i className="sq red" />Rejected</span><span><i className="sq blue" />Run or setting</span><span><i className="sq amber" />Learning</span></div>
          </div>
          <DayStrip entries={entries} />
        </div>

        <div className="audit-bar">
          <div className="audit-filters">
            <button className={`fchip ${!filter ? "on" : ""}`} onClick={() => setFilter("")}>All<span>{entries.length}</span></button>
            {Object.entries(counts).sort((x, y) => y[1] - x[1]).map(([k, n]) => {
              const C = cat(k + ".x");
              return <button key={k} className={`fchip ${filter === k ? "on" : ""}`} onClick={() => setFilter(filter === k ? "" : k)}><C.icon />{C.label}<span>{n}</span></button>;
            })}
          </div>
          <label className="audit-search"><Search /><input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} /></label>
        </div>

        {!groups.length && <div className="panel empty">Nothing matches</div>}
        {byDay.map(([day, gs]) => (
          <div key={day} className="audit-day">
            <div className="audit-day-h"><span>{dayLabel(day)}</span><span className="chip">{gs.reduce((n, g) => n + g.entries.length, 0)}</span></div>
            {gs.map((g) => <Card key={g.key} g={g} broken={brokenSeq} />)}
          </div>
        ))}
      </>}
    </>
  );
}
