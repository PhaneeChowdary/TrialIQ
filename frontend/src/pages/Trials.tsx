import { useState } from "react";
import { Plus, Search } from "lucide-react";
import { api } from "../api";
import { go } from "../App";
import { RunPill, Toast, ago, useLoad } from "../ui";

export default function Trials() {
  const trials = useLoad(api.trials);
  const [nct, setNct] = useState("");
  const [query, setQuery] = useState("heart failure");
  const [found, setFound] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");

  const add = async (id: string) => {
    setBusy(true);
    try {
      await api.addTrial(id);
      setToast(`${id} imported`);
      trials.reload();
    } catch (e: any) {
      setToast(e.message);
    } finally {
      setBusy(false);
    }
  };
  const search = async () => {
    setBusy(true);
    try { setFound(await api.searchCtgov(query)); } catch (e: any) { setToast(e.message); } finally { setBusy(false); }
  };
  const known = new Set((trials.data || []).map((t) => t.nct_id));

  return (
    <>
      <div className="page-head">
        <div><h1>Trials</h1><div className="sub">{trials.data?.length ?? "–"} in workspace</div></div>
        <div className="search">
          <input placeholder="NCT ID" value={nct} onChange={(e) => setNct(e.target.value.toUpperCase())} style={{ width: 160 }} />
          <button className="btn primary" disabled={busy || !/^NCT\d{8}$/.test(nct)} onClick={() => add(nct)}><Plus />Import</button>
        </div>
      </div>

      <div className="panel flush" style={{ marginBottom: 14 }}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Trial</th><th>Phase</th><th>Sponsor</th><th>Ages</th><th>Criteria</th><th>Last screen</th><th className="num">Eligible</th><th className="num">Review</th></tr></thead>
            <tbody>
              {(trials.data || []).map((t) => {
                const v = t.last_screen?.summary?.verdicts;
                return (
                  <tr key={t.nct_id} className="hover" onClick={() => go({ page: "trial", id: t.nct_id })}>
                    <td style={{ minWidth: 280 }}><div className="mono" style={{ color: "var(--teal)" }}>{t.nct_id}</div><strong>{t.title}</strong></td>
                    <td>{t.phases?.join(", ").replace(/PHASE/g, "P") || "–"}</td>
                    <td>{t.sponsor}</td>
                    <td>{t.min_age || "–"} – {t.max_age || "any"}</td>
                    <td>{t.criteria ? <span className="pill green">{t.criteria}</span> : <span className="pill amber">Not approved</span>}</td>
                    <td>{t.last_screen ? <span className="row"><RunPill run={t.last_screen} /><span className="faint small">{ago(t.last_screen.updated_at)}</span></span> : "–"}</td>
                    <td className="num"><strong>{v?.eligible ?? "–"}</strong></td>
                    <td className="num">{v?.needs_review ?? "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2><Search />ClinicalTrials.gov · recruiting in Indiana</h2>
          <div className="search">
            <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} style={{ width: 260 }} />
            <button className="btn" disabled={busy} onClick={search}>Search</button>
          </div>
        </div>
        {found && (
          <div className="table-wrap"><table>
            <thead><tr><th>NCT ID</th><th>Title</th><th>Phase</th><th>Sponsor</th><th className="num">Criteria chars</th><th /></tr></thead>
            <tbody>
              {found.map((f) => (
                <tr key={f.nct_id}>
                  <td className="mono">{f.nct_id}</td><td>{f.title}</td><td>{f.phases?.join(", ")}</td><td>{f.sponsor}</td>
                  <td className="num">{f.criteria_chars}</td>
                  <td>{known.has(f.nct_id) ? <span className="pill green">Added</span> :
                    <button className="btn sm" disabled={busy} onClick={() => add(f.nct_id)}><Plus />Add</button>}</td>
                </tr>
              ))}
              {!found.length && <tr><td colSpan={6} className="empty">No recruiting trials</td></tr>}
            </tbody>
          </table></div>
        )}
      </div>
      {toast && <Toast text={toast} onDone={() => setToast("")} />}
    </>
  );
}
