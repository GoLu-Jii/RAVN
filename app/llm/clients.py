import os
import json
import logging
from typing import Any, Dict, List, Optional
import requests
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))

logger = logging.getLogger("app.llm")

DEFAULT_OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "liquid/lfm-2.5-2.6b:free")
DEFAULT_OPENROUTER_FALLBACKS = [
    "liquid/lfm-2.5-2.6b:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
]

DEFAULT_GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


class LLMError(Exception):
    """Raised when LLM providers fail."""
    pass


class LLMMessage:
    def __init__(self, content: str, role: str = "assistant"):
        self.content = content
        self.role = role

    def __repr__(self) -> str:
        return f"LLMMessage(role={self.role!r}, content={self.content[:30]!r}...)"


class LLMChoice:
    def __init__(self, message: LLMMessage):
        self.message = message

    def __repr__(self) -> str:
        return f"LLMChoice(message={self.message!r})"


class LLMResponse:
    """
    Unified response object compatible with both Groq/OpenAI response shapes
    (response.choices[0].message.content) and direct string/property access.
    """
    def __init__(self, content: str, provider: str, model: str, raw_response: Any = None):
        self.content = content
        self.text = content
        self.provider = provider
        self.model = model
        self.raw_response = raw_response
        self.choices = [LLMChoice(LLMMessage(content=content, role="assistant"))]

    def __str__(self) -> str:
        return self.content

    def __repr__(self) -> str:
        return f"LLMResponse(provider={self.provider!r}, model={self.model!r}, content={self.content[:40]!r}...)"


def _clean_json_content(content: str) -> str:
    """Strip markdown code fence if LLM wrapped JSON in ```json ... ```"""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _call_openrouter(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    **kwargs: Any,
) -> LLMResponse:
    """Call OpenRouter API (Primary provider, using free-tier models)."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is not set")

    target_model = model or DEFAULT_OPENROUTER_MODEL
    # OpenRouter models follow 'vendor/model-name'. If a legacy model without '/' is passed, use default.
    if "/" not in target_model:
        target_model = DEFAULT_OPENROUTER_MODEL

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": kwargs.get("http_referer", "https://github.com/GoLu-Jii/RAVN"),
        "X-Title": kwargs.get("x_title", "RAVN"),
    }

    # OpenRouter supports up to 3 fallback models in the 'models' parameter.
    # Pass no_fallbacks=True to skip the fallback array and try only the exact model.
    no_fallbacks = kwargs.get("no_fallbacks", False)
    if no_fallbacks:
        fallback_models = [target_model]
    else:
        fallback_models = list(DEFAULT_OPENROUTER_FALLBACKS)
        if target_model not in fallback_models:
            fallback_models = [target_model] + fallback_models[:2]

    payload: Dict[str, Any] = {
        "model": target_model,
        "models": fallback_models[:3],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    is_json = bool(response_format and response_format.get("type") == "json_object")
    if is_json:
        payload["response_format"] = {"type": "json_object"}

    timeout = kwargs.get("timeout", 25)
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)

    if response.status_code != 200:
        raise RuntimeError(
            f"OpenRouter API error (HTTP {response.status_code}): {response.text}"
        )

    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError(f"OpenRouter returned no choices: {data}")

    message = choices[0].get("message", {})
    content = message.get("content") or ""
    model_used = data.get("model", target_model)

    if is_json:
        content = _clean_json_content(content)

    return LLMResponse(
        content=content.strip(),
        provider="openrouter",
        model=model_used,
        raw_response=data,
    )


def _call_gemini(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    **kwargs: Any,
) -> LLMResponse:
    """Call Google Gemini API as fallback provider."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is not set")

    target_model = model or DEFAULT_GEMINI_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    system_parts: List[Dict[str, str]] = []
    contents: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_parts.append({"text": text})
        else:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})

    if not contents and system_parts:
        contents.append({"role": "user", "parts": system_parts})
        system_parts = []

    generation_config: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_tokens,
        "thinkingConfig": {"thinkingBudget": kwargs.get("thinking_budget", 0)},
    }

    is_json = bool(response_format and response_format.get("type") == "json_object")
    if is_json:
        generation_config["responseMimeType"] = "application/json"

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": generation_config,
    }

    if system_parts:
        payload["system_instruction"] = {"parts": system_parts}

    timeout = kwargs.get("timeout", 25)
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)

    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini API error (HTTP {response.status_code}): {response.text}"
        )

    data = response.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data}")

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    if not parts:
        raise RuntimeError(f"Gemini candidate has no content parts: {candidate}")

    content = parts[0].get("text", "").strip()
    if is_json:
        content = _clean_json_content(content)

    return LLMResponse(
        content=content,
        provider="gemini",
        model=target_model,
        raw_response=data,
    )


