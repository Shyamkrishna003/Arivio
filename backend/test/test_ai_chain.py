"""
Exercise the AI provider chain.

    python test/test_ai_chain.py [--live]

By default no provider is called: chain resolution, key routing and fallback
ordering are checked against stubs, so this runs anywhere and costs nothing.

`--live` additionally sends a real report through whatever providers are
configured, which is the only way to see the thing this chain exists to fix —
a real transient failure being absorbed instead of dropping the user to
rule-based prose.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.ai import providers as P  # noqa: E402
from app.ai import gateway as G  # noqa: E402
from app.core.config import Settings  # noqa: E402

LIVE = "--live" in sys.argv
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  {'✅' if condition else '❌'} {message}")
    if not condition:
        failures.append(message)


def with_settings(**overrides):
    """Swap in a settings object for one assertion."""
    return Settings(_env_file=None, **overrides)


def test_chain_resolution() -> None:
    print("\n── Chain resolution ──")
    original = P.settings
    try:
        # Only providers with a key take part.
        P.settings = with_settings(GEMINI_API_KEY="k", GROQ_API_KEY="k")
        check(P.resolve_chain() == ["gemini", "groq"],
              f"only keyed providers are in the chain -> {P.resolve_chain()}")

        P.settings = with_settings()
        check(P.resolve_chain() == [], "no keys means no chain (rule-based mode)")

        # Order follows AI_PROVIDER_CHAIN.
        P.settings = with_settings(
            AI_PROVIDER_CHAIN="openrouter,gemini",
            GEMINI_API_KEY="k", OPENROUTER_API_KEY="k", GROQ_API_KEY="k",
        )
        check(P.resolve_chain() == ["openrouter", "gemini"],
              f"order follows the configured chain -> {P.resolve_chain()}")

        # A legacy single-provider deployment keeps working.
        P.settings = with_settings(AI_PROVIDER="groq", AI_API_KEY="legacy")
        check(P.resolve_chain() == ["groq"],
              f"AI_PROVIDER + AI_API_KEY alone still works -> {P.resolve_chain()}")
        check(P.resolve_key("groq") == "legacy", "legacy key routed to its own provider")
        check(P.resolve_key("gemini") == "",
              "a legacy key is NOT offered to other providers")

        # Vision calls skip providers with no vision model.
        P.settings = with_settings(GEMINI_API_KEY="k", GROQ_API_KEY="k", CEREBRAS_API_KEY="k")
        check(P.resolve_chain(vision=True) == ["gemini"],
              f"vision chain drops text-only providers -> {P.resolve_chain(vision=True)}")

        # Per-provider model overrides.
        P.settings = with_settings(GEMINI_API_KEY="k", GEMINI_MODEL="gemini-custom")
        check(P.resolve_model("gemini") == "gemini-custom", "GEMINI_MODEL override honoured")
        check(P.resolve_model("groq") == "openai/gpt-oss-20b",
              "an override for one provider does not leak to another")
    finally:
        P.settings = original


def test_truncation_detection() -> None:
    print("\n── Truncation detection ──")
    # The failure that motivated the chain: a reasoning model spends its
    # budget thinking and is cut off mid-JSON. Retrying the same provider with
    # room to finish is right; moving on would just repeat it.
    for message in (
        "Error code: 400 - {'code': 'json_validate_failed'}",
        "max completion tokens reached before generating a valid document",
        "Failed to generate JSON. Please adjust your prompt.",
    ):
        check(P._is_truncation(Exception(message)), f"recognised: {message[:52]}…")
    for message in ("Error code: 401 - invalid api key", "Request timed out."):
        check(not P._is_truncation(Exception(message)), f"not truncation: {message[:40]}")


async def test_fallthrough() -> None:
    print("\n── Fall-through ──")
    original_call = P._call_provider
    attempted: list[str] = []

    async def stub(provider, model, messages, **kwargs):
        attempted.append(provider.name)
        if provider.name in FAILING:
            raise RuntimeError(f"{provider.name} is down")
        return '{"summary": "ok", "detailed_analysis": "a"}'

    original_settings = P.settings
    P._call_provider = stub
    P.settings = with_settings(
        GEMINI_API_KEY="k", GROQ_API_KEY="k", CEREBRAS_API_KEY="k", OPENROUTER_API_KEY="k")
    try:
        global FAILING
        FAILING = set()
        attempted.clear()
        r = await P.complete_json([{"role": "user", "content": "x"}])
        check(r.provider == "gemini" and attempted == ["gemini"],
              f"a healthy primary is the only provider called -> {attempted}")

        FAILING = {"gemini"}
        attempted.clear()
        r = await P.complete_json([{"role": "user", "content": "x"}])
        check(r.provider == "groq" and attempted == ["gemini", "groq"],
              f"primary down falls through to the next -> {attempted}")

        FAILING = {"gemini", "groq", "cerebras"}
        attempted.clear()
        r = await P.complete_json([{"role": "user", "content": "x"}])
        check(r.provider == "openrouter",
              f"three failures still reach the last rung -> {attempted}")

        FAILING = {"gemini", "groq", "cerebras", "openrouter"}
        attempted.clear()
        try:
            await P.complete_json([{"role": "user", "content": "x"}])
            check(False, "all providers failing raises AllProvidersFailed")
        except P.AllProvidersFailed as e:
            check(len(e.errors) == 4, f"the error names every provider tried -> {sorted(e.errors)}")

        # Truncation retries the SAME provider once with a bigger budget.
        budgets: list[int] = []

        async def truncating(provider, model, messages, **kwargs):
            budgets.append(kwargs["max_tokens"])
            if len(budgets) == 1:
                raise RuntimeError("400 json_validate_failed: max completion tokens reached")
            return '{"summary": "ok"}'

        P._call_provider = truncating
        FAILING = set()
        r = await P.complete_json([{"role": "user", "content": "x"}], max_tokens=2000)
        check(budgets == [2000, 4000],
              f"a truncated response is retried with double the budget -> {budgets}")
        check(r.provider == "gemini", "and stays on the same provider")
    finally:
        P._call_provider = original_call
        P.settings = original_settings


async def test_report_fallback() -> None:
    print("\n── Report fallback ──")
    original_settings = G.settings
    try:
        # With no provider configured the report goes straight to rule-based
        # without pretending to try.
        import app.ai.providers as prov
        saved = prov.settings
        prov.settings = with_settings()
        report = await G.generate_report(
            product_name="Test", product_brand=None, product_category=None,
            suitability_score=50, verdict="Moderate", allergen_safe=True,
            flags=[], goal_alignments=[], nutrition=None, ingredients=[],
            user_goals=[], user_allergies=[],
        )
        check(report.provider == "fallback",
              f"no providers configured -> rule-based ({report.provider})")
        check(bool(report.summary), "the rule-based report still has a summary")
        prov.settings = saved
    finally:
        G.settings = original_settings


async def test_live() -> None:
    print("\n── Live (real providers) ──")
    chain = P.resolve_chain()
    if not chain:
        print("  ⚠️  SKIPPED: no providers configured.")
        return
    print(f"  chain: {' → '.join(chain)}")

    used: dict[str, int] = {}
    for i in range(5):
        report = await G.generate_report(
            product_name="Cadbury Dairy Milk", product_brand="Cadbury",
            product_category="Confectionery", suitability_score=16,
            verdict="Not recommended", allergen_safe=True,
            flags=[{"flag_type": "warning", "category": "nutrition",
                    "title": "High sugar", "description": "56g per 100g", "impact": -20}],
            goal_alignments=[{"goal": "Weight Loss", "alignment": "poor",
                              "score": 25, "reason": "high sugar", "evaluated": True}],
            nutrition={"energy_kcal": 534, "total_sugars_g": 56.0, "protein_g": 7.3},
            ingredients=[{"name": "sugar"}, {"name": "cocoa butter"}],
            user_goals=[{"goal_type": "weight loss"}], user_allergies=[],
        )
        used[report.provider] = used.get(report.provider, 0) + 1

    print(f"  answered by: {used}")
    check(used.get("fallback", 0) == 0,
          f"no run fell back to rule-based ({used.get('fallback', 0)}/5)")


async def main() -> int:
    test_chain_resolution()
    test_truncation_detection()
    await test_fallthrough()
    await test_report_fallback()
    if LIVE:
        await test_live()
    else:
        print("\n(run with --live to also call the real providers)")

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ All AI-chain checks passed.")
    return 0


FAILING: set = set()
sys.exit(asyncio.run(main()))
