"""
Personalization Engine — The Core Brain of Arivio

This module calculates a Personal Suitability Score (0-100) for a product
based on a user's health profile (goals, allergies, preferences).

The scoring pipeline:
1.  Allergen Check (hard constraint) — disqualification or graded penalty
1b. Dietary Pattern (hard constraint) — composition the pattern excludes
2.  Goal Alignment (soft) — nutritional alignment with the user's goals
3.  Nutritional Quality Index — general nutritional quality assessment
4.  Ingredient Profile Score — quality of ingredient composition
5.  Nutrient Preferences (soft, or hard when marked strict) — the individual
    nutrients the user asked us to watch

Three kinds of constraint, kept distinct because they answer different
questions and a caller filtering on one must not silently inherit another:

    allergen_safe     — is this safe for me?
    diet_compatible   — does this fit what I eat?
    preference_*      — does this match what I asked for?
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ──────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────

@dataclass
class SuitabilityFlag:
    """A single flag raised by the engine."""
    flag_type: str        # "danger", "warning", "positive", "info"
    category: str         # "allergen", "diet", "nutrition", "ingredient", "goal", "preference"
    title: str
    description: str
    impact: int           # Score impact (negative = bad, positive = good)


@dataclass
class GoalAlignment:
    """How well a product aligns with a specific user goal."""
    goal: str
    alignment: str            # "excellent", "good", "neutral", "poor", "bad", "not_evaluated"
    score: Optional[int]      # 0-100, or None when the goal could not be scored
    reason: str
    matched_profile: bool = False   # True if a real goal profile drove the score


@dataclass
class SuitabilityResult:
    """Full suitability analysis result."""
    overall_score: int                          # 0-100
    verdict: str                                # Human-readable verdict
    confidence: int                             # 0-100 confidence in the score
    allergen_safe: bool                         # True if no allergen conflicts
    # False when the product contains something the user's dietary pattern
    # excludes. Kept separate from allergen_safe: one is a safety question, the
    # other a compatibility one, and conflating them would let a caller
    # filtering for allergen safety silently inherit dietary filtering too.
    diet_compatible: bool = True
    flags: list[SuitabilityFlag] = field(default_factory=list)
    goal_alignments: list[GoalAlignment] = field(default_factory=list)
    nutritional_quality_score: int = 50
    ingredient_profile_score: int = 50
    breakdown: dict = field(default_factory=dict)


# ──────────────────────────────────────────────
# Goal-specific Nutritional Thresholds
# ──────────────────────────────────────────────

# For each goal, define what constitutes "good" and "bad" nutritional values
# per 100g serving
GOAL_PROFILES = {
    "weight loss": {
        "label": "Weight Loss",
        "prefer_low": ["energy_kcal", "total_fat_g", "total_sugars_g", "saturated_fat_g"],
        "prefer_high": ["protein_g", "fiber_g"],
        "thresholds": {
            "energy_kcal": {"good": 200, "bad": 500},
            "total_sugars_g": {"good": 8, "bad": 25},
            "total_fat_g": {"good": 8, "bad": 25},
            "saturated_fat_g": {"good": 2, "bad": 10},
            "protein_g": {"good": 8, "bad": 2},
            "fiber_g": {"good": 3, "bad": 0.5},
        },
        "weights": {
            "energy_kcal": 3.0,
            "total_sugars_g": 1.0,
            "total_fat_g": 1.0,
            "saturated_fat_g": 1.0,
            "protein_g": 1.0,
            "fiber_g": 1.0
        }
    },
    "muscle gain": {
        "label": "Muscle Gain",
        "prefer_high": ["protein_g", "energy_kcal"],
        "prefer_low": ["total_sugars_g"],
        "thresholds": {
            "protein_g": {"good": 15, "bad": 3},
            "energy_kcal": {"good": 150, "bad": 30},
            "total_sugars_g": {"good": 8, "bad": 30},
        },
        "weights": {
            "protein_g": 3.0,
            "energy_kcal": 1.0,
            "total_sugars_g": 1.0
        }
    },
    "heart health": {
        "label": "Heart Health",
        "prefer_low": ["sodium_mg", "saturated_fat_g", "total_fat_g", "cholesterol_mg"],
        "prefer_high": ["fiber_g"],
        "thresholds": {
            "sodium_mg": {"good": 200, "bad": 800},
            "saturated_fat_g": {"good": 2, "bad": 6},
            "total_fat_g": {"good": 5, "bad": 20},
            "cholesterol_mg": {"good": 20, "bad": 100},
            "fiber_g": {"good": 3, "bad": 0.5},
        },
        "weights": {
            "sodium_mg": 2.0,
            "saturated_fat_g": 2.0,
            "total_fat_g": 1.0,
            "cholesterol_mg": 1.0,
            "fiber_g": 1.0
        }
    },
    "diabetes management": {
        "label": "Diabetes Management",
        "prefer_low": ["total_sugars_g", "total_carbohydrates_g"],
        "prefer_high": ["fiber_g", "protein_g"],
        "thresholds": {
            "total_sugars_g": {"good": 5, "bad": 20},
            "total_carbohydrates_g": {"good": 20, "bad": 60},
            "fiber_g": {"good": 3, "bad": 0.5},
            "protein_g": {"good": 8, "bad": 2},
        },
        "weights": {
            "total_sugars_g": 3.0,
            "total_carbohydrates_g": 2.0,
            "fiber_g": 1.0,
            "protein_g": 1.0
        }
    },
    "general health": {
        "label": "General Health",
        "prefer_low": ["total_sugars_g", "sodium_mg", "saturated_fat_g"],
        "prefer_high": ["protein_g", "fiber_g"],
        "thresholds": {
            "total_sugars_g": {"good": 8, "bad": 25},
            "sodium_mg": {"good": 300, "bad": 800},
            "saturated_fat_g": {"good": 3, "bad": 8},
            "protein_g": {"good": 6, "bad": 1},
            "fiber_g": {"good": 2, "bad": 0.3},
        },
        "weights": {
            "total_sugars_g": 1.0,
            "sodium_mg": 1.0,
            "saturated_fat_g": 1.0,
            "protein_g": 1.0,
            "fiber_g": 1.0
        }
    },
    "energy boost": {
        "label": "Energy Boost",
        "prefer_high": ["energy_kcal", "total_carbohydrates_g", "protein_g"],
        "prefer_low": ["saturated_fat_g"],
        "thresholds": {
            "energy_kcal": {"good": 200, "bad": 30},
            "total_carbohydrates_g": {"good": 25, "bad": 5},
            "protein_g": {"good": 6, "bad": 1},
            "saturated_fat_g": {"good": 3, "bad": 10},
        },
        "weights": {
            "energy_kcal": 2.0,
            "total_carbohydrates_g": 2.0,
            "protein_g": 1.0,
            "saturated_fat_g": 1.0
        }
    },
}

# ──────────────────────────────────────────────
# Nutrient Preferences (soft constraints)
# ──────────────────────────────────────────────
#
# A preference sits between a goal and a hard constraint. A goal is a whole
# nutritional profile ("heart health" also weighs saturated fat, total fat and
# cholesterol); a preference is one nutrient the user asked us to watch. Without
# these, the only way to say "I want low sodium" was to adopt a goal that drags
# in four other thresholds.
#
# Thresholds follow front-of-pack traffic-light bands per 100g, which is the
# basis the rest of the engine already works on.
PREFERENCE_PROFILES = {
    "low_sugar": {
        "label": "Low sugar", "nutrient": "total_sugars_g",
        "direction": "low", "good": 5.0, "bad": 22.5, "unit": "g",
    },
    "low_sodium": {
        "label": "Low sodium", "nutrient": "sodium_mg",
        "direction": "low", "good": 120.0, "bad": 600.0, "unit": "mg",
    },
    "low_fat": {
        "label": "Low fat", "nutrient": "total_fat_g",
        "direction": "low", "good": 3.0, "bad": 17.5, "unit": "g",
    },
    "low_saturated_fat": {
        "label": "Low saturated fat", "nutrient": "saturated_fat_g",
        "direction": "low", "good": 1.5, "bad": 5.0, "unit": "g",
    },
    "high_protein": {
        "label": "High protein", "nutrient": "protein_g",
        "direction": "high", "good": 15.0, "bad": 3.0, "unit": "g",
    },
    "high_fiber": {
        "label": "High fibre", "nutrient": "fiber_g",
        "direction": "high", "good": 6.0, "bad": 1.5, "unit": "g",
    },
}

# Common phrasings for the canonical keys above.
PREFERENCE_ALIASES = {
    "low sugar": "low_sugar", "less sugar": "low_sugar", "sugar free": "low_sugar",
    "low salt": "low_sodium", "low sodium": "low_sodium",
    "low fat": "low_fat",
    "low saturated fat": "low_saturated_fat", "low sat fat": "low_saturated_fat",
    "high protein": "high_protein", "more protein": "high_protein",
    "high fibre": "high_fiber", "high fiber": "high_fiber",
}

# How far a satisfied or violated preference can move the score. Deliberately
# modest: a preference is a nudge, not a verdict, and several of them must not
# be able to swamp the nutritional assessment underneath.
_PREFERENCE_BONUS = 6      # fully satisfied
_PREFERENCE_PENALTY = -8   # fully violated — asymmetric, because failing what
                           # the user explicitly asked for matters more than
                           # meeting it
_PREFERENCE_TOTAL_CAP = 15

# A preference the user marked strict is a hard constraint, but a self-imposed
# one — kept clearly above the allergen and dietary caps so the three remain
# distinguishable by score alone.
_STRICT_PREFERENCE_CAP = 25


def _resolve_preference_key(preference_type: str) -> str:
    """Resolve a stored preference onto a canonical PREFERENCE_PROFILES key."""
    n = _normalize(preference_type).replace(" ", "_")
    if n in PREFERENCE_PROFILES:
        return n
    return PREFERENCE_ALIASES.get(_normalize(preference_type), n)


# Maps common goal phrasings onto the canonical GOAL_PROFILES keys so that
# free-text goals like "muscle_building" or "weight management" still resolve
# to a hardcoded profile instead of silently scoring neutral.
GOAL_ALIASES = {
    # weight loss
    "weight management": "weight loss",
    "weight control": "weight loss",
    "lose weight": "weight loss",
    "fat loss": "weight loss",
    "slimming": "weight loss",
    "cutting": "weight loss",
    # muscle gain
    "muscle building": "muscle gain",
    "build muscle": "muscle gain",
    "muscle growth": "muscle gain",
    "bulking": "muscle gain",
    "strength building": "muscle gain",
    "gain muscle": "muscle gain",
    # heart health
    "cardiovascular health": "heart health",
    "cardiac health": "heart health",
    "cholesterol management": "heart health",
    "blood pressure management": "heart health",
    "lower cholesterol": "heart health",
    # diabetes management
    "diabetes": "diabetes management",
    "blood sugar management": "diabetes management",
    "blood sugar control": "diabetes management",
    "glucose management": "diabetes management",
    "prediabetes": "diabetes management",
    # general health
    "general wellness": "general health",
    "overall health": "general health",
    "healthy eating": "general health",
    "balanced diet": "general health",
    "wellbeing": "general health",
    # energy boost
    "energy": "energy boost",
    "more energy": "energy boost",
    "endurance": "energy boost",
    "athletic performance": "energy boost",
}

# Synonym groups: every term below is another NAME FOR THE SAME allergen, so
# declaring any one of them should match all of them. Someone allergic to milk
# reacts to casein and whey too.
ALLERGEN_SYNONYMS = {
    "milk": ["milk", "dairy", "lactose", "casein", "whey", "cream", "butter", "cheese", "ghee", "paneer", "curd"],
    "peanuts": ["peanut", "peanuts", "groundnut", "groundnuts", "arachis"],
    "soy": ["soy", "soya", "soybean", "soybeans", "soy lecithin"],
    "eggs": ["egg", "eggs", "albumin", "globulin", "lysozyme", "mayonnaise"],
    "sesame": ["sesame", "tahini", "til"],
    "mustard": ["mustard"],
    "celery": ["celery"],
    "sulphites": ["sulphite", "sulfite", "sulphur dioxide", "so2"],
}

# Category groups: the members are DISTINCT allergens sharing an umbrella term.
# Declaring the umbrella ("tree nuts") matches every member; declaring a single
# member ("cashew") matches only that member. A cashew allergy does not imply
# an almond allergy, and a wheat allergy does not imply a rye allergy.
ALLERGEN_CATEGORIES = {
    "tree nuts": ["almond", "cashew", "walnut", "pistachio", "hazelnut", "pecan", "macadamia", "brazil nut"],
    "gluten": ["gluten", "wheat", "barley", "rye", "oats", "spelt", "semolina", "maida"],
    "fish": ["fish", "cod", "salmon", "tuna", "anchovy", "sardine"],
    "shellfish": ["shrimp", "prawn", "crab", "lobster", "clam", "mussel", "oyster", "scallop", "shellfish"],
}

# Extra ways users refer to a whole category.
ALLERGEN_CATEGORY_ALIASES = {
    "tree nut": "tree nuts",
    "treenuts": "tree nuts",
    "nuts": "tree nuts",
    "nut": "tree nuts",
    "seafood": "shellfish",
    "crustaceans": "shellfish",
    "molluscs": "shellfish",
    "mollusks": "shellfish",
}

# Certainty strings that mean "definitely present" vs. "trace / may contain"
_CONFIRMED_CERTAINTY = {"confirmed", "declared", "high", "certain", "definite"}


# ──────────────────────────────────────────────
# Dietary Pattern Compatibility
# ──────────────────────────────────────────────
#
# A dietary pattern is a HARD constraint, not a preference: a vegan product
# containing gelatin is not "slightly less suitable", it does not qualify. This
# is kept separate from the allergen check because it is a compatibility
# question rather than a safety one, and the two are reported separately.

_MEAT_TERMS = [
    "beef", "pork", "chicken", "mutton", "lamb", "turkey", "duck", "veal",
    "venison", "bacon", "ham", "sausage", "salami", "pepperoni", "prosciutto",
    "chorizo", "poultry", "liver", "tripe", "goat meat", "meat extract",
    "chicken fat", "bone broth", "keema",
]

# Definitely animal-derived, but not muscle meat — these rule a product out for
# every vegetarian pattern, and are the ones most often missed on a label.
_ANIMAL_DERIVED_TERMS = [
    "gelatin", "gelatine", "lard", "tallow", "suet", "animal fat", "bone char",
    "isinglass", "carmine", "cochineal", "e120", "shellac", "e904",
    "cod liver oil", "e441", "animal rennet",
]

_BEE_TERMS = ["honey", "beeswax", "e901", "royal jelly", "propolis"]

# Genuinely ambiguous: commonly plant-derived, sometimes not. These raise a
# "check the label" flag and deliberately do NOT change the score — capping a
# product because it might contain animal-derived glycerin would be a guess
# presented as a finding.
_UNCERTAIN_ORIGIN_TERMS = [
    "mono and diglycerides", "monoglycerides", "diglycerides", "e471",
    "glycerol", "glycerin", "glycerine", "e422", "collagen", "rennet",
    "lipase", "pepsin", "stearic acid", "magnesium stearate", "e470",
    "vitamin d3", "l cysteine", "e920",
]

# "meat" is qualifiable the same way the dairy words are — "coconut meat" is a
# plant, and flagging it would rule out most coconut products for vegetarians.
_QUALIFIABLE_MEAT = {"meat"}

# What each pattern rules out. Keys are normalized DietaryPattern values.
#
# Note the vegetarian/eggetarian split follows Indian usage, which is what this
# catalogue is built around (the ingredient tables carry ghee, paneer, maida,
# vanaspati): "vegetarian" excludes eggs, and "eggetarian" is the pattern that
# permits them. Western lacto-ovo vegetarians should select eggetarian.
#
# "keto" is deliberately absent: it is a macronutrient target, not an
# ingredient-exclusion list, and belongs in goal alignment where carbohydrate
# thresholds already live. Treating it here would rule out foods on the wrong
# basis entirely.
DIET_EXCLUSIONS = {
    "vegan": ["meat", "fish", "shellfish", "dairy", "egg", "animal_derived", "bee"],
    "vegetarian": ["meat", "fish", "shellfish", "egg", "animal_derived"],
    "eggetarian": ["meat", "fish", "shellfish", "animal_derived"],
    "pescatarian": ["meat", "animal_derived"],
}

_DIET_GROUP_LABELS = {
    "meat": "meat",
    "fish": "fish",
    "shellfish": "shellfish",
    "dairy": "dairy",
    "egg": "egg",
    "animal_derived": "animal-derived ingredients",
    "bee": "bee products",
}


# ──────────────────────────────────────────────
# Engine Functions
# ──────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for comparison."""
    return text.strip().lower().replace("-", " ").replace("_", " ")


