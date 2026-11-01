import { useRef, useState } from "react";
import { FileText, MessageSquareWarning, ShieldAlert, ShieldCheck, X } from "lucide-react";
import TraceWaterfall from "../Trace";
import { api, type AtomResult, type CriterionResult, type Resolution } from "../api";
import { Skeleton, VerdictPill, statusTone, useLoad } from "../ui";

export default function PatientDrawer({ runId, personId, onClose }: { runId: string; personId: string; onClose: () => void }) {
  const d = useLoad(() => api.patient(runId, personId), [runId, personId]);
  const trace = useLoad(() => api.trace(runId, personId), [runId, personId]);
  const [view, setView] = useState<"evidence" | "trace">("evidence");
  const [fb, setFb] = useState<{ open: boolean; target: string; correct: string; reason: string; msg: string }>(
    { open: false, target: "", correct: "", reason: "", msg: "" });
  const [focus, setFocus] = useState<{ note: string; quote: string } | null>(null);
  const notesRef = useRef<HTMLDivElement>(null);
  const p = d.data;
  const quotes = (p?.data.resolutions || []).filter((r) => r.quote && r.note_id);

  const show = (note: string, quote: string) => {
    setFocus({ note, quote });
    setTimeout(() => notesRef.current?.querySelector(`#note-${note}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  };
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-head">
          {p && <>
            <div>
              <h2>{p.data.person.name} <span className="mono faint" style={{ fontSize: 13 }}>{personId}</span></h2>
              <div className="muted small">{p.data.person.age} · {p.data.person.sex} · {p.data.person.race} · {p.data.person.ethnicity} · {p.data.person.clinic} · PCP {p.data.person.pcp}</div>
            </div>
            <VerdictPill v={p.verdict} />
            {p.stage === "open" && <span className="pill blue">chart-reviewed</span>}
            {p.data.review_error && <span className="pill red" title={p.data.review_error}>review failed · check by hand</span>}
          </>}
          <span className="grow" />
          <div className="tabs" style={{ margin: 0, border: 0 }}>
            <button className={`tab ${view === "evidence" ? "on" : ""}`} onClick={() => setView("evidence")}>Evidence</button>
            <button className={`tab ${view === "trace" ? "on" : ""}`} onClick={() => setView("trace")}>Trace</button>
          </div>
          <button className="btn sm" onClick={() => setFb({ ...fb, open: !fb.open, msg: "" })}><MessageSquareWarning />Correct</button>
          <button className="btn ghost" onClick={onClose}><X /></button>
        </div>
        {p ? (
          <div className="drawer-body">
            <div>
              {fb.open && (
                <div className="crit feedback-form">
                  <div className="lbl" style={{ marginBottom: 8 }}>Correct the system · it learns from this</div>
                  <div className="row" style={{ flexWrap: "nowrap" }}>
                    <select value={fb.target} onChange={(e) => setFb({ ...fb, target: e.target.value, correct: "" })}>
                      <option value="">Verdict ({p.verdict.replace("_", " ")})</option>
                      {p.data.screen.criteria.map((c) => <option key={c.id} value={c.id}>{c.id} ({c.status.replace("_", " ")})</option>)}
                    </select>
                    <select value={fb.correct} onChange={(e) => setFb({ ...fb, correct: e.target.value })}>
                      <option value="">Correct value…</option>
                      {(fb.target ? ["met", "not_met", "unknown"] : ["eligible", "ineligible", "needs_review"]).map((v) =>
                        <option key={v} value={v}>{v.replace("_", " ")}</option>)}
                    </select>
                  </div>
                  <input style={{ width: "100%", marginTop: 8 }} placeholder="Why? e.g. IV furosemide in hospital is not a regular oral diuretic"
                    value={fb.reason} onChange={(e) => setFb({ ...fb, reason: e.target.value })} />
                  <div className="row" style={{ marginTop: 8 }}>
                    <button className="btn sm primary" disabled={!fb.correct} onClick={async () => {
                      try {
                        await api.feedback(runId, personId, { criterion_id: fb.target, correct: fb.correct, reason: fb.reason });
                        setFb({ open: false, target: "", correct: "", reason: "", msg: "Saved. Open Criteria → Learning to turn it into a lesson." });
                      } catch (e: any) { setFb({ ...fb, msg: e.message }); }
                    }}>Save feedback</button>
                    <span className="small muted">{fb.msg}</span>
                  </div>
                </div>
              )}
              {!fb.open && fb.msg && <div className="small" style={{ color: "var(--green)", marginBottom: 10 }}>{fb.msg}</div>}
              {view === "trace" ? (trace.data ? <TraceWaterfall calls={trace.data.calls} events={trace.data.events} /> : <Skeleton rows={8} />) : <>
              {p.data.screen.criteria.map((c) => <CriterionBlock key={c.id} c={c} onQuote={show} />)}
              {p.data.resolutions.length > 0 && (
                <>
                  <h3 style={{ margin: "18px 0 10px" }}>Chart agent answers</h3>
                  {p.data.resolutions.map((r) => <ResolutionRow key={r.question_id} r={r} onQuote={show} />)}
                </>
              )}
              </>}
            </div>
            <div ref={notesRef}>
              {p.notes.slice().reverse().map((n) => (
                <div key={n.note_id} id={`note-${n.note_id}`} className="note">
                  <div className="note-head"><FileText size={13} /><b>{n.type}</b><span>{n.date}</span><span className="mono">{n.note_id}</span><span className="grow" />{n.author}</div>
                  <pre>{highlight(n.text, quotes.filter((q) => q.note_id === n.note_id).map((q) => q.quote), focus?.note === n.note_id ? focus.quote : "")}</pre>
                </div>
              ))}
            </div>
          </div>
        ) : d.error ? <div className="empty">{d.error}</div> : <div style={{ padding: 24 }}><Skeleton rows={10} /></div>}
      </div>
    </>
  );
}

function CriterionBlock({ c, onQuote }: { c: CriterionResult; onQuote: (n: string, q: string) => void }) {
  const tone = c.passes ? "green" : c.fails ? "red" : "amber";
  return (
    <div className="crit">
      <div className="crit-head">
        <span className={`pill ${tone}`} style={{ minWidth: 34, justifyContent: "center" }}>{c.id}</span>
        <span className="txt">{c.text.replace(/\\/g, "")}</span>
      </div>
      {c.atoms.map((a) => <AtomRow key={a.key} a={a} kind={c.kind} onQuote={onQuote} />)}
    </div>
  );
}

function AtomRow({ a, kind, onQuote }: { a: AtomResult; kind: string; onQuote: (n: string, q: string) => void }) {
  const tone = statusTone(a.status, kind);
  return (
    <div className="atom">
      <div className="lbl">
        <span className={`dot ${tone === "green" ? "green" : tone === "red" ? "red" : "amber"}`} />
        <span>{a.label}</span>
        <span className="faint small">{a.status.replace("_", " ")}</span>
        {a.source === "notes" && <span className="pill teal">notes</span>}
      </div>
      {a.children.length > 0 ? (
        <div className="kids">{a.children.map((k) => <AtomRow key={k.key} a={k} kind={kind} onQuote={onQuote} />)}</div>
      ) : (
        a.evidence.slice(0, 4).map((e, i) => {
          const m = e.match(/^(P\d{4}-N\d+): "(.*)"/);
          if (m) return <div key={i} className="evd quote" onClick={() => onQuote(m[1], m[2])}>“{m[2]}” <span className="faint mono">{m[1]}</span></div>;
          return <div key={i} className={`evd ${e.startsWith("check:") ? "warn" : ""}`}>{e}</div>;
        })
      )}
    </div>
  );
}

function ResolutionRow({ r, onQuote }: { r: Resolution; onQuote: (n: string, q: string) => void }) {
  const ok = r.reviewer === "approved";
  return (
    <div className="crit">
      <div className="row" style={{ marginBottom: 4 }}>
        <span className="mono faint">{r.question_id}</span>
        <strong style={{ fontSize: 13 }}>{r.concept}</strong>
        <span className={`pill ${r.finding === "present" ? "blue" : r.finding === "absent" ? "outline" : "amber"}`}>{r.finding}</span>
        {r.value != null && <span className="chip">{r.value} {r.unit}</span>}
        {r.date && <span className="faint small">{r.date}</span>}
        <span className="grow" />
        {r.round > 1 && <span className="pill purple">retry</span>}
        {ok ? <span className="pill green"><ShieldCheck />reviewed</span> : r.reviewer.startsWith("rejected") ? <span className="pill red"><ShieldAlert />rejected</span> : <span className="pill outline">{r.reviewer || "–"}</span>}
      </div>
      {r.quote && <div className="atom" style={{ margin: 0 }}><div className="evd quote" style={{ marginLeft: 0 }} onClick={() => onQuote(r.note_id, r.quote)}>“{r.quote}” <span className="faint mono">{r.note_id}</span></div></div>}
      {r.problems.map((x, i) => <div key={i} className="small" style={{ color: "var(--amber-text)", marginTop: 3 }}>{x}</div>)}
      {r.reviewer.startsWith("rejected") && <div className="small" style={{ color: "var(--violet)", marginTop: 3 }}>{r.reviewer}</div>}
    </div>
  );
}

function highlight(text: string, quotes: string[], focus: string) {
  const spans: { start: number; end: number; focus: boolean }[] = [];
  const low = text.toLowerCase();
  for (const q of quotes) {
    for (const part of q.split(/\.\.\.|…/)) {
      const needle = part.trim().replace(/^["“]|["”]$/g, "").toLowerCase();
      if (needle.length < 4) continue;
      const at = low.indexOf(needle);
      if (at >= 0) spans.push({ start: at, end: at + needle.length, focus: q === focus });
    }
  }
  spans.sort((a, b) => a.start - b.start);
  const out: React.ReactNode[] = [];
  let pos = 0;
  spans.forEach((s, i) => {
    if (s.start < pos) return;
    out.push(text.slice(pos, s.start));
    out.push(<mark key={i} className={s.focus ? "focus" : ""}>{text.slice(s.start, s.end)}</mark>);
    pos = s.end;
  });
  out.push(text.slice(pos));
  return out;
}
