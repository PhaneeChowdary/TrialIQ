"""MCP server for TrialIQ: trial screening tools for any MCP client. The tools call the TrialIQ API, so a run started here shows in the UI and joins the same queue. Claude Desktop, Claude Code, an IDE or another agent.

There is no tool that approves a referral or a LOINC mapping: those stay with a person in the UI. Patient tools return
the minimum necessary: the verdict, the criteria and the cited evidence, not the whole chart.

    .venv/bin/python -m backend.app.mcp_server        # stdio; the MCP client starts it"""

import functools
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

INSTRUCTIONS = """TrialIQ screens a health system's patients for clinical trials. List the trials, search
ClinicalTrials.gov, start a screening run, then read its candidates and the evidence for each patient. A study
coordinator approves referrals and LOINC mappings in the TrialIQ app."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
QUEUES_WORK = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


def explained(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.json().get("detail", exc.response.text) if exc.response.content else ""
            raise ToolError(f"TrialIQ refused the request ({exc.response.status_code}): {detail}") from exc
        except httpx.HTTPError as exc:
            raise ToolError(f"TrialIQ is not reachable: {exc}. Start the API first.") from exc
    return wrapper


def create_server(http: httpx.Client | None = None) -> MCPServer:
    http = http or httpx.Client(base_url=os.getenv("TRIALIQ_API_URL", "http://127.0.0.1:8020"), timeout=30,
                                headers={"X-User": "mcp-client"})
    server = MCPServer("trialiq", instructions=INSTRUCTIONS, version="1.0.0")

    def get(path: str, **params):
        r = http.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    def post(path: str, body=None):
        r = http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    @server.tool(annotations=READ_ONLY)
    @explained
    def list_trials() -> list[dict[str, Any]]:
        """Trials in the workspace, with the counts of their last screening run."""
        return [{"nct_id": t["nct_id"], "title": t["title"], "criteria": t["criteria"],
                 "last_run": (t.get("last_screen") or {}).get("id"),
                 "last_verdicts": ((t.get("last_screen") or {}).get("summary") or {}).get("verdicts")} for t in get("/api/trials")]

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
    @explained
    def search_clinicaltrials(condition: str) -> list[dict[str, Any]]:
        """Recruiting trials in Indiana on ClinicalTrials.gov for a condition, e.g. 'heart failure'."""
        return get("/api/ctgov/search", condition=condition)

    @server.tool(annotations=QUEUES_WORK)
    @explained
    def start_screening(nct_id: str) -> dict[str, Any]:
        """Start a screening run of all patients for a trial with approved criteria. Returns the run id."""
        return post(f"/api/trials/{nct_id.strip().upper()}/screen")

    @server.tool(annotations=READ_ONLY)
    @explained
    def get_run(run_id: str) -> dict[str, Any]:
        """Status, stage, funnel, verdict counts and model usage of a run."""
        r = get(f"/api/runs/{run_id.strip()}")
        s = r.get("summary", {})
        return {"id": r["id"], "kind": r["kind"], "trial": r["nct_id"], "status": r["status"], "stage": r["stage"],
                "funnel": s.get("funnel"), "verdicts": s.get("verdicts"), "reviewed": s.get("reviewed"),
                "model_calls": r["usage"]["calls"], "tokens": r["usage"]["tokens"], "error": r.get("error")}

    @server.tool(annotations=READ_ONLY)
    @explained
    def list_candidates(run_id: str, verdict: str = "eligible") -> list[dict[str, Any]]:
        """Patients of a run with a verdict: eligible, needs_review or ineligible."""
        return [{"person_id": r["person_id"], "verdict": r["verdict"], "reasons": r["reasons"], "clinic": r["person"]["clinic"],
                 "facts_from_notes": r["notes_used"], "flags": r["flags"]}
                for r in get(f"/api/runs/{run_id.strip()}/results", verdict=verdict)]

    @server.tool(annotations=READ_ONLY)
    @explained
    def explain_patient(run_id: str, person_id: str) -> dict[str, Any]:
        """The verdict of one patient with each criterion's status and its evidence (structured data or a quoted
        note line). Does not return the whole chart."""
        d = get(f"/api/runs/{run_id.strip()}/patients/{person_id.strip().upper()}")

        def evidence(atoms):
            out = []
            for a in atoms:
                out += evidence(a["children"]) if a["children"] else [f"{a['label']}: {a['status']} ({'; '.join(a['evidence'][:2])})"]
            return out
        return {"person_id": d["person_id"], "verdict": d["verdict"],
                "criteria": [{"id": c["id"], "kind": c["kind"], "status": c["status"], "evidence": evidence(c["atoms"])}
                             for c in d["data"]["screen"]["criteria"]]}

    return server


if __name__ == "__main__":
    create_server().run()
