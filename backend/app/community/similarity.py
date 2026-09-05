"""
Similar-user relevance for community experiences.

The requirement is explicit that the community section must not be a
"most liked reviews" list. It should answer a narrower question:

    Which of these experiences may be particularly relevant to THIS user?

Two rules shape everything here:

  1. Relevance is explained, never asserted as a bare percentage. Every score
     carries the dimensions that agreed and the ones that differ, so the reader
     can judge the match themselves.

  2. A similar profile is not a prediction. The caller renders these as
     "a user with a similar profile reported...", never as an outcome the
     reader should expect. Nothing in this module decides causation.

Scoring is deterministic — no model call — so the same pair always produces the
same explanation, and the reasoning can be shown in full.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.personalization.engine import _normalize, _resolve_goal_key


# Initial weighting from the requirements. Stated there as hypotheses to be
# validated against real feedback, so they live in one place and are passed
# through the scorer rather than being inlined.
#
# `health_context` is specified at 30% but the profile has nowhere to record it
# yet. It is kept here, unused, so the intended shape stays visible — dimensions
# with no data on either side never vote (see below), so its absence costs
# nothing today and adding the field later needs no change to the weighting.
SIMILARITY_WEIGHTS = {
    "health_context": 0.30,
    "allergy": 0.25,
    "dietary_pattern": 0.15,
    "goal": 0.10,
    "activity_level": 0.10,
    "age_range": 0.05,
    "usage_duration": 0.05,
}

# Below this, an experience is not offered as "relevant to you" at all. A weak
# match presented as a similar profile is worse than showing nothing, because it
# lends unearned authority to a stranger's anecdote.
RELEVANCE_THRESHOLD = 0.35

# Ordered coarsest-last: adjacent bands are partial credit, since 25-34 and
# 35-44 are more alike than 18-24 and 65+.
_AGE_ORDER = ["under 18", "18 24", "25 34", "35 44", "45 54", "55 64", "65 plus"]

# The open-ended top band is written "65+" in the UI, which normalization leaves
# as "65+" — not a member of the ordered list above. Without this it silently
# became uncomparable rather than matching, which is a hole that hides itself:
# the dimension just stops voting.
_AGE_ALIASES = {
    "65+": "65 plus",
    "65 and over": "65 plus",
    "over 65": "65 plus",
    "under18": "under 18",
    "<18": "under 18",
}


def _canon_age(value: str) -> str:
    """Map the age bands users actually see onto the ordered scale."""
    n = _normalize(value or "")
    return _AGE_ALIASES.get(n, n)

_ACTIVITY_ORDER = [
    "sedentary", "lightly active", "moderately active", "very active", "extremely active",
]

_DURATION_ORDER = [
    "once", "few days", "few weeks", "several months", "more than a year",
]


@dataclass
class DimensionMatch:
    """One comparable dimension, and how well it lined up."""
    dimension: str
    label: str              # human-readable, e.g. "Dietary pattern"
    score: float            # 0.0-1.0
    weight: float
    detail: str             # what actually matched, e.g. "both vegetarian"


@dataclass
class RelevanceResult:
    """Why an experience may — or may not — be relevant to a reader."""
    score: float                                    # 0.0-1.0 over comparable dimensions
    is_relevant: bool
    shared: list[DimensionMatch] = field(default_factory=list)
    differing: list[DimensionMatch] = field(default_factory=list)
    # Dimensions neither side disclosed. Surfaced so a thin match is visibly
    # thin rather than looking like a confident one.
    uncomparable: list[str] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        """"Why this experience may be relevant" — the agreeing dimensions."""
        return [m.detail for m in self.shared]

    @property
    def differences(self) -> list[str]:
        """Stated alongside the reasons, never omitted to make a match look better."""
        return [m.detail for m in self.differing]


def _ordinal_similarity(a: str, b: str, order: list[str]) -> Optional[float]:
    """
    Compare two values on an ordered scale.

    Adjacent bands score partial credit rather than zero — treating
    "moderately active" and "very active" as total strangers would discard a
    genuinely useful match.
    """
    na, nb = _normalize(a or ""), _normalize(b or "")
    if not na or not nb:
        return None
    if na not in order or nb not in order:
        return 1.0 if na == nb else None
    distance = abs(order.index(na) - order.index(nb))
    span = len(order) - 1
    if span <= 0:
        return 1.0
    return max(0.0, 1.0 - (distance / span))


def _list_overlap(a: Optional[list], b: Optional[list], resolve=None) -> Optional[float]:
    """
    Jaccard overlap between two sets of terms, or None when either is missing.

    An empty list on BOTH sides is a real agreement ("neither of us has any
    allergies") and scores 1.0; missing data on either side is not comparable.
    """
    if a is None or b is None:
        return None
    norm = (lambda v: resolve(_normalize(str(v)))) if resolve else (lambda v: _normalize(str(v)))
    sa = {norm(v) for v in a if str(v).strip()}
    sb = {norm(v) for v in b if str(v).strip()}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def score_relevance(
    reader: dict,
    context: Optional[dict],
    weights: Optional[dict] = None,
    threshold: float = RELEVANCE_THRESHOLD,
) -> RelevanceResult:
    """
    Score one shared context against the reader's profile.

    `context` is the anonymised context attached to a review, or None when the
    author did not consent to share any — in which case there is nothing to
    compare and the experience is community data only, never "relevant to you".

    Only dimensions BOTH sides disclosed are scored, and the weights are
    renormalised over those. A reader who shared nothing but their diet is not
    penalised for the dimensions they withheld; the match is simply reported as
    resting on less.
    """
    weights = weights or SIMILARITY_WEIGHTS
    result = RelevanceResult(score=0.0, is_relevant=False)

    if not context:
        result.uncomparable = ["No context was shared with this experience"]
        return result

    comparisons: list[DimensionMatch] = []

    def consider(dimension: str, label: str, score: Optional[float], detail_same: str, detail_diff: str):
        if score is None:
            result.uncomparable.append(label)
            return
        comparisons.append(DimensionMatch(
            dimension=dimension,
            label=label,
            score=score,
            weight=weights.get(dimension, 0.0),
            detail=detail_same if score >= 0.5 else detail_diff,
        ))

    # ── Allergies: the highest-weighted dimension we can actually compare ──
    # Someone who reacts to the same things is the most informative match.
    allergy_score = _list_overlap(reader.get("allergies"), context.get("relevant_allergies"))
    if allergy_score is not None:
        reader_allergies = [str(a) for a in (reader.get("allergies") or [])]
        shared_terms = sorted(
            {_normalize(a) for a in reader_allergies}
            & {_normalize(str(a)) for a in (context.get("relevant_allergies") or [])}
        )
        if shared_terms:
            detail_same = f"Shares your {', '.join(shared_terms)} sensitivity"
        else:
            detail_same = "Neither of you has declared allergies"
        consider("allergy", "Allergies", allergy_score,
                 detail_same, "Different allergy profile")
    else:
        result.uncomparable.append("Allergies")

    # ── Dietary pattern ──
    r_diet, c_diet = reader.get("dietary_pattern"), context.get("dietary_pattern")
    if r_diet and c_diet:
        same = _normalize(str(r_diet)) == _normalize(str(c_diet))
        consider("dietary_pattern", "Dietary pattern", 1.0 if same else 0.0,
                 f"Same dietary pattern ({_normalize(str(r_diet))})",
                 f"Different diet — they eat {_normalize(str(c_diet))}, you {_normalize(str(r_diet))}")
    else:
        result.uncomparable.append("Dietary pattern")

    # ── Goals: compared after alias resolution, so "bulking" matches "muscle gain" ──
    goal_score = _list_overlap(reader.get("goals"), context.get("goals"), resolve=_resolve_goal_key)
    if goal_score is not None and (reader.get("goals") or context.get("goals")):
        shared_goals = sorted(
            {_resolve_goal_key(_normalize(str(g))) for g in (reader.get("goals") or [])}
            & {_resolve_goal_key(_normalize(str(g))) for g in (context.get("goals") or [])}
        )
        consider("goal", "Goals", goal_score,
                 f"Shares your {', '.join(shared_goals)} goal" if shared_goals else "Similar goals",
                 "Working towards different goals")
    else:
        result.uncomparable.append("Goals")

    # ── Activity level ──
    activity = _ordinal_similarity(
        reader.get("activity_level") or "", context.get("activity_level") or "", _ACTIVITY_ORDER)
    if activity is not None:
        consider("activity_level", "Activity level", activity,
                 f"Similar activity level ({_normalize(str(context.get('activity_level')))})",
                 f"They are {_normalize(str(context.get('activity_level')))}, you are "
                 f"{_normalize(str(reader.get('activity_level')))}")
    else:
        result.uncomparable.append("Activity level")

    # ── Age range ──
    age = _ordinal_similarity(
        _canon_age(reader.get("age_range") or ""),
        _canon_age(context.get("age_range") or ""),
        _AGE_ORDER)
    if age is not None:
        consider("age_range", "Age range", age,
                 f"Similar age range ({context.get('age_range')})",
                 f"Different age range ({context.get('age_range')})")
    else:
        result.uncomparable.append("Age range")

    # ── Usage duration ──
    # A reader who has used something once learns most from another short user;
    # a months-long experience answers a different question.
    duration = _ordinal_similarity(
        reader.get("usage_duration") or "", context.get("usage_duration") or "", _DURATION_ORDER)
    if duration is not None:
        consider("usage_duration", "Usage duration", duration,
                 "Comparable length of use",
                 f"Used it for a different length of time ({_normalize(str(context.get('usage_duration')))})")
    else:
        result.uncomparable.append("Usage duration")

    if not comparisons:
        return result

    # Renormalise over what was actually comparable, so a partial profile yields
    # an honest score rather than one damped by everything left undisclosed.
    total_weight = sum(m.weight for m in comparisons)
    if total_weight <= 0:
        return result

    result.score = sum(m.score * m.weight for m in comparisons) / total_weight
    result.shared = [m for m in comparisons if m.score >= 0.5]
    result.differing = [m for m in comparisons if m.score < 0.5]
    # A single agreeing dimension is not a "similar profile", however well it
    # scores — requiring corroboration keeps one coincidence from carrying it.
    result.is_relevant = result.score >= threshold and len(result.shared) >= 2
    return result
