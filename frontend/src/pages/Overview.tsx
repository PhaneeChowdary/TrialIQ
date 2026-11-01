import { useState } from "react";
import { api, type Run, type RunEvent, type Trial } from "../api";
import { go } from "../App";
import PipelineFigure, { type FigureStage } from "../Figure";
import { Kpi, RunPill, ago, useInterval, useLoad } from "../ui";
import { Timeline, type TimelineItem } from "../charts";

const STAGE_OBJECT: Record<string, FigureStage> = {
  prescreen: "prescreen", chart_review: "chart", adjudicate: "reviewer", outreach: "coordinator", approval: "coordinator",
};

export default function Overview() {
  const o = useLoad(api.overview);
  const codes = useLoad(api.labCodes);
  const t = useLoad(api.trials);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const d = o.data;
  const runs: Run[] = d?.recent_runs || [];
  const live = runs.find((r) => r.status === "running") || null;
  const lastScreen = runs.find((r) => r.kind === "screen") || null;

  const refresh = () => {
    o.reload();
    t.reload();
    const run = live || lastScreen;
    if (run) api.events(run.id, 0).then((e) => setEvents(e.slice(-8))).catch(() => {});
  };
  useInterval(refresh, live ? 2500 : 8000);

  const screen = live
    ? events.filter((e) => e.kind !== "tool" || e.patient).slice(-4).map((e) => `> ${e.patient ? e.patient + " " : ""}${e.text}`.toLowerCase())
    : [];
  const f = lastScreen?.summary?.funnel;
  const v = lastScreen?.summary?.verdicts;
  const captions: Record<FigureStage, string> = {
    ehr: `${d?.patients ?? "–"} patients · ${d?.notes ?? "–"} notes · ${d ? `${d.approved_mappings}/${d.unmapped_codes}` : "–"} local codes mapped`,
    prescreen: f ? `${f.pool} screened · ${f.excluded} excluded by rules · ${f.open} to chart review` : "rules · no model",
    chart: live ? `chart review · ${live.summary?.reviewed ?? 0}/${live.summary?.funnel?.open ?? "–"} patients` : "notes + tools · local model",
    reviewer: "quotes · units · dates · negation · family history",
    coordinator: v ? `${v.eligible} eligible · ${v.needs_review} need review · ${d?.awaiting_approval ?? 0} awaiting approval` : "approves every referral",
  };
  const pick = (s: FigureStage) => {
    if (s === "ehr") return go({ page: "terminology" });
    if (s === "reviewer") return go({ page: "evals" });
    const run = live || lastScreen;
    if (!run) return go({ page: "trials" });
    const tab = s === "coordinator" ? "referrals" : s === "chart" ? "screening" : "candidates";
    go({ page: "trial", id: run.nct_id, tab, run: run.id });
  };
  const tasks: TimelineItem[] = [
    ...runs.filter((r) => r.status === "awaiting_approval").map((r) => ({
      key: r.id, time: clockDay(r.updated_at), meta: r.nct_id,
      title: `Approve ${r.summary?.verdicts?.eligible ?? ""} referral drafts`, tone: "amber" as const,
      pill: <span className="pill amber">Review</span>, onClick: () => go({ page: "trial", id: r.nct_id, tab: "referrals", run: r.id }),
    })),
    ...(codes.data || []).filter((c) => !c.loinc && c.row_count && c.mapping?.status !== "approved").map((c) => ({
      key: c.source_code, time: c.mapping ? `${c.mapping.confidence} confidence` : "no proposal yet",
      meta: c.clinic_name, title: `LOINC for ${c.source_code} · ${c.source_name}`, tone: "violet" as const,
      pill: <span className="pill purple">Map</span>, onClick: () => go({ page: "terminology" }),
    })),
    ...runs.filter((r) => r.status === "failed").slice(0, 2).map((r) => ({
      key: `f${r.id}`, time: clockDay(r.updated_at), meta: r.id, title: `Run failed: ${r.error.slice(0, 80)}`, tone: "red" as const,
      pill: <span className="pill red">Failed</span>,
    })),
  ];
  const status = live ? `on · ${live.id} · ${live.stage.replace("_", " ")}` : "off";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <div className="sub">Synthetic EHR · 3 Indiana clinics · real ClinicalTrials.gov protocols</div>
        </div>
      </div>

      <div style={{ marginBottom: 14 }}>
        <PipelineFigure live={!!live} screen={screen} captions={captions} active={live ? STAGE_OBJECT[live.stage] : null}
          onPick={pick} status={status} />
      </div>

      <div className="grid g5" style={{ marginBottom: 14 }}>
        <Kpi v={d?.patients ?? "–"} l="Patients" d={`${d?.notes ?? "–"} notes`} />
        <Kpi v={d?.trials ?? "–"} l="Trials" />
        <Kpi v={d?.awaiting_approval ?? "–"} l="Awaiting approval" tone={d?.awaiting_approval ? "amber" : ""} />
        <Kpi v={d ? `${d.approved_mappings}/${d.unmapped_codes}` : "–"} l="Local codes mapped" tone={d && d.approved_mappings < d.unmapped_codes ? "amber" : "green"} />
        <Kpi v={d?.running ?? "–"} l="Runs in progress" />
      </div>

      <div className="grid g3" style={{ marginBottom: 14 }}>
        {(t.data || []).map((trial: Trial) => <TrialCard key={trial.nct_id} trial={trial} />)}
      </div>

      <div className="grid g2" style={{ alignItems: "start" }}>
        <div className="panel flush">
          <div className="panel-head"><h2>Waiting for you</h2><span className="legend">{tasks.length} open</span></div>
          <Timeline items={tasks} empty="Nothing to approve" />
        </div>
        <div className="panel flush">
          <div className="panel-head"><h2>Recent runs</h2></div>
          <Timeline empty="No runs yet" items={runs.slice(0, 6).map((r) => ({
            key: r.id,
            time: clockDay(r.updated_at),
            meta: `${r.kind} · ${r.id}`,
            title: r.nct_id ? `${r.nct_id} · ${r.kind === "screen" ? (r.summary?.verdicts ? `${r.summary.verdicts.eligible} eligible, ${r.summary.verdicts.needs_review} to review` : r.stage.replace("_", " ")) : r.kind}` : "LOINC mapping proposals",
            tone: RULE[r.status] || "grey",
            pill: <RunPill run={r} />,
            onClick: r.nct_id ? () => go({ page: "trial", id: r.nct_id, tab: r.kind === "criteria" ? "criteria" : "screening", run: r.id }) : () => go({ page: "terminology" }),
          }))} />
        </div>
      </div>
    </>
  );
}

