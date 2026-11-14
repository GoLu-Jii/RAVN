"""Strict JSON output contract for the weekly LLM stage."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SourceName = Literal["github", "ats", "blog", "web_social"]


class WeeklySignalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    sources: list[SourceName]
    cited_event_ids: list[int]
    connection_note: str


class WeeklyLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    data_limitations: list[str]
    signals: list[WeeklySignalCandidate]


def validate_output(data: dict, max_signals: int) -> WeeklyLLMOutput:
    """Validate with either supported Pydantic API and enforce configured cap."""
    if hasattr(WeeklyLLMOutput, "model_validate"):
        result = WeeklyLLMOutput.model_validate(data)
    else:  # Pydantic v1 compatibility
        result = WeeklyLLMOutput.parse_obj(data)
    if len(result.signals) > max_signals:
        raise ValueError(f"Output contains more than the allowed {max_signals} signals.")
    return result
