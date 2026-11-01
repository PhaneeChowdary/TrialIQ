import { useState } from "react";
import { Check, RefreshCw, Sparkles, X } from "lucide-react";
import { api, type LabCode } from "../api";
import { Kpi, Toast, useInterval, useLoad } from "../ui";
import { useConfirm } from "../Confirm";

export default function Terminology() {
  const codes = useLoad(api.labCodes);
  const runs = useLoad(() => api.runs());
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [toast, setToast] = useState("");
  const mapping = (runs.data || []).find((r) => r.kind === "mapping" && ["queued", "running"].includes(r.status));
  useInterval(() => { codes.reload(); runs.reload(); }, mapping ? 2500 : null);

  const all = codes.data || [];
  const unmapped = all.filter((c) => !c.loinc && c.row_count);
  const approved = unmapped.filter((c) => c.mapping?.status === "approved").length;
  const rows = all.filter((c) => !c.loinc).length ? [...unmapped, ...all.filter((c) => c.loinc)] : all;

  const confirm = useConfirm();
  const decide = async (c: LabCode, approve: boolean) => {
    const loinc = edit[c.source_code] ?? c.mapping?.loinc;
    if (approve && !(await confirm({
      title: "Approve LOINC mapping", tone: c.mapping?.confidence === "high" && loinc === c.mapping?.loinc ? "primary" : "warning",
      confirmLabel: "Approve mapping",
      message: <>The <b>{c.row_count}</b> results under <code>{c.source_code}</code> ({c.source_name}, {c.unit}) will count as LOINC <b>{loinc}</b> in new screening runs.</>,
      details: [loinc === c.mapping?.loinc ? `Agent confidence: ${c.mapping?.confidence ?? "–"}` : "You changed the agent's proposal",
        "The code checks the LOINC format and that the unit fits before it saves", "Running screens keep the mappings they started with"],
    }))) return;
    try {
      await api.decideMapping(c.source_code, approve, loinc || undefined);
      setToast(`${c.source_code} ${approve ? "approved" : "rejected"}`);
      codes.reload();
    } catch (e: any) { setToast(e.message); }
  };
  const run = async () => {
    if (!(await confirm({ title: "Propose mappings", confirmLabel: "Start the mapper",
      message: <>The mapper agent searches LOINC for {unmapped.filter((c) => c.mapping?.status !== "approved").length} local code(s). It only proposes: you approve each mapping.</> }))) return;
    await api.runMapper(); runs.reload();
  };

  return (
    <>
      <div className="page-head">
        <div><h1>Terminology</h1><div className="sub">Local lab codes → LOINC</div></div>
        <button className="btn primary" disabled={!!mapping} onClick={run}>
          {mapping ? <RefreshCw className="spin" /> : <Sparkles />}{mapping ? "Mapping" : "Propose mappings"}
        </button>
      </div>
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Kpi v={all.length} l="Lab codes" />
        <Kpi v={unmapped.length} l="Without LOINC" tone={unmapped.length ? "amber" : ""} d={`${unmapped.reduce((n, c) => n + c.row_count, 0)} results`} />
        <Kpi v={unmapped.filter((c) => c.mapping?.status === "proposed").length} l="Proposed" tone="teal" />
        <Kpi v={`${approved}/${unmapped.length}`} l="Approved" tone={approved === unmapped.length ? "green" : ""} />
      </div>
      <div className="panel flush">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Local code</th><th>Name</th><th>Unit</th><th>Clinic</th><th className="num">Results</th><th>LOINC</th><th>Agent proposal</th><th /></tr></thead>
            <tbody>
              {rows.map((c) => {
                const m = c.mapping;
                const open = !c.loinc;
                return (
                  <tr key={c.source_code}>
                    <td className="mono"><strong>{c.source_code}</strong></td>
                    <td>{c.source_name}</td>
                    <td className="mono">{c.unit}</td>
                    <td className="small">{c.clinic_name}</td>
                    <td className="num">{c.row_count}</td>
                    <td>
                      {c.loinc ? <span className="chip">{c.loinc}</span> : m?.status === "approved" ? <span className="pill green mono">{m.loinc}</span> :
                        <input className="mono" style={{ width: 110, padding: "4px 8px" }} placeholder="LOINC" value={edit[c.source_code] ?? m?.loinc ?? ""}
                          onChange={(e) => setEdit({ ...edit, [c.source_code]: e.target.value })} />}
                    </td>
                    <td className="small" style={{ maxWidth: 380 }}>
                      {m ? (
                        <>
                          <span className={`pill ${m.confidence === "high" ? "green" : m.confidence === "medium" ? "amber" : "red"}`}>{m.confidence}</span>{" "}
                          <span className={`pill ${m.status === "approved" ? "green" : m.status === "rejected" ? "red" : "outline"}`}>{m.status}</span>
                          <div className="muted" style={{ marginTop: 4 }}>{m.reason}</div>
                        </>
                      ) : open ? <span className="faint">–</span> : null}
                    </td>
                    <td>
                      {open && m?.status !== "approved" && (
                        <div className="row" style={{ flexWrap: "nowrap" }}>
                          <button className="btn sm good" disabled={!(edit[c.source_code] ?? m?.loinc)} onClick={() => decide(c, true)}><Check /></button>
                          {m && <button className="btn sm bad" onClick={() => decide(c, false)}><X /></button>}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      {toast && <Toast text={toast} onDone={() => setToast("")} />}
    </>
  );
}
