import { useEffect, useState } from "react";
import { Activity, BarChart3, BookOpenCheck, Cpu, FlaskConical, LayoutDashboard, Menu, Moon, ScrollText, ShieldCheck, Sun, X } from "lucide-react";
import { api } from "./api";
import { useInterval, useTheme } from "./ui";
import Overview from "./pages/Overview";
import Trials from "./pages/Trials";
import TrialPage from "./pages/TrialPage";
import Terminology from "./pages/Terminology";
import Evals from "./pages/Evals";
import Models from "./pages/Models";
import Audit from "./pages/Audit";
import Security from "./pages/Security";

export type Route = { page: string; id?: string; tab?: string; run?: string };

function parse(): Route {
  const [page = "overview", id, tab, run] = window.location.hash.replace(/^#\/?/, "").split("/");
  return { page, id, tab, run };
}

export function go(r: Route) {
  window.location.hash = "/" + [r.page, r.id, r.tab, r.run].filter(Boolean).join("/");
}

const NAV = [
  { page: "overview", label: "Overview", icon: LayoutDashboard },
  { page: "trials", label: "Trials", icon: FlaskConical },
  { page: "terminology", label: "Terminology", icon: BookOpenCheck },
  { page: "evals", label: "Evaluation", icon: BarChart3 },
  { page: "models", label: "Models", icon: Cpu },
  { page: "security", label: "Security", icon: ShieldCheck },
  { page: "audit", label: "Audit log", icon: ScrollText },
];

export default function App() {
  const [route, setRoute] = useState<Route>(parse());
  const [health, setHealth] = useState<any>(null);
  const [counts, setCounts] = useState<{ approvals: number; mappings: number }>({ approvals: 0, mappings: 0 });
  const [menu, setMenu] = useState(false);
  const [theme, toggleTheme] = useTheme();

  useEffect(() => {
    const on = () => { setRoute(parse()); setMenu(false); window.scrollTo(0, 0); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const refresh = () => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.overview().then((o) => setCounts({ approvals: o.awaiting_approval, mappings: Math.max(0, o.unmapped_codes - o.approved_mappings) })).catch(() => {});
  };
  useEffect(refresh, []);
  useInterval(refresh, 8000);

  const page = route.page === "trial" ? "trials" : route.page;
  return (
    <div className="shell">
      <aside className={`side ${menu ? "open" : ""}`}>
        <div className="brand">
          <img src="/favicon.svg" width={32} height={32} alt="" />
          <div>TrialIQ<small>Trial screening</small></div>
        </div>
        <button className="btn ghost menu-btn" aria-label="Menu" onClick={() => setMenu(!menu)}>{menu ? <X /> : <Menu />}</button>
        {NAV.map((n) => (
          <button key={n.page} className={`nav ${page === n.page ? "on" : ""}`} onClick={() => { setMenu(false); go({ page: n.page }); }}>
            <n.icon />
            {n.label}
            {n.page === "trials" && counts.approvals > 0 && <span className="count">{counts.approvals}</span>}
            {n.page === "terminology" && counts.mappings > 0 && <span className="count">{counts.mappings}</span>}
          </button>
        ))}
        <div className="side-foot">
          <span className="row"><span className={`dot ${health?.ollama?.ok ? "green" : "red"}`} />Ollama {health?.ollama?.ok ? "online" : "offline"}</span>
          <span className="row"><Activity size={12} />PHI {health?.cloud_phi ? "cloud allowed" : "local only"}</span>
          <span className="row">Screening date {health?.screen_date || "–"}</span>
          <button className="btn sm theme-btn" onClick={toggleTheme}>{theme === "dark" ? <Sun /> : <Moon />}{theme === "dark" ? "Light" : "Dark"} theme</button>
        </div>
      </aside>
      <main className="main">
        {route.page === "overview" && <Overview />}
        {route.page === "trials" && <Trials />}
        {route.page === "trial" && route.id && <TrialPage id={route.id} tab={route.tab || "screening"} runId={route.run} />}
        {route.page === "terminology" && <Terminology />}
        {route.page === "evals" && <Evals />}
        {route.page === "models" && <Models />}
        {route.page === "security" && <Security />}
        {route.page === "audit" && <Audit />}
      </main>
    </div>
  );
}
