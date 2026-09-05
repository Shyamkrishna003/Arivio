"""
Community feature regression tests — similarity and moderation.

No DB required: both modules are deterministic, which is the point. Relevance
has to be reproducible and explainable, so it is computed from plain dicts and
can be asserted directly.
"""

import sys
sys.path.insert(0, '.')

from app.community.moderation import _looks_like_spam
from app.community.similarity import (
    score_relevance, SIMILARITY_WEIGHTS, RELEVANCE_THRESHOLD,
)

fails = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
    if not ok:
        fails.append(name)


# The worked example from the requirements: two users sharing age band, goal,
# diet and activity. A's experience should reach B.
READER = {
    "age_range": "25-34", "dietary_pattern": "high protein",
    "activity_level": "very active", "goals": ["muscle building"],
    "allergies": [], "usage_duration": "several_months",
}
TWIN = {
    "age_range": "25-34", "dietary_pattern": "high protein",
    "activity_level": "very active", "goals": ["muscle gain"],
    "relevant_allergies": [], "usage_duration": "several months",
}
STRANGER = {
    "age_range": "65-plus", "dietary_pattern": "vegan",
    "activity_level": "sedentary", "goals": ["weight loss"],
    "relevant_allergies": ["peanuts"], "usage_duration": "once",
}

print("=== similar profiles are surfaced, dissimilar ones are not ===")
twin = score_relevance(READER, TWIN)
check("similar profile is relevant", twin.is_relevant, True)
check("similar profile scores high", twin.score > 0.9, True)
stranger = score_relevance(READER, STRANGER)
check("dissimilar profile not relevant", stranger.is_relevant, False)
check("dissimilar profile scores low", stranger.score < RELEVANCE_THRESHOLD, True)

print("\n=== relevance is always explained, never a bare number ===")
check("a relevant match lists its reasons", len(twin.reasons) >= 2, True)
# Differences are reported even on a strong match — hiding them would overstate it.
partial = score_relevance(READER, {**TWIN, "activity_level": "sedentary"})
check("differences are surfaced too", len(partial.differences) >= 1, True)
check("differing dimension is named",
      any("sedentary" in d for d in partial.differences), True)
check("undisclosed dimensions reported as not compared",
      "Age range" in score_relevance(
          READER, {"goals": ["muscle gain"], "dietary_pattern": "high protein",
                   "relevant_allergies": []}).uncomparable, True)

print("\n=== goals are compared after alias resolution ===")
alias = score_relevance(READER, {
    "goals": ["bulking"], "dietary_pattern": "high protein", "relevant_allergies": []})
check("'bulking' matches 'muscle building'", alias.is_relevant, True)
check("shared goal is named in the reason",
      any("muscle gain" in r for r in alias.reasons), True)

print("\n=== an experience with no shared context is never 'relevant to you' ===")
none_ctx = score_relevance(READER, None)
check("no context -> not relevant", none_ctx.is_relevant, False)
check("no context -> zero score", none_ctx.score, 0.0)
check("no context is explained", len(none_ctx.uncomparable), 1)

print("\n=== one agreeing dimension is not a similar profile ===")
thin = score_relevance(
    {"dietary_pattern": "vegan", "goals": [], "allergies": None},
    {"dietary_pattern": "vegan"})
check("single-dimension match rejected", thin.is_relevant, False)

print("\n=== weights renormalise over what was actually comparable ===")
# A reader who disclosed only their diet gets an honest score from that one
# dimension rather than one damped by everything withheld.
diet_only = score_relevance(
    {"dietary_pattern": "vegan", "goals": ["weight loss"], "allergies": []},
    {"dietary_pattern": "vegan", "goals": ["weight loss"], "relevant_allergies": []})
check("partial profiles still score fully when they agree", diet_only.score, 1.0)
check("weights sum to 1.0 as specified", round(sum(SIMILARITY_WEIGHTS.values()), 6), 1.0)

print("\n=== shared allergies are the strongest comparable signal ===")
w = SIMILARITY_WEIGHTS
check("allergy outweighs diet", w["allergy"] > w["dietary_pattern"], True)
check("diet outweighs goal", w["dietary_pattern"] > w["goal"], True)
check("goal outweighs age", w["goal"] > w["age_range"], True)
shared_allergy = score_relevance(
    {"allergies": ["peanuts"], "dietary_pattern": "vegan", "goals": []},
    {"relevant_allergies": ["peanuts"], "dietary_pattern": "vegan"})
check("shared allergy is named in the reason",
      any("peanuts" in r for r in shared_allergy.reasons), True)

print("\n=== ordinal dimensions give partial credit to adjacent bands ===")
adjacent = score_relevance(
    {"activity_level": "very active", "dietary_pattern": "vegan", "goals": []},
    {"activity_level": "moderately active", "dietary_pattern": "vegan"})
far = score_relevance(
    {"activity_level": "very active", "dietary_pattern": "vegan", "goals": []},
    {"activity_level": "sedentary", "dietary_pattern": "vegan"})
check("adjacent activity bands beat distant ones", adjacent.score > far.score, True)

print("\n=== every age band the UI offers lands on the ordered scale ===")
# "65+" normalizes to "65+", which is not a member of the ordered list, so
# without an alias the dimension silently stopped voting rather than matching.
from app.community.similarity import _AGE_ORDER, _canon_age
from app.users.models import AGE_RANGES
for band in AGE_RANGES:
    check(f"age band {band!r} is placeable", _canon_age(band) in _AGE_ORDER, True)

older = score_relevance(
    {"age_range": "65+", "dietary_pattern": "vegan", "goals": [], "allergies": []},
    {"age_range": "65+", "dietary_pattern": "vegan", "relevant_allergies": []})
check("65+ matches 65+", any("65+" in r for r in older.reasons), True)
check("65+ is not silently uncomparable", "Age range" in older.uncomparable, False)

print("\n=== spam and bot patterns are caught ===")
for text, should_flag in [
    ("Bloating after about three weeks of daily use.", False),
    ("It was fine, no noticeable effect either way.", False),
    ("Buy now at www.example.com", True),
    ("Great! Use code SAVE50 for a discount", True),
    ("email me at spam@example.com", True),
    ("call me on +91 98765 43210", True),
    ("THIS PRODUCT IS ABSOLUTELY INCREDIBLE AND AMAZING", True),
    ("aaaaaaaaaaaaaaaaaaaa", True),
    ("good good good good good good good good good", True),
    ("", False),
    (None, False),
]:
    check(f"spam({text!r:44.44})", _looks_like_spam(text) is not None, should_flag)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