def _resolve_goal_key(goal_type: str) -> str:
    """
    Resolve a free-text goal onto a canonical GOAL_PROFILES key.
    Falls back to the normalized string if there is no known alias.
    """
    n = _normalize(goal_type)
    if n in GOAL_PROFILES:
        return n
    return GOAL_ALIASES.get(n, n)


# Words that name a dairy product on their own but, when preceded by a plant
# source, name something with no dairy in it at all. Without this, "butter"
# matched "peanut butter", "cocoa butter" and "shea butter", and "milk" matched
# every plant milk — each one hard-capping the score at 15 and telling a
# milk-allergic user that almond milk contains milk.
_QUALIFIABLE_DAIRY = {"butter", "milk", "cream", "cheese", "yoghurt", "yogurt"}

_PLANT_QUALIFIERS = {
    "peanut", "peanuts", "cocoa", "cacao", "shea", "almond", "almonds",
    "coconut", "soy", "soya", "oat", "oats", "rice", "cashew", "cashews",
    "hazelnut", "walnut", "sunflower", "sesame", "hemp", "pea", "macadamia",
    "pistachio", "apple", "nut", "seed", "plant", "vegan", "vegetable",
}


def _dairy_term_hit(text_norm: str, term: str) -> bool:
    """
    Whether a qualifiable dairy word appears in its dairy sense.

    An occurrence preceded by a plant source ("almond milk", "cocoa butter") is
    not dairy and does not count. A bare or compound occurrence still does, so
    "buttermilk", "milk solids" and "butter oil" are unaffected.
    """
    pattern = rf"(?:(\w+)[\s\-]+)?{re.escape(term)}(?:s|es)?\b"
    for match in re.finditer(pattern, text_norm):
        preceding = match.group(1)
        if preceding is None or preceding not in _PLANT_QUALIFIERS:
            return True
    return False


