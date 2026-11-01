import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Check, ExternalLink, KeyRound, Lock, Plus, RefreshCw, Trash2, Unlock, X } from "lucide-react";
import { Kpi, Toast, secs, useLoad } from "../ui";
import { ProviderLogo } from "../logos";
import { useConfirm } from "../Confirm";

type Provider = {
  id: string; name: string; maker: string; keys_url: string; connected: boolean; key_hint: string; key_source: string;
  models: string[]; model: string; paused: boolean; usage: { calls: number; tokens: number };
};
type Level = { level: string; about: string; phi: boolean; route: string[]; chain: { provider: string; model: string; skipped: string }[] };
type Overview = {
  providers: Provider[]; levels: Level[]; cloud_phi: boolean; custom_routes: boolean;
  local: { model: string; usage: { calls: number; tokens: number } }; usage: any;
};

async function call(path: string, method = "GET", body?: unknown) {
  const res = await fetch(`/api/models${path}`, {
    method, headers: { "Content-Type": "application/json", "X-User": localStorage.getItem("trialiq.user") || "coordinator" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

const LEVEL_NAME: Record<string, string> = { protocol: "Protocol", chart: "Chart", draft: "Drafts" };

export default function Models() {
  const m = useLoad<Overview>(() => call(""));
  const [toast, setToast] = useState("");
  const d = m.data;
  const set = (p: Promise<Overview>, done?: string) =>
    p.then((o) => { m.setData(o); if (done) setToast(done); }).catch((e) => setToast(e.message));
  const usage = d?.usage;
  const confirm = useConfirm();
  const togglePhi = async () => {
    if (!d) return;
    if (!d.cloud_phi && !(await confirm({
      title: "Allow cloud models to read patient notes", tone: "danger", confirmLabel: "Allow cloud models", typeToConfirm: "SYNTHETIC",
      message: <>Chart review, the reviewer, reflection and referral letters will send <b>patient notes</b> to the cloud providers in their chains.</>,
      details: ["Use this only with synthetic data", "Real patient data needs a business associate agreement (BAA) and your privacy office's approval",
        "You can switch it off at any time; the audit log records the change"],
    }))) return;
    set(call("/privacy", "PUT", { cloud_phi: !d.cloud_phi }), d.cloud_phi ? "Patient notes stay on the local model" : "Cloud models may read patient notes");
  };
  return (
    <>
      <div className="page-head">
        <div><h1>Models</h1><div className="sub">Cloud keys · chains by task · local backup</div></div>
        {d && (
          <button className={`btn ${d.cloud_phi ? "bad" : "good"}`} onClick={togglePhi}>
            {d.cloud_phi ? <Unlock /> : <Lock />}{d.cloud_phi ? "Cloud may read notes" : "Notes stay local"}
          </button>
        )}
      </div>
      <div className="grid g4" style={{ marginBottom: 14 }}>
        <Kpi v={d ? d.providers.filter((p) => p.connected).length : "–"} l="Cloud providers" d={d ? `of ${d.providers.length}` : ""} />
        <Kpi v={usage?.calls ?? "–"} l="Model calls" d={usage ? `${(usage.tokens / 1000).toFixed(0)}k tokens` : ""} />
        <Kpi v={usage?.median_ms != null ? secs(usage.median_ms) : "–"} l="Median call" />
        <Kpi v={usage?.failed ?? "–"} l="Failed calls" tone={usage?.failed ? "amber" : ""} />
      </div>

      <div className="grid g3" style={{ marginBottom: 14 }}>
        {d?.providers.map((p) => <ProviderCard key={p.id} p={p} onChange={set} />)}
        {d && (
          <div className="panel">
            <div className="provider-head">
              <span className="logo-tile"><ProviderLogo id="ollama" /></span>
              <div className="grow"><div className="t">Local model</div><div className="m">Ollama</div></div>
              <span className="pill green">Always on</span>
            </div>
            <div className="mono" style={{ fontSize: 13, marginBottom: 8 }}>{d.local.model}</div>
            <div className="legend">{d.local.usage.calls} calls · {(d.local.usage.tokens / 1000).toFixed(0)}k tokens · last link of every chain</div>
          </div>
        )}
      </div>

      {d && <Chains d={d} onChange={set} />}

      <div className="panel flush" style={{ marginTop: 14 }}>
        <div className="panel-head"><h3>By agent</h3></div>
        <table>
          <thead><tr><th>Agent</th><th className="num">Calls</th><th className="num">Tokens in</th><th className="num">Tokens out</th><th className="num">Avg time</th><th className="num">Failed</th></tr></thead>
          <tbody>
            {Object.entries(usage?.per_agent || {}).map(([a, v]: [string, any]) => (
              <tr key={a}><td><strong>{a}</strong></td><td className="num">{v.calls}</td><td className="num">{v.tokens_in}</td><td className="num">{v.tokens_out}</td>
                <td className="num">{secs(v.ms / Math.max(1, v.calls))}</td><td className="num">{v.failed || "–"}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      {toast && <Toast text={toast} onDone={() => setToast("")} />}
    </>
  );
}

function ProviderCard({ p, onChange }: { p: Provider; onChange: (x: Promise<Overview>, done?: string) => void }) {
  const confirm = useConfirm();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState(p.model);
  const [check, setCheck] = useState<any>(null);
  useEffect(() => setModel(p.model), [p.model]);
  const save = async () => {
    setBusy(true);
    await onChange(call(`/${p.id}/key`, "PUT", { api_key: key }), `${p.name} key saved`);
    setKey("");
    setBusy(false);
  };
  const test = async () => {
    setBusy(true);
    setCheck(null);
    try { setCheck(await call(`/${p.id}/check`, "POST", { model })); } catch (e: any) { setCheck({ error: e.message }); }
    setBusy(false);
  };
  const choose = (value: string) => { setModel(value); onChange(call(`/${p.id}/model`, "PUT", { model: value })); };
  return (
    <div className="panel">
      <div className="provider-head">
        <span className="logo-tile"><ProviderLogo id={p.id} /></span>
        <div className="grow"><div className="t">{p.name}</div><div className="m">{p.maker}</div></div>
        {p.connected ? <span className={`pill ${p.paused ? "amber" : "green"}`}>{p.paused ? "Rate limited" : `Key ${p.key_hint}`}</span>
          : <span className="pill outline">No key</span>}
      </div>
      {p.connected ? (
        <>
          <div className="row" style={{ flexWrap: "nowrap", marginBottom: 10 }}>
            {p.models.length ? (
              <select value={model} onChange={(e) => choose(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
                {!p.models.includes(model) && <option value={model}>{model}</option>}
                {p.models.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            ) : <input className="mono" value={model} onChange={(e) => setModel(e.target.value)} onBlur={() => choose(model)} style={{ flex: 1 }} />}
            <button className="btn sm" disabled={busy} onClick={test}>{busy ? <RefreshCw className="spin" /> : <Check />}Test</button>
          </div>
          {check && (
            <div className="checks">
              {check.error ? <div className="err">{check.error}</div> : check.checks.map((c: any) => (
                <div key={c.name} className="row small" style={{ flexWrap: "nowrap" }}>
                  <span className={`dot ${c.ok ? "green" : "red"}`} /><span style={{ minWidth: 110 }}>{c.name}</span>
                  <span className="faint mono ellipsis" style={{ flex: 1 }} title={c.detail}>{c.detail}</span>
                  <span className="faint mono">{c.ms} ms</span>
                </div>
              ))}
            </div>
          )}
          <div className="row legend" style={{ marginTop: 10 }}>
            <span>{p.usage.calls} calls · {(p.usage.tokens / 1000).toFixed(0)}k tokens</span>
            <span className="grow" />
            {p.key_source === "page" ? (
              <button className="btn ghost sm" onClick={async () => {
                if (await confirm({ title: `Remove the ${p.name} key`, tone: "danger", confirmLabel: "Remove key",
                  message: <>The {p.maker} key ending {p.key_hint} is deleted from this computer. Chains skip {p.name} until you add a key again.</>,
                  details: ["Runs in progress fall back to the next model of their chain"] }))
                  onChange(call(`/${p.id}/key`, "DELETE"), `${p.name} key removed`);
              }}><Trash2 />Remove</button>
            ) : <span>from .env</span>}
          </div>
        </>
      ) : (
        <>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input type="password" placeholder="API key" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off"
              onKeyDown={(e) => e.key === "Enter" && key.length >= 8 && save()} style={{ flex: 1, minWidth: 0 }} />
            <button className="btn sm primary" disabled={busy || key.length < 8} onClick={save}>{busy ? <RefreshCw className="spin" /> : <KeyRound />}Save</button>
          </div>
          <div className="row legend" style={{ marginTop: 10 }}>
            <span>Tested before saving · stored encrypted</span><span className="grow" />
            <a className="row" style={{ gap: 4 }} href={p.keys_url} target="_blank" rel="noreferrer">Get a key<ExternalLink size={11} /></a>
          </div>
        </>
      )}
    </div>
  );
}

function Chains({ d, onChange }: { d: Overview; onChange: (x: Promise<Overview>, done?: string) => void }) {
  const confirm = useConfirm();
  const keyed = d.providers.filter((p) => p.connected).map((p) => p.id);
  const name = (id: string) => d.providers.find((p) => p.id === id)?.name || id;
  const [routes, setRoutes] = useState<Record<string, string[]>>({});
  useEffect(() => setRoutes(Object.fromEntries(d.levels.map((l) => [l.level, l.route]))), [d]);
  const dirty = d.levels.some((l) => JSON.stringify(l.route) !== JSON.stringify(routes[l.level]));
  const move = (level: string, i: number, by: number) => setRoutes((r) => {
    const c = [...r[level]];
    [c[i], c[i + by]] = [c[i + by], c[i]];
    return { ...r, [level]: c };
  });
  const toggle = (level: string, id: string) => setRoutes((r) => ({
    ...r, [level]: r[level].includes(id) ? r[level].filter((x) => x !== id) : [...r[level], id],
  }));
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Chains by task</h2>
        <button className="btn sm primary" disabled={!dirty} onClick={async () => {
          const phiCloud = d.cloud_phi && ["chart", "draft"].some((lv) => (routes[lv] || []).length > 0);
          if (phiCloud && !(await confirm({ title: "Save chains", tone: "warning", confirmLabel: "Save chains",
            message: "Cloud models may read notes, and the chart or draft chain has cloud providers: patient notes will go to them.",
            details: ["Use this only with synthetic data"] }))) return;
          onChange(call("/routes", "PUT", { routes }), "Chains saved");
        }}>Save chains</button>
      </div>
      <div className="grid g3">
        {d.levels.map((l) => {
          const route = routes[l.level] || [];
          const live = new Map(l.chain.map((c) => [c.provider, c]));
          return (
            <div key={l.level} className="chain">
              <div className="row" style={{ marginBottom: 4 }}>
                <strong style={{ fontWeight: 500 }}>{LEVEL_NAME[l.level]}</strong>
                <span className="grow" />
                {l.phi ? (d.cloud_phi ? <span className="pill amber"><Unlock />PHI to cloud</span> : <span className="pill green"><Lock />PHI local</span>)
                  : <span className="pill outline">No PHI</span>}
              </div>
              <div className="legend" style={{ marginBottom: 10 }}>{l.about}</div>
              {route.map((id, i) => {
                const c = live.get(id);
                return (
                  <div key={id} className={`link ${c?.skipped ? "off" : ""}`}>
                    <span className="mono faint">{i + 1}</span>
                    <ProviderLogo id={id} size="sm" />
                    <span className="grow ellipsis"><b>{name(id)}</b> <span className="mono faint">{c?.model}</span></span>
                    {c?.skipped && <span className="legend">{c.skipped}</span>}
                    <button className="btn ghost sm" disabled={i === 0} onClick={() => move(l.level, i, -1)} aria-label="Up"><ArrowUp /></button>
                    <button className="btn ghost sm" disabled={i === route.length - 1} onClick={() => move(l.level, i, 1)} aria-label="Down"><ArrowDown /></button>
                    <button className="btn ghost sm" onClick={() => toggle(l.level, id)} aria-label="Remove"><X /></button>
                  </div>
                );
              })}
              <div className="link local"><span className="mono faint">{route.length + 1}</span><ProviderLogo id="ollama" size="sm" /><span className="grow"><b>Local</b> <span className="mono faint">{d.local.model}</span></span><span className="legend">backup</span></div>
              <div className="row" style={{ marginTop: 8, gap: 6 }}>
                {keyed.filter((id) => !route.includes(id)).map((id) => (
                  <button key={id} className="btn sm" onClick={() => toggle(l.level, id)}><Plus /><ProviderLogo id={id} size="sm" />{name(id)}</button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
