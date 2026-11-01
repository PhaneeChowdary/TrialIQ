"""Agent security: what each agent may use, a screen for instructions hidden in patient data, the check that patient
data only goes to allowed models, and the hash chain of the audit log.

Every check here fails closed: a tool that is not on an agent's list is not given to it and a call to it is blocked;
instruction-like text in a note is removed before a model sees it; a chain that would send patient notes to a cloud model while that is switched off is refused. The code guards still check quotes against the original note."""

import hashlib
import re

# --- Permissions: the data level, the tools and the human gate of each agent --------------------------------

AGENTS: dict[str, dict] = {
    "criteria": {"name": "Criteria agent", "data": "protocol", "tools": [], "output": "Criteria draft",
                 "gate": "Criteria approval"},
    "terminology": {"name": "Terminology agent", "data": "protocol",
                    "tools": ["search_ehr_concepts", "search_loinc", "search_icd10", "search_rxnorm"],
                    "output": "Codes in the draft", "gate": "Criteria approval"},
    "criteria_reviewer": {"name": "Criteria reviewer", "data": "protocol", "tools": [], "output": "Review of the draft",
                          "gate": "Criteria approval"},
    "mapper": {"name": "LOINC mapper", "data": "protocol", "tools": ["search_loinc", "search_ehr_concepts"],
               "output": "Mapping proposals", "gate": "Mapping approval"},
    "chart": {"name": "Chart agent", "data": "chart",
              "tools": ["search_notes", "read_note", "list_lab_results", "list_medications"],
              "output": "Answers with quotes", "gate": "Code guards, evidence reviewer"},
    "evidence_reviewer": {"name": "Evidence reviewer", "data": "chart", "tools": [], "output": "Accept or reject",
                          "gate": "Code guards"},
    "reflection": {"name": "Reflection agent", "data": "chart", "tools": [], "output": "Lesson proposals",
                   "gate": "Lesson approval"},
    "outreach": {"name": "Referral writer", "data": "draft", "tools": [], "output": "Referral drafts",
                 "gate": "Referral guard, referral approval"},
}
DATA_LABEL = {"protocol": "Protocol only", "chart": "Patient notes", "draft": "Patient facts"}


def allowed_tools(agent: str) -> set[str]:
    return set(AGENTS.get(agent, {}).get("tools", []))


def permit(agent: str, tools: list) -> tuple[list, list[str]]:
    """The tools this agent may have, and the names of the ones it may not."""
    allowed = allowed_tools(agent)
    return [t for t in tools if t.name in allowed], [t.name for t in tools if t.name not in allowed]


# --- Prompt injection: instructions written into patient data ----------------------------------------------

_INJECTION = [
    r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}\b(?:instructions?|rules?|prompts?|criteria|guidelines)\b",
    r"\b(?:new|updated|revised) instructions?\s*[:\-]",
    r"\byou are now\b",
    r"\b(?:system|developer) (?:prompt|message)\b",
    r"(?:^|\n|:)\s*(?:system|assistant)\s*:",  # a role label ("SYSTEM: ..."), not "Cardiovascular system: normal"
    r"\b(?:admin|developer|debug|god) mode\b",
    r"\b(?:mark|report|classify|label|flag)\b[^.\n]{0,40}\b(?:as )?(?:eligible|ineligible|met|not met)\b",
    r"\b(?:exclude|include|enrol|enroll)\b[^.\n]{0,30}\bfrom (?:all|every|any)\b[^.\n]{0,20}\b(?:trials?|studies|research)\b",
    r"\b(?:to|for) (?:any|the|all) (?:ai|llm|language model|assistant|model|bot)s?\b",
]
INJECTION = re.compile("|".join(f"(?:{p})" for p in _INJECTION), re.I)
_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]|$)")
REMOVED = "[removed: instruction-like text in patient data]"


def screen_text(text: str) -> tuple[str, list[str]]:
    """Removes each sentence that reads as an instruction to a model. Returns the clean text and what it removed."""
    if not text or not INJECTION.search(text):
        return text, []
    found: list[str] = []

    def drop(m: re.Match) -> str:
        sentence = m.group(0)
        if sentence.strip() and INJECTION.search(sentence):
            found.append(sentence.strip())
            return (" " if sentence.startswith(" ") else "") + REMOVED
        return sentence
    return _SENTENCE.sub(drop, text), found


# --- Patient data routing ----------------------------------------------------------------------------------

def check_egress(level: str, links: list[tuple[str, str]], cloud_phi: bool, phi_levels: set[str]) -> None:
    """A chain for patient data may hold a cloud model only when 'cloud models may read notes' is on."""
    if level in phi_levels and not cloud_phi:
        cloud = [p for p, _ in links if p != "ollama"]
        if cloud:
            raise PermissionError(f"patient data may not go to {', '.join(cloud)}: cloud models may not read notes")


# --- Audit log hash chain ------------------------------------------------------------------------------------

GENESIS = "0" * 64


def entry_hash(prev: str, at: str, actor: str, action: str, detail: str) -> str:
    """Each audit entry holds the hash of the entry before it, so an edit or a deleted entry breaks the chain."""
    return hashlib.sha256("\x1f".join([prev, at, actor, action, detail]).encode()).hexdigest()