def _text_contains_allergen(text_norm: str, synonyms: set[str]) -> bool:
    """
    Check whether a normalized text mentions any of the allergen synonyms.

    Short tokens (< 5 chars, e.g. "egg", "soy", "cod") are matched on word
    boundaries only, so "egg" no longer matches "eggplant" and "cod" no longer
    matches "cocoa". Longer synonyms still use substring matching so compound
    words like "buttermilk" (via "butter") are caught — except for the dairy
    words above, which are checked for a plant qualifier first.
    """
    for syn in synonyms:
        syn = syn.strip()
        if not syn:
            continue
        if syn in _QUALIFIABLE_DAIRY:
            if _dairy_term_hit(text_norm, syn):
                return True
        elif len(syn) >= 5:
            if syn in text_norm:
                return True
        else:
            if re.search(rf"\b{re.escape(syn)}(?:s|es)?\b", text_norm):
                return True
    return False


# Words that carry no allergen meaning on their own, so they never trigger a
# group lookup when a typed phrase is broken into tokens.
_ALLERGEN_STOPWORDS = {
    "and", "or", "of", "the", "a", "an", "s", "free", "allergy", "allergic",
    "intolerance", "intolerant", "sensitivity", "products", "product",
}


def known_allergen_suggestions() -> list[str]:
    """
    Canonical allergen names to offer as autocomplete suggestions.

    Steering users onto these avoids the free-text phrasings that only resolve
    through the weaker token fallback.
    """
    names = set(ALLERGEN_SYNONYMS.keys())
    names |= set(ALLERGEN_CATEGORIES.keys())
    for members in ALLERGEN_CATEGORIES.values():
        names |= {_normalize(m) for m in members}
    return sorted(n.replace("_", " ") for n in names)


def _lookup_allergen_group(term: str) -> tuple[set[str], bool]:
    """Match one exact term against the synonym and category tables."""
    terms: set[str] = set()
    recognized = False

    # Synonym groups — naming any member pulls in the whole group.
    for key, values in ALLERGEN_SYNONYMS.items():
        norm_values = [_normalize(v) for v in values]
        if term == _normalize(key) or term in norm_values:
            terms.update(norm_values)
            recognized = True

    # Category groups — only the umbrella term pulls in the whole group.
    category = ALLERGEN_CATEGORY_ALIASES.get(term, term)
    if category in ALLERGEN_CATEGORIES:
        terms.update(_normalize(v) for v in ALLERGEN_CATEGORIES[category])
        terms.add(category)
        recognized = True
    else:
        # Naming a single member matches that member alone.
        for members in ALLERGEN_CATEGORIES.values():
            if term in [_normalize(v) for v in members]:
                terms.add(term)
                recognized = True
                break

    return terms, recognized


def _build_allergen_synonyms(user_allergen: str) -> tuple[set[str], bool]:
    """
    Expand a user's declared allergen into the set of terms to check against.

    Returns (terms, recognized). `recognized` is False when the allergen is not
    present in any known synonym or category group — in that case matching falls
    back to the literal name only, and coverage is necessarily partial.
    """
    allergen_lower = _normalize(user_allergen)
    if not allergen_lower:
        return set(), False

    terms = {allergen_lower}

    # 1. Exact match on the whole phrase — the most precise reading.
    exact, recognized = _lookup_allergen_group(allergen_lower)
    terms |= exact

    # 2. Otherwise fall back to matching tokens within the phrase, so natural
    #    input like "Sesame Seeds" or "Cow's Milk" still reaches its synonym
    #    group instead of silently degrading to literal-only matching (which
    #    would miss "tahini" and "whey").
    #
    #    Bigrams are tried before single words so a specific member ("brazil
    #    nut") wins over its category ("nut"), and the FIRST hit stops the
    #    search so "peanut butter" resolves to peanuts rather than also
    #    dragging in the dairy group via "butter".
    if not recognized:
        words = [
            w for w in re.split(r"[^a-z0-9]+", allergen_lower)
            if w and w not in _ALLERGEN_STOPWORDS
        ]
        bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
        for candidate in bigrams + words:
            found, ok = _lookup_allergen_group(candidate)
            if ok:
                terms |= found
                terms.add(candidate)
                recognized = True
                break

    return {t for t in terms if t}, recognized


