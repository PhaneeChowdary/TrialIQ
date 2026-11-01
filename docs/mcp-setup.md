# MCP setup

TrialIQ has an MCP server, so Claude Desktop, Claude Code or another agent can use the screening tools.
The tools call the TrialIQ API: start the API first (`./run.sh`).

| Tool | Does |
|---|---|
| `list_trials` | Trials and the counts of their last run |
| `search_clinicaltrials` | Recruiting Indiana trials for a condition (ClinicalTrials.gov) |
| `start_screening` | Starts a screening run for a trial with approved criteria |
| `get_run` | Status, funnel, verdicts and model use of a run |
| `list_candidates` | Patients of a run by verdict |
| `explain_patient` | Criteria and cited evidence of one patient (not the whole chart) |

No tool approves a referral or a LOINC mapping. A person does that in the app.

## Claude Code

```bash
claude mcp add trialiq -- /path/to/TrialIQ/.venv/bin/python -m backend.app.mcp_server
```

Run it from the project folder, or set `PYTHONPATH` to it.

## Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "trialiq": {
      "command": "/path/to/TrialIQ/.venv/bin/python",
      "args": ["-m", "backend.app.mcp_server"],
      "env": {"PYTHONPATH": "/path/to/TrialIQ", "TRIALIQ_API_URL": "http://127.0.0.1:8020"}
    }
  }
}
```

Example prompt: "Which heart failure trial in TrialIQ has the most eligible patients, and why is P0091 eligible?"