const RULE: Record<string, TimelineItem["tone"]> = {
  completed: "green", awaiting_approval: "amber", running: "blue", failed: "red", queued: "grey",
};

function clockDay(iso: string) {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return `${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })} · ${today ? "Today" : d.toLocaleDateString([], { month: "short", day: "numeric" })}`;
}

function TrialCard({ trial }: { trial: Trial }) {
  const s = trial.last_screen;
  const v = s?.summary?.verdicts;
  const f = s?.summary?.funnel;
  return (
    <div className="panel clickable" onClick={() => go({ page: "trial", id: trial.nct_id })}>
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="pill teal">{trial.nct_id}</span>
        {trial.phases?.map((p) => <span key={p} className="pill outline">{p.replace("PHASE", "Phase ")}</span>)}
        <span className="grow" />
        {s && <RunPill run={s} />}
      </div>
      <h3 style={{ lineHeight: 1.4, marginBottom: 8, fontWeight: 500 }}>{trial.title}</h3>
      <div className="faint small" style={{ marginBottom: 14 }}>{trial.sponsor} · {trial.conditions?.slice(0, 2).join(", ")}</div>
      {v && f ? (
        <>
          <div className="bar-mini" style={{ marginBottom: 10 }}>
            <span style={{ width: `${(v.eligible / f.pool) * 100}%`, background: "var(--green)" }} />
            <span style={{ width: `${(v.needs_review / f.pool) * 100}%`, background: "var(--amber)" }} />
          </div>
          <div className="row legend" style={{ gap: 14 }}>
            <span><b style={{ color: "var(--green)", fontWeight: 400 }}>{v.eligible}</b> eligible</span>
            <span><b style={{ color: "var(--amber)", fontWeight: 400 }}>{v.needs_review}</b> review</span>
            <span><b style={{ color: "var(--text-2)", fontWeight: 400 }}>{f.open}</b> charted</span>
            <span className="grow" />
            <span>{ago(s?.updated_at)}</span>
          </div>
        </>
      ) : (
        <div className="legend">{trial.criteria ? "Criteria approved · not screened" : "Criteria not approved"}</div>
      )}
    </div>
  );
}