def _literal_allergen_hit(
    synonyms: set[str],
    product_allergens: list[dict],
    product_ingredients: list[dict],
) -> tuple[bool, bool]:
    """Return (confirmed, trace) from string matching alone."""
    confirmed = False
    trace = False

    for pa in product_allergens:
        pa_name = _normalize(pa.get("allergen", ""))
        if pa_name and _text_contains_allergen(pa_name, synonyms):
            certainty = _normalize(pa.get("certainty", "possible"))
            if certainty in _CONFIRMED_CERTAINTY:
                confirmed = True
            else:
                trace = True

    # A named ingredient means the allergen is definitely present.
    for ing in product_ingredients:
        ing_name = _normalize(ing.get("name", ""))
        if ing_name and _text_contains_allergen(ing_name, synonyms):
            confirmed = True

    return confirmed, trace


def find_unresolved_allergens(
    user_allergies: list[dict],
    product_allergens: list[dict],
    product_ingredients: list[dict],
) -> list[str]:
    """
    User allergens that string matching cannot settle for this product.

    These are the ones with no known synonym group AND no literal hit, so a
    clean result proves nothing — the right candidates for AI inference against
    the ingredient list. Allergens we have synonyms for are deliberately
    excluded: the tables already cover them and an LLM call would add cost and
    risk without adding information.
    """
    unresolved = []
    for allergy in user_allergies:
        name = allergy.get("allergen", "")
        if not name:
            continue
        synonyms, recognized = _build_allergen_synonyms(name)
        if recognized:
            continue
        confirmed, trace = _literal_allergen_hit(synonyms, product_allergens, product_ingredients)
        if not confirmed and not trace:
            unresolved.append(name)
    return unresolved


def _check_allergen_match(
    user_allergen: str,
    allergy_type: str,
    severity: Optional[str],
    product_allergens: list[dict],
    product_ingredients: list[dict],
    inferred: Optional[dict] = None,
) -> tuple[list[SuitabilityFlag], str, bool]:
    """
    Check whether a user's declared allergen matches any product allergen or ingredient.

    Returns (flags, conflict_level, recognized) where conflict_level is one of:
        "none"        — no match
        "preference"  — match, but the entry is a soft preference (not a safety issue)
        "trace"       — "may contain" / possible cross-contamination
        "confirmed"   — the product definitely contains the allergen

    `recognized` reports whether the allergen was found in a known synonym or
    category group, so the caller can disclose partial coverage.
    """
    synonyms, recognized = _build_allergen_synonyms(user_allergen)
    is_preference = _normalize(allergy_type or "allergy") == "preference"

    confirmed = False
    trace = False

    confirmed, trace = _literal_allergen_hit(synonyms, product_allergens, product_ingredients)

    # AI inference, consulted only when string matching found nothing. It can
    # ADD a conflict but never clear one — a literal or synonym hit always
    # stands, so a model mistake can't make an unsafe product look safe.
    ai_match = None
    if not confirmed and not trace and inferred and inferred.get("found"):
        ai_match = inferred
        if _normalize(str(inferred.get("certainty", "medium"))) == "high":
            confirmed = True
        else:
            trace = True

    level = "none"
    if confirmed:
        level = "preference" if is_preference else "confirmed"
    elif trace:
        level = "preference" if is_preference else "trace"

    flags: list[SuitabilityFlag] = []
    label = allergy_type or "allergy"

    # AI-derived findings are attributed, so the user can weigh them against a
    # declared-label match and check for themselves.
    if ai_match:
        via = ai_match.get("matched_ingredient")
        detail = f" We matched the ingredient '{via}'." if via else ""
        reason = str(ai_match.get("reason") or "").strip()
        if reason:
            detail += f" {reason}"
        suffix = f" (identified by AI ingredient analysis, not a declared label.){detail}"
    else:
        suffix = ""

    if level == "confirmed":
        flags.append(SuitabilityFlag(
            flag_type="danger",
            category="allergen",
            title=f"Contains {user_allergen}",
            description=(
                f"This product contains {user_allergen}, which is on your {label} list"
                + (f" (severity: {severity})" if severity else "")
                + "." + suffix
            ),
            impact=-50,
        ))
    elif level == "trace":
        flags.append(SuitabilityFlag(
            flag_type="warning",
            category="allergen",
            title=f"May contain {user_allergen}",
            description=(
                f"This product may contain {user_allergen}, which is on your {label} list."
                + suffix
            ),
            impact=-25,
        ))
    elif level == "preference":
        flags.append(SuitabilityFlag(
            flag_type="warning",
            category="allergen",
            title=f"Contains {user_allergen}",
            description=(
                f"This product contains {user_allergen}, which you prefer to avoid." + suffix
            ),
            impact=-15,
        ))

    return flags, level, recognized


def _diet_group_terms(group: str) -> set[str]:
    """
    The terms that identify one excluded group.

    Dairy, egg, fish and shellfish reuse the allergen tables rather than
    duplicating them — the same words identify the same foods, and a term added
    for allergen coverage should improve diet matching for free.
    """
    if group == "dairy":
        return {_normalize(v) for v in ALLERGEN_SYNONYMS["milk"]}
    if group == "egg":
        return {_normalize(v) for v in ALLERGEN_SYNONYMS["eggs"]}
    if group in ("fish", "shellfish"):
        return {_normalize(v) for v in ALLERGEN_CATEGORIES[group]}
    if group == "meat":
        return {_normalize(v) for v in _MEAT_TERMS}
    if group == "animal_derived":
        return {_normalize(v) for v in _ANIMAL_DERIVED_TERMS}
    if group == "bee":
        return {_normalize(v) for v in _BEE_TERMS}
    return set()


def _meat_term_hit(text_norm: str, term: str) -> bool:
    """"coconut meat" is a plant; a bare or compound "meat" is not."""
    return _dairy_term_hit(text_norm, term)


def _check_diet_compatibility(
    dietary_pattern: Optional[str],
    product_ingredients: list[dict],
    product_allergens: list[dict],
) -> tuple[list[SuitabilityFlag], str, list[str]]:
    """
    Check a product against the user's dietary pattern.

    Returns (flags, level, uncertain_terms) where level is one of:
        "none"          — nothing incompatible found
        "uncertain"     — an ambiguous ingredient that MAY be animal-derived
        "incompatible"  — an ingredient this pattern excludes

    "uncertain" never changes the score. The ingredient is genuinely ambiguous,
    and a guess that lowers a score reads as a finding.
    """
    flags: list[SuitabilityFlag] = []
    pattern = _normalize(dietary_pattern or "")
    excluded_groups = DIET_EXCLUSIONS.get(pattern)
    if not excluded_groups:
        # omnivore, keto, other, or nothing declared — no ingredient exclusions.
        return flags, "none", []

    ingredient_names = [
        _normalize(str(i.get("name", ""))) for i in product_ingredients
        if str(i.get("name", "")).strip()
    ]
    # Declared allergens carry the same information for dairy/egg/fish and are
    # often present when the ingredient list is not.
    declared = [
        _normalize(str(a.get("allergen", ""))) for a in product_allergens
        if str(a.get("allergen", "")).strip()
    ]
    haystack = ingredient_names + declared

    found: dict[str, str] = {}   # group -> the ingredient that matched
    for group in excluded_groups:
        terms = _diet_group_terms(group)
        for text in haystack:
            for term in terms:
                if term in _QUALIFIABLE_DAIRY:
                    hit = _dairy_term_hit(text, term)
                elif term in _QUALIFIABLE_MEAT:
                    hit = _meat_term_hit(text, term)
                elif len(term) >= 5:
                    hit = term in text
                else:
                    hit = bool(re.search(rf"\b{re.escape(term)}(?:s|es)?\b", text))
                if hit:
                    found.setdefault(group, text)
                    break
            if group in found:
                break

    pattern_label = pattern.capitalize()

    if found:
        groups = ", ".join(_DIET_GROUP_LABELS.get(g, g) for g in found)
        examples = ", ".join(sorted({v for v in found.values()})[:3])
        flags.append(SuitabilityFlag(
            flag_type="danger",
            category="diet",
            title=f"Not {pattern_label}",
            description=(
                f"This product contains {groups}, which your {pattern_label} "
                f"dietary pattern excludes. Identified from: {examples}."
            ),
            impact=-50,
        ))
        return flags, "incompatible", []

    # Only worth raising when nothing definitive was found — an ambiguous
    # ingredient adds nothing once the product is already ruled out.
    uncertain = []
    for text in haystack:
        for term in _UNCERTAIN_ORIGIN_TERMS:
            if term in text:
                uncertain.append(text)
                break

    if uncertain:
        names = ", ".join(sorted(set(uncertain))[:3])
        flags.append(SuitabilityFlag(
            flag_type="warning",
            category="diet",
            title="May not be " + pattern_label,
            description=(
                f"Contains {names}, which can be either plant- or animal-derived. "
                "The label doesn't say which, so check the packaging or the "
                "manufacturer if this matters to you."
            ),
            impact=0,
        ))
        return flags, "uncertain", sorted(set(uncertain))

    return flags, "none", []


