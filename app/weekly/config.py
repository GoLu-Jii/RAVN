"""Configuration for the weekly detector, with environment overrides."""
from dataclasses import dataclass, fields
import os
from typing import Mapping

from dotenv import load_dotenv

load_dotenv()


def normalize_voyage_endpoint(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    if value.endswith("/v1"):
        return value + "/embeddings"
    if not value.endswith("/embeddings"):
        return value + "/v1/embeddings"
    return value


@dataclass(frozen=True)
class WeeklyConfig:
    week_start: str = "monday"
    schedule_hour: int = 3
    schedule_minute: int = 0
    timezone: str = "Asia/Kolkata"
    history_weeks: int = 12
    half_life_weeks: float = 2.0
    max_queries: int = 40
    example_k: int = 5
    count_k: int = 50
    min_similarity: float | None = None
    input_token_budget: int = 12000
    retrieval_budget_share: float = 0.5
    max_output_tokens: int = 4000
    max_signals: int = 8
    unverified_threshold: float = 0.20
    trend_min_weeks: int = 3
    weekly_llm_model: str = "openai/gpt-oss-120b"
    embed_model_id: str = "voyage-4-lite"
    prep_version: str = "voyage-document-v1"
    voyage_endpoint: str = "https://ai.mongodb.com/v1/embeddings"
    retry_attempts: int = 6

    def __post_init__(self) -> None:
        if self.week_start.lower() != "monday":
            raise ValueError("The weekly design uses Monday as the fixed week start.")
        if not 0 <= self.schedule_hour <= 23 or not 0 <= self.schedule_minute <= 59:
            raise ValueError("Schedule hour/minute are out of range.")
        for name in ("history_weeks", "max_queries", "example_k", "count_k", "input_token_budget", "max_output_tokens", "max_signals", "retry_attempts"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1.")
        if self.half_life_weeks <= 0 or self.trend_min_weeks < 2:
            raise ValueError("Half-life must be positive and a trend must span at least two weeks.")
        if not 0 <= self.retrieval_budget_share <= 1:
            raise ValueError("retrieval_budget_share must be between 0 and 1.")
        if not 0 <= self.unverified_threshold <= 1:
            raise ValueError("unverified_threshold must be between 0 and 1.")
        if self.min_similarity is not None and not -1 <= self.min_similarity <= 1:
            raise ValueError("min_similarity must be between -1 and 1.")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "WeeklyConfig":
        source = os.environ if environ is None else environ
        values = {}
        for field in fields(cls):
            raw = source.get(f"RAVN_WEEKLY_{field.name.upper()}")
            if field.name == "voyage_endpoint" and raw is None:
                raw = source.get("VOYAGE_EMBEDDING_PROVIDER_ENDPOINT") or source.get("ENDPOINT")
            if raw is None:
                continue
            default = field.default
            if default is None:
                values[field.name] = float(raw) if raw.strip() else None
            elif isinstance(default, bool):
                values[field.name] = raw.strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(default, int):
                values[field.name] = int(raw)
            elif isinstance(default, float):
                values[field.name] = float(raw)
            else:
                values[field.name] = raw.strip()
                if field.name == "voyage_endpoint":
                    values[field.name] = normalize_voyage_endpoint(values[field.name])
        return cls(**values)


WEEKLY_CONFIG = WeeklyConfig.from_env()
