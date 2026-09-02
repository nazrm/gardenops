from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from gardenops.models import StrictBaseModel

AssistantIntentKind = Literal[
    "question",
    "journal",
    "harvest",
    "issue",
    "task_completion",
    "unknown",
]
AssistantEventType = Literal[
    "planted",
    "moved",
    "divided",
    "pruned",
    "watered",
    "fertilized",
    "bloomed",
    "died",
    "observed",
]
AssistantResultState = Literal[
    "answer",
    "needs_input",
    "proposal",
    "applied",
    "cancelled",
    "error",
]


class AssistantIntent(StrictBaseModel):
    intent: AssistantIntentKind
    confidence: float = Field(ge=0, le=1)
    plant_query: str = Field(default="", max_length=200)
    plot_query: str = Field(default="", max_length=200)
    occurred_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    event_type: AssistantEventType | None = None
    title: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=4000)
    quantity: float | None = Field(default=None, ge=0)
    unit: (
        Literal["kg", "g", "lbs", "oz", "pieces", "bunches", "liters", "heads", "other"] | None
    ) = None
    quality: Literal["excellent", "good", "fair", "poor"] = "good"
    issue_type: (
        Literal["pest", "disease", "fungal", "nutrient", "environmental", "damage", "other"] | None
    ) = None
    severity: Literal["low", "normal", "high", "critical"] = "normal"
    symptoms: str = Field(default="", max_length=1000)
    task_query: str = Field(default="", max_length=300)


class CaptureFieldCandidate(StrictBaseModel):
    value: str = Field(max_length=500)
    confidence: float = Field(ge=0, le=1)


class CapturePlantCandidate(StrictBaseModel):
    name: str = Field(default="", max_length=200)
    latin: str = Field(default="", max_length=200)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(default="", max_length=40)
    taxonomy_refs: list[str] = Field(default_factory=list, max_length=10)


class CaptureAnalysis(StrictBaseModel):
    plant_candidates: list[CapturePlantCandidate] = Field(default_factory=list, max_length=5)
    event_candidate: CaptureFieldCandidate | None = None
    issue_candidate: CaptureFieldCandidate | None = None
    requires_clarification: bool = False


class AssistantChoice(StrictBaseModel):
    value: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=500)


class AssistantProposal(StrictBaseModel):
    kind: Literal["journal", "harvest", "issue", "task_completion"]
    summary: str = Field(min_length=1, max_length=500)
    fields: dict = Field(default_factory=dict)


class AssistantRecord(StrictBaseModel):
    type: Literal["journal_entry", "harvest_entry", "issue", "task"]
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=200)


class AssistantResult(StrictBaseModel):
    state: AssistantResultState
    request_id: str = Field(min_length=1, max_length=120)
    reference: str = Field(min_length=1, max_length=20)
    message: str = Field(max_length=6000)
    choices: list[AssistantChoice] = Field(default_factory=list, max_length=20)
    proposal: AssistantProposal | dict = Field(default_factory=dict)
    records: list[AssistantRecord] = Field(default_factory=list, max_length=20)
    retryable: bool = False

    @field_validator("proposal")
    @classmethod
    def validate_empty_proposal(cls, value: AssistantProposal | dict) -> AssistantProposal | dict:
        if isinstance(value, dict) and value:
            return AssistantProposal.model_validate(value)
        return value


class ProcessTextInput(StrictBaseModel):
    source_room_id: str = Field(min_length=1, max_length=255)
    source_event_id: str = Field(min_length=1, max_length=255)
    source_sender_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=2000)
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class AnalyzeCaptureInput(StrictBaseModel):
    source_room_id: str = Field(min_length=1, max_length=255)
    source_event_id: str = Field(min_length=1, max_length=255)
    source_sender_id: str = Field(min_length=1, max_length=255)
    capture_asset_id: str = Field(min_length=1, max_length=120)
    caption: str = Field(default="", max_length=2000)
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ContinueInput(StrictBaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=2000)


class RequestEventInput(StrictBaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(default="", max_length=255)