def _evaluate_goal_alignment(
    goal_type: str,
    nutrition: Optional[dict],
    custom_profiles: dict = None,
    status_message: Optional[str] = None,
) -> GoalAlignment:
    """
    Evaluate how well a product's nutrition aligns with a specific health goal.

    A goal that cannot be scored returns score=None and alignment="not_evaluated"
    rather than a neutral 50 — a placeholder number reads as a real verdict and
    contradicts the accompanying "not scored" messaging.

    `status_message` is the explanation already shown to the user on their
    profile (e.g. why the goal was judged unsupported), so the same wording
    appears here instead of a second, different phrasing.
    """
    profile = None
    norm = _normalize(goal_type)
    resolved = _resolve_goal_key(goal_type)

    if custom_profiles:
        if norm in custom_profiles:
            profile = custom_profiles[norm]
        elif resolved in custom_profiles:
            profile = custom_profiles[resolved]

    if profile is None:
        profile = GOAL_PROFILES.get(resolved)

    if not profile:
        return GoalAlignment(
            goal=goal_type,
            alignment="not_evaluated",
            score=None,
            reason=status_message or (
                "We don't have a scoring profile for this goal, so it wasn't "
                "applied to this product's score."
            ),
            matched_profile=False,
        )

    if not nutrition:
        return GoalAlignment(
            goal=profile.get("label", goal_type),
            alignment="not_evaluated",
            score=None,
            reason="This product doesn't have enough nutrition data to score this goal.",
            matched_profile=False,
        )

    weights = profile.get("weights", {}) or {}
    thresholds = profile.get("thresholds", {}) or {}
    prefer_low = profile.get("prefer_low", []) or []
    prefer_high = profile.get("prefer_high", []) or []

    scores = []
    reasons = []

    for nutrient, limits in thresholds.items():
        # Skip nutrients that have no declared direction — otherwise they would
        # contribute a meaningless 0 and drag the average down.
        if nutrient not in prefer_low and nutrient not in prefer_high:
            continue

        if not isinstance(limits, dict) or "good" not in limits or "bad" not in limits:
            continue

        value = nutrition.get(nutrient)
        if value is None:
            continue

        good = limits["good"]
        bad = limits["bad"]
        if good == bad:
            continue

        score_val = 0
        pretty = nutrient.replace("_", " ").title()

        if nutrient in prefer_low:
            if value <= good:
                score_val = 100
                reasons.append(f"✓ {pretty}: {value} (excellent)")
            elif value >= bad:
                score_val = 0
                reasons.append(f"✗ {pretty}: {value} (too high)")
            else:
                ratio = (value - good) / (bad - good)
                score_val = int((1 - ratio) * 100)
                reasons.append(f"~ {pretty}: {value} (moderate)")
        else:  # prefer_high
            if value >= good:
                score_val = 100
                reasons.append(f"✓ {pretty}: {value} (excellent)")
            elif value <= bad:
                score_val = 0
                reasons.append(f"✗ {pretty}: {value} (too low)")
            else:
                ratio = (value - bad) / (good - bad)
                score_val = int(ratio * 100)
                reasons.append(f"~ {pretty}: {value} (moderate)")

        score_val = max(0, min(100, score_val))
        scores.append((nutrient, score_val))

    if not scores:
        return GoalAlignment(
            goal=profile.get("label", goal_type),
            alignment="not_evaluated",
            score=None,
            reason="None of this product's nutrition facts apply to this goal.",
            matched_profile=False,
        )

    total_weight = 0.0
    weighted_sum = 0.0
    for nutrient, score_val in scores:
        w = float(weights.get(nutrient, 1.0))
        weighted_sum += score_val * w
        total_weight += w

    avg = int(weighted_sum / total_weight) if total_weight else 50

    if avg >= 80:
        alignment = "excellent"
    elif avg >= 60:
        alignment = "good"
    elif avg >= 40:
        alignment = "neutral"
    elif avg >= 20:
        alignment = "poor"
    else:
        alignment = "bad"

    top_reasons = reasons[:3] if reasons else ["General alignment computed from nutritional data."]
    return GoalAlignment(
        goal=profile.get("label", goal_type),
        alignment=alignment,
        score=avg,
        reason=" | ".join(top_reasons),
        matched_profile=True,
    )


def _fmt(value) -> str:
    """
    Render a nutrient value for display.

    Source data carries full float precision — a unit conversion turns a tidy
    label figure into "55.4545454545455g", which reads as false precision on a
    number that came off a packet. One decimal place is as much as any label
    justifies, and a whole number stays whole.
    """
    try:
        rounded = round(float(value), 1)
    except (TypeError, ValueError):
        return str(value)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _evaluate_preferences(
    user_preferences: list[dict],
    nutrition: Optional[dict],
) -> tuple[list[SuitabilityFlag], int, str]:
    """
    Score the product against the nutrients the user asked us to watch.

    Returns (flags, adjustment, level) where `adjustment` is the net points to
    apply and `level` is:
        "none"   — no preference applied, or all satisfied
        "soft"   — at least one preference not met; the score is nudged down
        "strict" — a preference marked as a hard constraint was violated

    A preference is a nudge, never a block, unless the user marked it strict.
    Preferences we cannot measure — the nutrient is absent from the label — are
    skipped silently rather than counted as met, which would reward missing data.
    """
    flags: list[SuitabilityFlag] = []
    if not user_preferences or not nutrition:
        return flags, 0, "none"

    total = 0
    level = "none"
    seen: set[str] = set()

    for pref in user_preferences:
        key = _resolve_preference_key(str(pref.get("preference_type", "")))
        profile = PREFERENCE_PROFILES.get(key)
        if not profile or key in seen:
            continue
        seen.add(key)

        value = nutrition.get(profile["nutrient"])
        if value is None:
            continue

        good, bad = profile["good"], profile["bad"]
        if profile["direction"] == "low":
            if value <= good:
                ratio = 1.0
            elif value >= bad:
                ratio = 0.0
            else:
                ratio = 1.0 - (value - good) / (bad - good)
        else:
            if value >= good:
                ratio = 1.0
            elif value <= bad:
                ratio = 0.0
            else:
                ratio = (value - bad) / (good - bad)

        # ratio 1.0 -> full bonus, 0.0 -> full penalty, linear between.
        impact = int(round(_PREFERENCE_PENALTY + ratio * (_PREFERENCE_BONUS - _PREFERENCE_PENALTY)))
        total += impact

        is_strict = bool(pref.get("is_hard_constraint"))
        shown = _fmt(value) + profile["unit"]
        label = profile["label"]

        if ratio >= 0.75:
            flags.append(SuitabilityFlag(
                flag_type="positive", category="preference",
                title=f"Meets your {label.lower()} preference",
                description=f"{profile['nutrient'].replace('_', ' ').title()}: {shown} per 100g.",
                impact=impact,
            ))
        elif ratio <= 0.25 and is_strict:
            level = "strict"
            flags.append(SuitabilityFlag(
                flag_type="danger", category="preference",
                title=f"Fails your strict {label.lower()} requirement",
                description=(
                    f"{profile['nutrient'].replace('_', ' ').title()}: {shown} per 100g. "
                    "You marked this preference as strict, so this product does not qualify."
                ),
                impact=impact,
            ))
        else:
            if level != "strict":
                level = "soft"
            flags.append(SuitabilityFlag(
                flag_type="warning", category="preference",
                title=f"Against your {label.lower()} preference",
                description=f"{profile['nutrient'].replace('_', ' ').title()}: {shown} per 100g.",
                impact=impact,
            ))

    total = max(-_PREFERENCE_TOTAL_CAP, min(_PREFERENCE_TOTAL_CAP, total))
    return flags, total, level


