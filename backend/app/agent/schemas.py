"""Structured outputs of the agents. Kept flat and short, so a local 8B model can fill them."""

from typing import Literal

from pydantic import BaseModel, Field

from ..criteria import Criterion

Finding = Literal["present", "absent", "unclear"]


class Answer(BaseModel):
    question_id: str = Field(description="The id of the question, e.g. Q1")
    finding: Finding = Field(description="present: the fact is true for this patient now / in the window. absent: the "
                                         "notes show it is not (negated, family member, resolved, outside the window). "
                                         "unclear: the notes do not decide it")
    note_id: str = Field("", description="The note that holds the quote, e.g. P0012-N3")
    quote: str = Field("", description="The exact words from that note, copied, at most 25 words")
    value: float | None = Field(None, description="For a lab or measurement question: the number in the quote")
    unit: str | None = Field(None, description="The unit of the value as written, e.g. %, mmol/mol, mg/mmol, pg/mL")
    date: str | None = Field(None, description="Date of the fact as YYYY-MM-DD (lab date, diagnosis date, event date, medicine start), if known")
    end_date: str | None = Field(None, description="For a medicine course: the stop date as YYYY-MM-DD, if it stopped")
    reason: str = Field("", description="One short sentence")


class ChartAnswers(BaseModel):
    answers: list[Answer]


class Verdict(BaseModel):
    question_id: str
    ok: bool = Field(description="True when the quote supports the finding")
    problem: str = Field("", description="If not ok: what is wrong, e.g. 'the quote is about the mother', 'negated', "
                                         "'event is older than the window'")


class ReviewVerdicts(BaseModel):
    verdicts: list[Verdict]


class DraftCriteria(BaseModel):
    criteria: list[Criterion]


class CodeChoice(BaseModel):
    criterion_id: str
    atom_index: int = Field(description="1-based index of the atom in the criterion")
    codes: list[str] = Field(description="Codes copied from tool results")


class CodeChoices(BaseModel):
    choices: list[CodeChoice]


class CriteriaReview(BaseModel):
    approved: bool
    problems: list[str] = Field(default_factory=list, description="Each problem names the criterion id")


class MappingProposal(BaseModel):
    source_code: str
    loinc: str = Field(description="A LOINC code copied from search_loinc results, or empty when none fits")
    confidence: Literal["high", "medium", "low"]
    reason: str


class Referral(BaseModel):
    subject: str
    body: str
