"""
Provider chain for every model call in the application.

Previously each caller picked one provider from settings and, if that call
failed, gave up — the report fell back to rule-based prose, allergen inference
returned nothing, goal profiles stayed pending. One transient 400 or 429 was
enough, and provider selection was written out at five call sites that had all
drifted slightly apart.

This module owns provider selection once, and turns it into a chain:

    Gemini Flash ─▶ Groq ─▶ Cerebras ─▶ OpenRouter ─▶ (caller's own fallback)

Every provider speaks the OpenAI chat-completions protocol, so one client
works for all of them; they differ only in base URL, model names, and whether
they support JSON mode. A provider with no API key configured is skipped
silently rather than counted as a failure — an unconfigured provider is not an
outage.

The chain is tried in order and the first success wins. Only when *every*
configured provider has failed does the caller's own fallback run, which is
what the rule-based report was always meant to be: a last resort, not a
routine outcome.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import get_settings

settings = get_settings()


@dataclass(frozen=True)
class Provider:
    name: str
    # None means the OpenAI SDK's own default.
    base_url: Optional[str]
    default_model: str
    # None means this provider cannot be used for image input, so it is
    # skipped for vision calls rather than sent an image it will reject.
    default_vision_model: Optional[str]
    # Config keys holding an API key for this provider, in priority order.
    key_settings: tuple[str, ...]
    # Whether response_format={"type": "json_object"} is accepted. Providers
    # that ignore or reject it get the parameter omitted; the prompt asks for
    # JSON anyway and _parse_ai_response strips markdown fences.
    supports_json_mode: bool = True
    extra_headers: dict = field(default_factory=dict)


# Ordered registry. Editing DEFAULT_CHAIN below changes what runs and in what
# order; this is just what each provider is.
PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        # Measured on a full product report, 3 runs each:
        #
        #   gemini-3.5-flash-lite    3.0s   1426 chars of analysis
        #   gemini-3.1-flash-lite    4.3s   1309
        #   gemini-3.5-flash        11.5s   1762
        #   gemini-3.6-flash          503 "experiencing high demand"
        #
        # flash-lite is both the fastest and the most reliable, and its output
        # is no thinner — 3.6-flash was returning 503s and, when it did answer,
        # took 13-34s while a user waited on the product page.
        default_model="gemini-3.5-flash-lite",
        # Same model reads labels at the full flash model's accuracy for a
        # tenth of the latency — see DEFAULT_VISION_MODELS in app/ocr/gateway.py.
        default_vision_model="gemini-3.5-flash-lite",
        key_settings=("GEMINI_API_KEY", "OCR_API_KEY"),
    ),
    "groq": Provider(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="openai/gpt-oss-20b",
        # Groq's vision line-up varies by account and this project's serves
        # none, so it never takes a vision call unless GROQ_VISION_MODEL says
        # otherwise.
        default_vision_model=None,
        key_settings=("GROQ_API_KEY",),
    ),
    "cerebras": Provider(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        default_model="llama-3.3-70b",
        default_vision_model=None,
        key_settings=("CEREBRAS_API_KEY",),
    ),
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        # A free model, so the last rung of the chain costs nothing. Free tiers
        # are rate-limited and rotate; override with OPENROUTER_MODEL.
        default_model="meta-llama/llama-3.3-70b-instruct:free",
        default_vision_model=None,
        key_settings=("OPENROUTER_API_KEY",),
        # Free models frequently ignore or reject JSON mode.
        supports_json_mode=False,
        # OpenRouter asks callers to identify themselves; it also affects
        # free-tier rate limits.
        extra_headers={"X-Title": "Nirnavi"},
    ),
    "openai": Provider(
        name="openai",
        base_url=None,
        default_model="gpt-4o-mini",
        default_vision_model="gpt-4o-mini",
        key_settings=("OPENAI_API_KEY",),
    ),
}

DEFAULT_CHAIN = ("gemini", "groq", "cerebras", "openrouter")

# Errors worth retrying on the SAME provider before moving on. The first is
# the failure that prompted this module: a reasoning model spends its budget
# thinking, runs out mid-JSON, and the provider rejects the truncated
# document. Retrying with room to finish fixes it; moving to the next provider
# would just repeat it there.
_TRUNCATION_MARKERS = (
    "json_validate_failed",
    "max completion tokens reached",
    "failed to generate json",
    "failed to validate json",
)

# Ceiling for that retry, so a pathological prompt cannot escalate forever.
_MAX_TOKEN_CEILING = 8000


class AllProvidersFailed(Exception):
    """Every configured provider failed. Carries what each one said."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "no providers configured"
        super().__init__(f"All AI providers failed ({detail})")


@dataclass
class CompletionResult:
    data: dict
    provider: str
    model: str


def _setting(name: str) -> str:
    return (getattr(settings, name, "") or "").strip()


def resolve_key(provider_name: str) -> str:
    """
    The API key for one provider, or "" if it has none.

    Checks the provider's own key settings first, then falls back to the
    legacy single-key settings — so an existing deployment configured with
    AI_PROVIDER + AI_API_KEY keeps working, and its key is used for exactly
    the provider it belongs to rather than being offered to all of them.
    """
    provider = PROVIDERS.get(provider_name)
    if provider:
        for key_setting in provider.key_settings:
            value = _setting(key_setting)
            if value:
                return value

    legacy_provider = _setting("AI_PROVIDER").lower()
    legacy_provider = "gemini" if legacy_provider == "google" else legacy_provider
    if legacy_provider == provider_name and _setting("AI_API_KEY"):
        return _setting("AI_API_KEY")

    ocr_provider = _setting("OCR_PROVIDER").lower()
    ocr_provider = "gemini" if ocr_provider == "google" else ocr_provider
    if ocr_provider == provider_name and _setting("OCR_API_KEY"):
        return _setting("OCR_API_KEY")

    return ""