def _calculate_nutritional_quality(nutrition: Optional[dict]) -> tuple[int, list[SuitabilityFlag]]:
    """
    Calculate an overall nutritional quality score (0-100) independent of user goals.
    Based on general dietary guidelines.

    Uses an additive scoring system starting from a base of 60 (neutral-positive)
    to avoid artificially capping scores for genuinely healthy products.
    """
    if not nutrition:
        return 50, [SuitabilityFlag(
            flag_type="info",
            category="nutrition",
            title="Limited nutritional data",
            description="Nutritional information is incomplete for this product.",
            impact=0,
        )]

    flags = []
    score = 60  # Start at a neutral-positive baseline

    # ── Sugar Assessment ──
    sugar = nutrition.get("total_sugars_g")
    if sugar is not None:
        if sugar > 22.5:
            score -= 18
            flags.append(SuitabilityFlag("warning", "nutrition", "Very high sugar",
                f"Contains {_fmt(sugar)}g of sugar per 100g (high is >22.5g).", -18))
        elif sugar > 15:
            score -= 10
            flags.append(SuitabilityFlag("warning", "nutrition", "High sugar",
                f"Contains {_fmt(sugar)}g of sugar per 100g.", -10))
        elif sugar > 11.25:
            score -= 5
            flags.append(SuitabilityFlag("info", "nutrition", "Moderate sugar",
                f"Contains {_fmt(sugar)}g of sugar per 100g.", -5))
        elif sugar <= 5:
            score += 12
            flags.append(SuitabilityFlag("positive", "nutrition", "Low sugar",
                f"Only {_fmt(sugar)}g of sugar per 100g.", 12))
        else:
            score += 5  # Acceptable sugar level

    # ── Sodium Assessment ──
    sodium = nutrition.get("sodium_mg")
    if sodium is not None:
        if sodium > 600:
            score -= 12
            flags.append(SuitabilityFlag("warning", "nutrition", "High sodium",
                f"Contains {_fmt(sodium)}mg of sodium per 100g (high is >600mg).", -12))
        elif sodium > 400:
            score -= 5
        elif sodium <= 200:
            score += 10
            flags.append(SuitabilityFlag("positive", "nutrition", "Low sodium",
                f"Only {_fmt(sodium)}mg of sodium per 100g.", 10))
        else:
            score += 3  # Acceptable sodium level

    # ── Saturated Fat Assessment ──
    sat_fat = nutrition.get("saturated_fat_g")
    if sat_fat is not None:
        if sat_fat > 5:
            score -= 10
            flags.append(SuitabilityFlag("warning", "nutrition", "High saturated fat",
                f"Contains {_fmt(sat_fat)}g of saturated fat per 100g.", -10))
        elif sat_fat > 3:
            score -= 3
        elif sat_fat <= 1.5:
            score += 10
            flags.append(SuitabilityFlag("positive", "nutrition", "Low saturated fat",
                f"Only {_fmt(sat_fat)}g of saturated fat per 100g.", 10))
        else:
            score += 3  # Acceptable level

    # ── Protein Assessment ──
    protein = nutrition.get("protein_g")
    if protein is not None:
        if protein >= 15:
            score += 12
            flags.append(SuitabilityFlag("positive", "nutrition", "High protein content",
                f"Contains {_fmt(protein)}g of protein per 100g.", 12))
        elif protein >= 8:
            score += 8
            flags.append(SuitabilityFlag("positive", "nutrition", "Good protein content",
                f"Contains {_fmt(protein)}g of protein per 100g.", 8))
        elif protein >= 3:
            score += 3

    # ── Fiber Assessment ──
    fiber = nutrition.get("fiber_g")
    if fiber is not None:
        if fiber >= 5:
            score += 12
            flags.append(SuitabilityFlag("positive", "nutrition", "Excellent fiber content",
                f"Contains {_fmt(fiber)}g of fiber per 100g.", 12))
        elif fiber >= 3:
            score += 8
            flags.append(SuitabilityFlag("positive", "nutrition", "Good fiber content",
                f"Contains {_fmt(fiber)}g of fiber per 100g.", 8))
        elif fiber >= 1:
            score += 3

    # Clamp score
    score = max(0, min(100, score))
    return score, flags


# ── Ingredient quality signals ──
# Ingredient COUNT alone is a poor proxy for quality: "sugar, palm oil" is two
# ingredients and not a wholesome product. These lists let the profile score
# reflect what the ingredients actually are.

_REFINED_SUGARS = [
    "sugar", "cane sugar", "brown sugar", "invert sugar", "glucose", "dextrose",
    "fructose", "corn syrup", "glucose syrup", "golden syrup", "maltodextrin",
    "maltose", "molasses", "treacle", "honey powder", "caramel",
]
_REFINED_FATS = [
    "palm oil", "palmolein", "palm kernel", "hydrogenated", "shortening",
    "vanaspati", "interesterified", "margarine",
]
_ADDITIVES = [
    "artificial flavour", "artificial flavor", "artificial colour", "artificial color",
    "monosodium glutamate", "msg", "aspartame", "saccharin", "sucralose",
    "acesulfame", "sodium benzoate", "potassium sorbate", "bha", "bht", "tbhq",
    "carrageenan", "emulsifier", "stabiliser", "stabilizer", "preservative",
    "anticaking", "anti caking", "raising agent", "humectant", "flavour enhancer",
    "flavor enhancer", "colour", "artificial sweetener",
]
_WHOLE_FOODS = [
    "whole grain", "wholegrain", "whole wheat", "wholewheat", "oat", "oats",
    "brown rice", "quinoa", "millet", "barley", "lentil", "chickpea", "bean",
    "almond", "walnut", "cashew", "pistachio", "peanut", "seed", "flaxseed",
    "chia", "sunflower seed", "pumpkin seed", "vegetable", "spinach", "tomato",
    "carrot", "fruit", "date", "raisin", "apple", "banana", "berry", "milk",
    "yogurt", "yoghurt", "egg", "olive oil", "water", "salt", "spice",
    "cinnamon", "turmeric", "ginger", "garlic",
]
# E-numbers (E100-E1999), the generic marker for an additive.
_E_NUMBER = re.compile(r"\be\s?\d{3}\b")


