"""Single-provider weekly LLM call with schema validation and bounded JSON retries."""
import json
import logging

from app.llm.clients import get_llm_client
from app.weekly.config import WEEKLY_CONFIG, WeeklyConfig
from app.weekly.prompt import PROMPT_VERSION, build_messages
from app.weekly.schema import validate_output


logger = logging.getLogger(__name__)
JSON_VALIDATION_RETRIES = 2


class WeeklyLLMError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        attempts: int,
        raw_output: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.attempts = attempts
        self.raw_output = raw_output
        self.provider = provider
        self.model = model


def _finish_reason(response) -> str | None:
    raw = getattr(response, "raw_response", None)
    if isinstance(raw, dict):
        choices = raw.get("choices") or []
        return choices[0].get("finish_reason") if choices else None
    choices = getattr(raw, "choices", None) or []
    return getattr(choices[0], "finish_reason", None) if choices else None


def _usage(response) -> dict | None:
    raw = getattr(response, "raw_response", None)
    usage = raw.get("usage") if isinstance(raw, dict) else getattr(raw, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return dict(usage)
    return {
        key: getattr(usage, key)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


def run_weekly_llm(
    weekly_input: dict,
    *,
    client=None,
    config: WeeklyConfig = WEEKLY_CONFIG,
    prompt_version: str = PROMPT_VERSION,
) -> dict:
    """Call only the pinned provider/model; retry malformed output at most twice."""
    client = client or get_llm_client()
    messages = build_messages(weekly_input, max_signals=config.max_signals)
    last_raw = None
    validation_error = None
    response = None

    for attempt in range(1, JSON_VALIDATION_RETRIES + 2):
        if validation_error:
            messages = build_messages(
                weekly_input,
                validation_error,
                max_signals=config.max_signals,
            )
        try:
            response = client.chat.completions.create(
                messages=messages,
                model=config.weekly_llm_model,
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=config.max_output_tokens,
                allow_fallback=False,
                no_fallbacks=True,
            )
        except Exception as exc:
            logger.exception("Weekly LLM provider call failed")
            raise WeeklyLLMError(
                f"Weekly LLM provider failed: {type(exc).__name__}: {exc}",
                retryable=True,
                attempts=attempt,
                provider="groq",
                model=config.weekly_llm_model,
            ) from exc

        last_raw = getattr(response, "content", None) or ""
        finish_reason = _finish_reason(response)
        if finish_reason == "length":
            raise WeeklyLLMError(
                "Weekly LLM output was truncated at the token limit.",
                retryable=False,
                attempts=attempt,
                raw_output=last_raw,
                provider=getattr(response, "provider", "groq"),
                model=getattr(response, "model", config.weekly_llm_model),
            )

        try:
            parsed_json = json.loads(last_raw)
            output = validate_output(parsed_json, config.max_signals)
            return {
                "output": output.model_dump() if hasattr(output, "model_dump") else output.dict(),
                "raw_output": last_raw,
                "provider": getattr(response, "provider", "groq"),
                "model": getattr(response, "model", config.weekly_llm_model),
                "prompt_version": prompt_version,
                "attempts": attempt,
                "finish_reason": finish_reason,
                "usage": _usage(response),
            }
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            validation_error = f"{type(exc).__name__}: {exc}"
            if attempt >= JSON_VALIDATION_RETRIES + 1:
                raise WeeklyLLMError(
                    f"Weekly LLM output remained invalid after {JSON_VALIDATION_RETRIES} retries: {validation_error}",
                    retryable=False,
                    attempts=attempt,
                    raw_output=last_raw,
                    provider=getattr(response, "provider", "groq"),
                    model=getattr(response, "model", config.weekly_llm_model),
                ) from exc

    raise WeeklyLLMError(
        "Weekly LLM stage ended without a valid response.",
        retryable=False,
        attempts=JSON_VALIDATION_RETRIES + 1,
        raw_output=last_raw,
    )