def resolve_model(provider_name: str, *, vision: bool = False) -> Optional[str]:
    """
    Which model to use for a provider, honouring a per-provider override.

    `<PROVIDER>_MODEL` / `<PROVIDER>_VISION_MODEL` win when set — model names
    are not portable between providers, so there is deliberately no global
    override that would send a Gemini model id to Groq.
    """
    provider = PROVIDERS.get(provider_name)
    if not provider:
        return None

    prefix = provider_name.upper()
    override = _setting(f"{prefix}_VISION_MODEL" if vision else f"{prefix}_MODEL")
    if override:
        return override

    return provider.default_vision_model if vision else provider.default_model


def resolve_chain(*, vision: bool = False) -> list[str]:
    """
    The providers that will actually be tried, in order.

    AI_PROVIDER_CHAIN sets the order. Providers with no key are dropped, as
    are providers with no vision model when the call carries an image.

    A configured AI_PROVIDER that is not already in the chain is appended
    rather than ignored, so an existing single-provider deployment keeps
    working without editing the chain.
    """
    raw = _setting("AI_PROVIDER_CHAIN")
    explicit = bool(raw)
    names = [n.strip().lower() for n in raw.split(",") if n.strip()] if raw else list(DEFAULT_CHAIN)
    names = ["gemini" if n == "google" else n for n in names]

    # A deployment that predates the chain configures AI_PROVIDER alone, and
    # that provider must still be tried — so it is appended when the chain is
    # the built-in default.
    #
    # But only then. Someone who has written out AI_PROVIDER_CHAIN has stated
    # the order they want, and quietly appending a provider they left out
    # would contradict it — AI_PROVIDER still holds "groq" from the shipped
    # defaults on most installs, so this would fire almost every time.
    if not explicit:
        legacy = _setting("AI_PROVIDER").lower()
        legacy = "gemini" if legacy == "google" else legacy
        if legacy and legacy in PROVIDERS and legacy not in names:
            names.append(legacy)

    chain = []
    for name in names:
        provider = PROVIDERS.get(name)
        if not provider or name in chain:
            continue
        if not resolve_key(name):
            continue
        if vision and not resolve_model(name, vision=True):
            continue
        chain.append(name)
    return chain


def chain_models(*, vision: bool = False) -> set[str]:
    """
    Every model the chain might answer with.

    Used by caches that key on the model: with a chain, a cached verdict may
    have come from any provider in it, and all of them are trusted enough to
    be in the chain in the first place.
    """
    return {
        m for name in resolve_chain(vision=vision)
        if (m := resolve_model(name, vision=vision))
    }


def _is_truncation(error: Exception) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _TRUNCATION_MARKERS)


async def _call_provider(
    provider: Provider,
    model: str,
    messages: list,
    *,
    max_tokens: int,
    temperature: float,
    timeout: float,
) -> str:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=resolve_key(provider.name),
        base_url=provider.base_url,
        timeout=timeout,
        # The chain is the retry strategy; the SDK retrying internally just
        # delays the move to the next provider.
        max_retries=0,
        default_headers=provider.extra_headers or None,
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if provider.supports_json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = await client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or "{}"


async def complete_json(
    messages: list,
    *,
    max_tokens: int = 4000,
    temperature: float = 0.2,
    vision: bool = False,
    timeout: Optional[float] = None,
    chain: Optional[list[str]] = None,
) -> CompletionResult:
    """
    Ask the provider chain for a JSON object, returning the first success.

    Raises AllProvidersFailed only when every configured provider has been
    tried and failed — that is the signal for a caller to use its own
    fallback, and it should now be rare rather than routine.
    """
    from app.ai.gateway import _parse_ai_response

    names = chain if chain is not None else resolve_chain(vision=vision)
    timeout = timeout if timeout is not None else settings.AI_TIMEOUT
    errors: dict[str, str] = {}

    for name in names:
        provider = PROVIDERS[name]
        model = resolve_model(name, vision=vision)
        if not model:
            continue

        budget = max_tokens
        for attempt in range(2):
            try:
                raw = await _call_provider(
                    provider, model, messages,
                    max_tokens=budget, temperature=temperature, timeout=timeout,
                )
                data = _parse_ai_response(raw)
                # A JSON object was asked for. A model that returns a bare
                # array — which they do, especially the ones without JSON
                # mode — would reach callers that all do `data.get(...)` and
                # crash them with AttributeError. Treated as a failed attempt
                # so the chain moves on, which is what the chain is for.
                if not isinstance(data, dict):
                    raise ValueError(
                        f"expected a JSON object, got {type(data).__name__}"
                    )
                return CompletionResult(data=data, provider=name, model=model)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — provider SDKs raise many types
                # A truncated JSON document is worth one more go with room to
                # finish; anything else means move on.
                if attempt == 0 and _is_truncation(e) and budget < _MAX_TOKEN_CEILING:
                    budget = min(budget * 2, _MAX_TOKEN_CEILING)
                    print(
                        f"⚠️ AI provider '{name}' ran out of tokens mid-JSON; "
                        f"retrying with max_tokens={budget}"
                    )
                    continue
                errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"⚠️ AI provider '{name}' ({model}) failed: {errors[name]}")
                break

    raise AllProvidersFailed(errors)