def _classify_ingredient(name_norm: str) -> Optional[str]:
    """Return "poor" for a refined/additive ingredient, "whole" for a whole food."""
    if _E_NUMBER.search(name_norm):
        return "poor"
    for term in _REFINED_SUGARS + _REFINED_FATS + _ADDITIVES:
        if term in name_norm:
            return "poor"
    for term in _WHOLE_FOODS:
        if re.search(rf"\b{re.escape(term)}s?\b", name_norm):
            return "whole"
    return None


def _calculate_ingredient_profile(product_ingredients: list[dict]) -> tuple[int, list[SuitabilityFlag]]:
    """
    Score the ingredient list on composition quality, not just length.

    Count sets a base, but what the ingredients ARE dominates. Position is
    weighted because labels are ordered by quantity: the first few ingredients
    are most of the product, so refined sugar listed first matters far more
    than a stabiliser listed twelfth.
    """
    flags = []
    num_ingredients = len(product_ingredients)

    if num_ingredients == 0:
        return 60, [SuitabilityFlag("info", "ingredient",
            "No ingredient data", "Ingredient information not available for this product.", 0)]

    # Base from count — a narrower band than before, so quality can move it.
    if num_ingredients <= 5:
        score = 78
    elif num_ingredients <= 10:
        score = 72
    elif num_ingredients <= 20:
        score = 65
    elif num_ingredients <= 30:
        score = 55
    else:
        score = 45

    penalty = 0.0
    bonus = 0.0
    poor_names: list[str] = []
    whole_names: list[str] = []

    for idx, ing in enumerate(product_ingredients):
        name = str(ing.get("name", "")).strip()
        if not name:
            continue
        kind = _classify_ingredient(_normalize(name))
        if kind is None:
            continue
        # Ingredients are listed by descending quantity.
        weight = 3.0 if idx < 3 else 2.0 if idx < 6 else 1.0
        if kind == "poor":
            penalty += 4.0 * weight
            if len(poor_names) < 4:
                poor_names.append(name)
        else:
            bonus += 2.5 * weight
            if len(whole_names) < 4:
                whole_names.append(name)

    # Cap both sides so a very long label can't run the score to an extreme.
    score = score - min(45.0, penalty) + min(25.0, bonus)
    score = max(0, min(100, int(round(score))))

    if poor_names:
        flags.append(SuitabilityFlag(
            "warning", "ingredient", "Refined or processed ingredients",
            "Contains " + ", ".join(poor_names)
            + (" among others." if len(poor_names) >= 4 else "."),
            -int(min(45.0, penalty)),
        ))
    if whole_names:
        flags.append(SuitabilityFlag(
            "positive", "ingredient", "Whole-food ingredients",
            "Contains " + ", ".join(whole_names) + ".",
            int(min(25.0, bonus)),
        ))

    if num_ingredients <= 5 and not poor_names:
        flags.append(SuitabilityFlag("positive", "ingredient",
            "Simple composition", f"Only {num_ingredients} ingredients — minimally processed.", 5))
    elif num_ingredients > 30:
        flags.append(SuitabilityFlag("warning", "ingredient",
            "Very complex composition", f"{num_ingredients} ingredients — highly processed product.", -10))

    return score, flags


