export type Status = "met" | "not_met" | "unknown";
export type Verdict = "eligible" | "ineligible" | "needs_review";

export interface Atom {
  type: string; concept: string; codes?: string[]; keywords?: string[]; min?: number | null; max?: number | null;
  min_exclusive?: boolean; max_exclusive?: boolean; unit?: string | null; lookback_days?: number | null;
  min_duration_days?: number | null; negate?: boolean; all_of?: Atom[]; aggregate?: string; status?: string;
  ignore_courses_up_to_days?: number | null; value?: string | null;
}
export interface Criterion { id: string; kind: "inclusion" | "exclusion"; text: string; screenable?: boolean; combine?: string; atoms: Atom[]; hints?: string[] }
export interface CriteriaSet { nct_id: string; criteria: Criterion[]; approved_by?: string; approved_at?: string; source?: string; version?: number; changes?: string[] }

export interface Trial {
  nct_id: string; title: string; official_title?: string; status: string; phases: string[]; conditions: string[];
  sponsor: string; min_age?: string; max_age?: string; indiana_sites?: string[]; last_update?: string; fetched?: string;
  eligibility?: string; criteria?: string | null; last_screen?: Run | null;
}
export interface Run {
  id: string; kind: string; nct_id: string; status: string; stage: string; actor: string; created_at: string;
  updated_at: string; summary: Record<string, any>; error: string; usage?: Usage; busy?: boolean;
  criteria?: CriteriaSet | null; mappings_used?: Record<string, string> | null;
}
export interface Usage {
  calls: number; tokens: number; failed: number; median_ms: number | null;
  per_agent: Record<string, { calls: number; ms: number; tokens_in: number; tokens_out: number; failed: number }>;
  models: Record<string, number>;
}
export interface RunEvent { seq: number; at: string; kind: string; agent: string; patient: string; text: string; data: any }
export interface ResultRow {
  person_id: string; stage: string; verdict: Verdict; reasons: string[]; criteria: Record<string, Status>;
  notes_used: number; flags: number;
  person: { name: string; age: number; sex: string; race: string; ethnicity: string; clinic: string; pcp: string };
}
export interface AtomResult {
  key: string; label: string; type: string; status: Status; evidence: string[]; open: boolean; source: string;
  negate: boolean; hits: { note_id: string; date: string; type: string; sentence: string }[]; children: AtomResult[];
}
export interface CriterionResult { id: string; kind: string; text: string; status: Status; atoms: AtomResult[]; fails: boolean; passes: boolean }
export interface Resolution {
  question_id: string; concept: string; keys: string[]; status: Status; finding: string; note_id: string; quote: string;
  value: number | null; unit: string | null; date: string | null; reason: string; problems: string[]; reviewer: string; round: number;
}
export interface PatientDetail {
  person_id: string; stage: string; verdict: Verdict;
  data: { person: ResultRow["person"]; reasons: string[]; screen: { criteria: CriterionResult[] }; resolutions: Resolution[]; review_error?: string };
  notes: { note_id: string; date: string; type: string; author: string; text: string }[];
}
export interface Outreach { run_id: string; person_id: string; status: string; subject: string; body: string; to_name: string; decided_by?: string }
export interface Feedback {
  id: number; nct_id: string; run_id: string; person_id: string; kind: string; criterion_id: string;
  system_value: string; correct_value: string; reason: string; actor: string; at: string; lesson_id: string;
}
export interface Lesson {
  id: string; nct_id: string; run_id: string; created_at: string; status: string; kind: string; criterion_id: string;
  atom_index: number | null; value: string; rationale: string; feedback_ids: number[]; problems: string[];
  impact: { changed: number; warnings?: string[]; moves: Record<string, number>; patients: string[]; open_facts: [number, number]; focus: Record<string, [string, string]> } | null;
  decided_by?: string; decided_at?: string;
}
export interface LabCode {
  source_code: string; source_name: string; unit: string; loinc: string | null; clinic: string; clinic_name: string; row_count: number;
  mapping: { loinc: string; status: string; confidence: string; reason: string; proposed_by: string; decided_by?: string } | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-User": localStorage.getItem("trialiq.user") || "coordinator", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  health: () => call<any>("/health"),
  overview: () => call<any>("/overview"),
  trials: () => call<Trial[]>("/trials"),
  trial: (id: string) => call<{ trial: Trial; criteria: CriteriaSet | null; criteria_source: string | null; drafts: any[]; runs: Run[] }>(`/trials/${id}`),
  addTrial: (nct_id: string) => call<Trial>("/trials", { method: "POST", body: JSON.stringify({ nct_id }) }),
  searchCtgov: (condition: string) => call<any[]>(`/ctgov/search?condition=${encodeURIComponent(condition)}`),
  buildCriteria: (id: string) => call<{ run_id: string }>(`/trials/${id}/criteria/build`, { method: "POST" }),
  approveCriteria: (id: string, draft_id: string) => call(`/trials/${id}/criteria/approve`, { method: "POST", body: JSON.stringify({ draft_id }) }),
  estimate: (id: string) => call<any>(`/trials/${id}/estimate`),
  screen: (id: string) => call<{ run_id: string }>(`/trials/${id}/screen`, { method: "POST" }),
  runs: (nct?: string) => call<Run[]>(`/runs${nct ? `?nct_id=${nct}` : ""}`),
  run: (id: string) => call<Run>(`/runs/${id}`),
  events: (id: string, after = 0) => call<RunEvent[]>(`/runs/${id}/events?after=${after}`),
  results: (id: string) => call<ResultRow[]>(`/runs/${id}/results`),
  patient: (run: string, pid: string) => call<PatientDetail>(`/runs/${run}/patients/${pid}`),
  outreach: (id: string) => call<Outreach[]>(`/runs/${id}/outreach`),
  decide: (id: string, decisions: any[]) => call(`/runs/${id}/decisions`, { method: "POST", body: JSON.stringify(decisions) }),
  labCodes: () => call<LabCode[]>("/lab-codes"),
  runMapper: () => call<{ run_id: string }>("/mappings/run", { method: "POST" }),
  decideMapping: (code: string, approve: boolean, loinc?: string) =>
    call(`/mappings/${code}`, { method: "POST", body: JSON.stringify({ approve, loinc }) }),
  feedback: (run: string, pid: string, body: { criterion_id?: string; correct: string; reason: string }) =>
    call<{ id: number }>(`/runs/${run}/patients/${pid}/feedback`, { method: "POST", body: JSON.stringify(body) }),
  trialFeedback: (nct: string) => call<Feedback[]>(`/trials/${nct}/feedback`),
  learn: (nct: string) => call<{ run_id: string }>(`/trials/${nct}/learn`, { method: "POST" }),
  lessons: (nct: string) => call<Lesson[]>(`/trials/${nct}/lessons`),
  decideLesson: (id: string, approve: boolean) => call<{ ok: boolean; version: number | null }>(`/lessons/${id}`, { method: "POST", body: JSON.stringify({ approve }) }),
  trace: (run: string, pid: string) => call<{ calls: any[]; events: RunEvent[] }>(`/runs/${run}/patients/${pid}/trace`),
  compare: (a: string, b: string) => call<any>(`/compare?a=${a}&b=${b}`),
  models: () => call<any>("/models"),
  evals: () => call<any>("/evals"),
  audit: () => call<any[]>("/audit"),
  auditVerify: () => call<{ ok: boolean; entries: number; broken_at: any }>("/audit/verify"),
  security: () => call<any>("/security"),
};