def _call_groq(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    **kwargs: Any,
) -> LLMResponse:
    """Call Groq API as secondary fallback provider."""
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    target_model = model or DEFAULT_GROQ_MODEL
    client = Groq(api_key=api_key)

    call_params: Dict[str, Any] = {
        "model": target_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        call_params["response_format"] = response_format

    # Strip internal kwargs that shouldn't be forwarded to the Groq SDK
    groq_kwargs = {k: v for k, v in kwargs.items() if k not in ("no_fallbacks", "http_referer", "x_title", "timeout")}
    for k, v in groq_kwargs.items():
        if k not in call_params:
            call_params[k] = v

    response = client.chat.completions.create(**call_params)
    content = response.choices[0].message.content or ""
    return LLMResponse(
        content=content.strip(),
        provider="groq",
        model=target_model,
        raw_response=response,
    )


class ChatCompletionsNamespace:
    """Provides a .chat.completions.create(...) interface compatible with OpenAI/Groq."""
    def __init__(self, client: "LLMClient"):
        self._client = client

    def create(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> LLMResponse:
        return self._client.generate(
            messages=messages,
            model=model,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )


class ChatNamespace:
    def __init__(self, client: "LLMClient"):
        self.completions = ChatCompletionsNamespace(client)


class LLMClient:
    """
    Main LLM client:
    - Primary: OpenRouter (free tier with automatic multi-model fallbacks)
    - Fallback: Gemini and Groq (if OpenRouter encounters daily limits or errors)
    """
    def __init__(
        self,
        primary_model: Optional[str] = None,
    ):
        self.primary_model = primary_model or DEFAULT_OPENROUTER_MODEL
        self.chat = ChatNamespace(self)

    def generate(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        provider: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        Execute completion with provider routing and automatic fallback.
        Provider can be explicitly specified ("groq", "gemini", "openrouter")
        or auto-detected from the model name.
        """
        # Auto-detect provider if model name matches known endpoints
        if not provider and model:
            if model in ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"):
                provider = "groq"
            elif model.startswith("gemini"):
                provider = "gemini"

        # Define call wrappers for each provider
        def call_openrouter():
            target_model = model if (provider in (None, "openrouter")) else DEFAULT_OPENROUTER_MODEL
            logger.debug(f"Attempting OpenRouter ({target_model})...")
            return _call_openrouter(
                messages=messages,
                model=target_model,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        def call_gemini():
            target_model = model if (provider == "gemini") else DEFAULT_GEMINI_MODEL
            logger.debug(f"Attempting Gemini ({target_model})...")
            return _call_gemini(
                messages=messages,
                model=target_model,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        def call_groq():
            target_model = model if (provider == "groq") else DEFAULT_GROQ_MODEL
            logger.debug(f"Attempting Groq ({target_model})...")
            return _call_groq(
                messages=messages,
                model=target_model,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        # Determine attempt order based on requested provider
        if provider == "groq":
            pipeline = [("Groq", call_groq), ("Gemini", call_gemini), ("OpenRouter", call_openrouter)]
        elif provider == "gemini":
            pipeline = [("Gemini", call_gemini), ("Groq", call_groq), ("OpenRouter", call_openrouter)]
        else:
            pipeline = [("OpenRouter", call_openrouter), ("Gemini", call_gemini), ("Groq", call_groq)]

        errors = []
        for name, fn in pipeline:
            try:
                resp = fn()
                if name != pipeline[0][0]:
                    logger.info(f"{name} backup provider succeeded.")
                return resp
            except Exception as e:
                errors.append(f"{name}: {e}")
                logger.warning(f"{name} failed: {e}. Falling back...")

        raise LLMError(f"All LLM providers failed: {'; '.join(errors)}")


# Global shared instance
default_client = LLMClient()


def get_llm_client() -> LLMClient:
    """Get the default shared LLM client."""
    return default_client


def chat_completion(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    response_format: Optional[Dict[str, str]] = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    **kwargs: Any,
) -> LLMResponse:
    """Convenience helper to run a completion using OpenRouter with automatic fallbacks."""
    return default_client.generate(
        messages=messages,
        model=model,
        response_format=response_format,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