def calculate_suitability(
    product_nutrition: Optional[dict],
    product_allergens: list[dict],
    product_ingredients: list[dict],
    user_goals: list[dict],
    user_allergies: list[dict],
    user_preferences: list[dict],
    custom_profiles: dict = None,
    inferred_allergen_matches: dict = None,
    dietary_pattern: Optional[str] = None,
) -> SuitabilityResult:
    """
    Main entry point: Calculate the Personal Suitability Score.

    Args:
        product_nutrition: Dict of nutrition values (energy_kcal, protein_g, etc.)
        product_allergens: List of dicts with allergen info [{allergen, certainty}]
        product_ingredients: List of dicts with ingredient info [{name, position}]
        user_goals: List of dicts [{goal_type, priority}]
        user_allergies: List of dicts [{allergen, allergy_type, severity}]
        user_preferences: List of dicts [{preference_type, is_hard_constraint}]
    """
    all_flags: list[SuitabilityFlag] = []

    # ── Step 1: Allergen Check (Hard + Soft Constraints) ──
    _LEVEL_ORDER = {"none": 0, "preference": 1, "trace": 2, "confirmed": 3}
    conflict_level = "none"
    # Severities of every allergy that reached the current worst level. Only if
    # ALL of them are mild do we soften the cap — otherwise one mild allergy
    # listed before a severe one would understate the risk.
    conflict_severities: list[str] = []
    unrecognized_allergens: list[str] = []
    ai_checked_allergens: list[str] = []
    inferred_allergen_matches = inferred_allergen_matches or {}
    for allergy in user_allergies:
        allergen_name = allergy.get("allergen", "")
        inferred = inferred_allergen_matches.get(_normalize(allergen_name))
        allergen_flags, level, recognized = _check_allergen_match(
            allergen_name,
            allergy.get("allergy_type", "allergy"),
            allergy.get("severity"),
            product_allergens,
            product_ingredients,
            inferred,
        )
        if allergen_flags:
            all_flags.extend(allergen_flags)
        if _LEVEL_ORDER[level] > _LEVEL_ORDER[conflict_level]:
            conflict_level = level
            conflict_severities = [_normalize(allergy.get("severity") or "")]
        elif level != "none" and _LEVEL_ORDER[level] == _LEVEL_ORDER[conflict_level]:
            conflict_severities.append(_normalize(allergy.get("severity") or ""))
        # An allergen we don't know synonyms for was only checked by literal
        # name, so a clean result does not mean the product is definitely safe.
        if not recognized and level == "none" and allergen_name:
            if inferred is not None:
                ai_checked_allergens.append(allergen_name)
            else:
                unrecognized_allergens.append(allergen_name)

    if unrecognized_allergens:
        names = ", ".join(unrecognized_allergens)
        all_flags.append(SuitabilityFlag(
            flag_type="info",
            category="allergen",
            title="Limited allergen coverage",
            description=(
                f"We only matched {names} by name — no known synonyms are on file. "
                "Check the label yourself before consuming."
            ),
            impact=0,
        ))

    if ai_checked_allergens:
        names = ", ".join(ai_checked_allergens)
        # An AI "not found" is weaker evidence than a synonym table, so this
        # still tells the user to verify rather than declaring the product safe.
        all_flags.append(SuitabilityFlag(
            flag_type="info",
            category="allergen",
            title="Ingredients checked by AI",
            description=(
                f"We found no sign of {names} in this product's ingredients, but that "
                "check was AI-based rather than from a declared label. Check the packaging "
                "before consuming."
            ),
            impact=0,
        ))

    # A confirmed/trace match against a real allergy or intolerance makes the
    # product unsafe. A soft preference does not affect safety.
    allergen_safe = conflict_level in ("none", "preference")

    # ── Step 1b: Dietary pattern (hard constraint) ──
    # A pattern the user declared rules a product out on composition, the same
    # way an allergen does, and is reported as its own finding rather than
    # folded into the score's explanation.
    diet_flags, diet_level, _diet_uncertain = _check_diet_compatibility(
        dietary_pattern, product_ingredients, product_allergens
    )
    all_flags.extend(diet_flags)
    diet_compatible = diet_level != "incompatible"

    # ── Step 2: Goal Alignment (Soft Constraints) ──
    goal_alignments: list[GoalAlignment] = []
    goal_weights: list[float] = []
    if user_goals:
        for goal in user_goals:
            alignment = _evaluate_goal_alignment(
                goal.get("goal_type", "general health"),
                product_nutrition,
                custom_profiles,
                goal.get("status_message"),
            )
            goal_alignments.append(alignment)
            priority = goal.get("priority") or 0
            goal_weights.append(1.0 + max(0, priority) * 0.5)
    else:
        # Default to general health if no goals set
        goal_alignments.append(
            _evaluate_goal_alignment("general health", product_nutrition, custom_profiles)
        )
        goal_weights.append(1.0)

    any_goal_matched = any(ga.matched_profile for ga in goal_alignments)

    # Tell the user which goals aren't contributing, rather than letting an
    # unscoreable goal quietly sit at a neutral 50.
    unscored_goals = [ga.goal for ga in goal_alignments if not ga.matched_profile]
    if unscored_goals and user_goals:
        names = ", ".join(unscored_goals)
        # When an allergen conflict caps the score, the goal components aren't
        # what the number reflects — and the allergen must stay the most
        # prominent finding, so this stays informational rather than a warning.
        if conflict_level in ("confirmed", "trace"):
            detail = "This product's score is set by the allergen conflict above."
            flag_type = "info"
        elif not any_goal_matched:
            detail = "This score reflects general nutritional quality only, not your goals."
            flag_type = "warning"
        else:
            detail = "Your other goals were still applied."
            flag_type = "info"

        all_flags.append(SuitabilityFlag(
            flag_type=flag_type,
            category="goal",
            title="Goal not scored",
            description=f"We couldn't score this product against {names}. {detail}",
            impact=0,
        ))

    # ── Step 3: Nutritional Quality Index ──
    nutritional_quality_score, nutrition_flags = _calculate_nutritional_quality(product_nutrition)
    all_flags.extend(nutrition_flags)

    # ── Step 4: Ingredient Profile Score ──
    ingredient_score, ingredient_flags = _calculate_ingredient_profile(product_ingredients)
    all_flags.extend(ingredient_flags)

    # ── Step 5: Nutrient preferences (soft constraints) ──
    preference_flags, preference_adjustment, preference_level = _evaluate_preferences(
        user_preferences, product_nutrition
    )
    all_flags.extend(preference_flags)

    # ── Composite Score ──
    # Goal alignment is the strongest signal (50%), nutritional quality supports
    # it (30%), ingredient profile is a minor modifier (20%).
    # Only goals we could actually evaluate get a vote. An unscoreable goal
    # sitting at a neutral 50 would drag a bad product upwards and a good one
    # downwards, which is worse than not counting it at all.
    scored = [(ga, w) for ga, w in zip(goal_alignments, goal_weights) if ga.matched_profile]

    if scored:
        total_goal_weight = sum(w for _, w in scored) or 1.0
        goal_score = int(sum(ga.score * w for ga, w in scored) / total_goal_weight)
        applied_weights = {
            "goal_alignment": 0.50,
            "nutritional_quality": 0.30,
            "ingredient_profile": 0.20,
        }
        base_overall = int(
            goal_score * 0.50 +
            nutritional_quality_score * 0.30 +
            ingredient_score * 0.20
        )
    else:
        # No goal could be evaluated — drop the goal term so it stops voting.
        # Deliberately NOT a straight 3:2 renormalization of the remaining
        # 0.30/0.20: ingredient profile is only an ingredient *count* heuristic,
        # so doubling its weight would reward a two-ingredient candy. Lean on
        # nutritional quality, which is the one real signal left.
        goal_score = None
        applied_weights = {
            "goal_alignment": 0.0,
            "nutritional_quality": 0.75,
            "ingredient_profile": 0.25,
        }
        base_overall = int(
            nutritional_quality_score * 0.75 +
            ingredient_score * 0.25
        )

    # Preferences adjust the assessment before any cap, so a product ruled out
    # by an allergen or dietary pattern stays ruled out regardless of how many
    # preferences it happens to satisfy.
    base_overall = max(0, min(100, base_overall + preference_adjustment))

    if conflict_level == "confirmed":
        # Hard cap. A "mild" severity is slightly less punishing than
        # moderate/severe/unspecified.
        cap = 30 if (conflict_severities and all(sv == "mild" for sv in conflict_severities)) else 15
        overall = min(cap, base_overall)
    elif conflict_level == "trace":
        # Serious but not an outright disqualification.
        overall = min(45, int(base_overall * 0.6))
    elif conflict_level == "preference":
        # Soft: proportional penalty, no hard floor/ceiling.
        overall = int(base_overall * 0.9) - 5
    else:
        overall = base_overall

    # A dietary pattern is a hard constraint: the product does not qualify,
    # however well it scores nutritionally. Applied after the allergen cap and
    # taking whichever is lower, so a product that is both stays at the
    # stricter number rather than being lifted by this one.
    if not diet_compatible:
        overall = min(overall, 15)

    # A preference the user marked strict is a hard constraint too, but a
    # self-imposed one — capped above the allergen and dietary ceilings so the
    # three stay distinguishable.
    if preference_level == "strict":
        overall = min(overall, _STRICT_PREFERENCE_CAP)

    overall = max(0, min(100, overall))

    # ── Verdict ──
    has_nutrition = bool(product_nutrition) and any(
        v is not None for v in product_nutrition.values()
    )

    # The allergen verdict leads when both apply — it is the safety one.
    if conflict_level == "confirmed":
        verdict = "Not suitable — contains an allergen from your profile"
    elif conflict_level == "trace":
        verdict = "Caution — may contain an allergen from your profile"
    elif not diet_compatible:
        verdict = (
            f"Not {_normalize(dietary_pattern or '').capitalize()} — "
            "excluded by your dietary pattern"
        )
    elif preference_level == "strict":
        verdict = "Does not meet a preference you marked as strict"
    else:
        if not has_nutrition and conflict_level == "none":
            verdict = "Limited data — suitability could not be fully assessed"
        elif overall >= 80:
            verdict = "Highly suitable for your health profile"
        elif overall >= 60:
            verdict = "Generally suitable with some considerations"
        elif overall >= 40:
            verdict = "Moderate suitability — review the details"
        elif overall >= 20:
            verdict = "Low suitability for your goals"
        else:
            verdict = "Not recommended based on your profile"

        if conflict_level == "preference":
            verdict = f"Contains an ingredient you avoid — {verdict[0].lower()}{verdict[1:]}"

    # ── Confidence: driven by how much real data we actually had ──
    confidence = 25
    if product_nutrition:
        n_fields = sum(1 for v in product_nutrition.values() if v is not None)
        confidence += min(35, n_fields * 5)
    if product_ingredients:
        confidence += min(15, len(product_ingredients) * 3)
    if product_allergens:
        confidence += 10
    if any_goal_matched:
        confidence += 10
    if not user_goals:
        confidence -= 5
    confidence = max(20, min(95, int(confidence)))

    return SuitabilityResult(
        overall_score=overall,
        verdict=verdict,
        confidence=confidence,
        allergen_safe=allergen_safe,
        diet_compatible=diet_compatible,
        flags=all_flags,
        goal_alignments=goal_alignments,
        nutritional_quality_score=nutritional_quality_score,
        ingredient_profile_score=ingredient_score,
        breakdown={
            # None when no goal could be evaluated — the goal term was dropped
            # and the remaining weights renormalized.
            "goal_alignment": goal_score,
            "nutritional_quality": nutritional_quality_score,
            "ingredient_profile": ingredient_score,
            "allergen_conflict": conflict_level,
            # "none" | "uncertain" | "incompatible"
            "diet_conflict": diet_level,
            # "none" | "soft" | "strict", and the net points preferences moved
            # the score by (already included in overall_score).
            "preference_conflict": preference_level,
            "preference_adjustment": preference_adjustment,
            "unscored_goals": unscored_goals,
            "weights": applied_weights,
        }
    )
