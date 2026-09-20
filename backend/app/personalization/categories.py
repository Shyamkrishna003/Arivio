"""
Category-aware scoring.

Every threshold in the engine is written for a food you eat by the plate. Applied
unchanged to a cooking oil or a soft drink they mislead, because the same number
means something different depending on what the product is.

Extra virgin olive oil scored 58 — below white bread at 86. Its goal alignment
was 40, and the arithmetic shows why:

    sugar    0g    → 100 ✓
    sodium   2mg   → 100 ✓
    sat fat  14g   →   0 ✗   judged against a threshold meant for foods
    protein  0g    →   0 ✗   an oil has no protein
    fibre    0g    →   0 ✗   an oil has no fibre
                     ────
                      40

Two of those zeros are not failures. Oil contains no protein or fibre *by
nature*, and marking it down for that is like failing a fish for not climbing.
The engine already skips a nutrient it has no data for; it just could not tell
"not measured" from "not applicable".

So a category profile declares two things:

  `not_applicable` — nutrients that carry no information for this food type and
      should be skipped rather than scored as zero.

  `derived` + `thresholds` — a different, more meaningful basis for a nutrient
      that does matter. For oils, absolute saturated fat says little (every oil
      is nearly all fat); the share of that fat which is saturated says a lot.
      Olive oil is 14% saturated, butter 63%, coconut oil 87%.

Scope is deliberately narrow. Only categories with a demonstrated scoring
failure are here, and these are the same two that Nutri-Score singles out for
special handling — beverages and added fats — which is a reasonable sign they
are the ones that genuinely need it. Cheese and nuts are the usual next
candidates; the framework takes them without change, but nothing in this
catalogue currently mis-scores because of them, so they are not guessed at.

A category may also declare how much of it a person actually eats. Ground
cinnamon scored 93 — above white bread — on the figures for 100g of it, and
nobody eats 100g of cinnamon. See REFERENCE_PORTIONS_G below.

That does not soften the disqualifying-nutrient rule, because a realistic
portion of soy sauce still delivers 41% of a day's sodium. It changes the
question from "is this number large per 100g?" to "how much of this are you
getting when you use the thing", which is the one that matters.
"""

from typing import Optional

# ──────────────────────────────────────────────
# Portion realism
# ──────────────────────────────────────────────
#
# Every threshold is per 100g, which is right for a food you eat by the plate
# and meaningless for one you use by the teaspoon. Ground cinnamon scored 93 —
# above white bread, near lentils — on figures for 100g of it. Nobody eats 100g
# of cinnamon.
#
# The portions below are OURS, per category, and deliberately not the label's
# `serving_size`. A declared serving is set by the manufacturer and is the
# classic thing to shrink when the numbers look bad; per-100g labelling is
# mandated precisely because of that. A category-typical amount cannot be
# gamed by the product being scored.
#
# Only categories where the per-100g basis actively misleads get one. Anything
# without a portion keeps the per-100g rules unchanged, which is the safe
# default and the common case.
REFERENCE_PORTIONS_G: dict[str, float] = {
    "seasonings": 2.0,    # half a teaspoon of a dried spice or herb
    "condiments": 15.0,   # a tablespoon of sauce, ketchup, mustard
    "added_fats": 14.0,   # a tablespoon of oil or butter
    "beverages": 250.0,   # a glass or a small can
}

# Adult daily reference intakes. Sodium and saturated fat are the WHO/EU
# figures; energy is the conventional 2,000 kcal.
#
# Total sugars uses the EU Reference Intake of 90g rather than the WHO free-
# sugars limit of 50g, because `total_sugars_g` includes intrinsic sugars —
# the lactose in milk, the fructose in fruit. Judging those against a free-
# sugars limit would mark plain milk down as if it were a soft drink. The
# stricter figure would be right for `added_sugars_g`, which labels rarely
# carry.
REFERENCE_INTAKES: dict[str, float] = {
    "sodium_mg": 2000.0,
    "total_sugars_g": 90.0,
    "added_sugars_g": 50.0,
    "saturated_fat_g": 20.0,
    "trans_fat_g": 2.0,
    "energy_kcal": 2000.0,
}

# Share of a day's reference that one realistic portion has to deliver before a
# nutrient counts as disqualifying. Replaces the per-100g multiple wherever a
# portion is known, because it answers the question that actually matters:
# how much of this are you getting when you eat the thing.
PORTION_SEVERE_SHARE = 0.15
PORTION_EXTREME_SHARE = 0.30

# Below this, a portion contributes so little of anything that the product
# cannot be meaningfully praised or condemned on nutrition.
PORTION_NEGLIGIBLE_SHARE = 0.02

# Ceiling on a negligible-contribution product's score. Not a strong
# endorsement — a seasoning is not a dietary choice. Deliberately a ceiling
# only, never a floor: every other cap in the engine holds a score DOWN, and
# lifting one would undo an allergen or dietary conflict.
NEGLIGIBLE_SCORE_CEILING = 70


CATEGORY_PROFILES: dict[str, dict] = {
    "beverages": {
        "label": "Drink",
        "not_applicable": set(),
        # Drinks are consumed in far larger volumes than solids are eaten by
        # weight, so the same grams-per-100 means more. Nutri-Score scores
        # beverages on their own stricter scale for exactly this reason; the
        # FSA's front-of-pack red line for drinks is likewise half the one for
        # food (11.25g of sugar per 100ml, not 22.5g).
        # Carbohydrate is scaled alongside sugar, not instead of it. In a
        # sugary drink the two are very nearly the same grams, so leaving
        # carbohydrate on the solid-food scale let a cola score a perfect 100
        # for "low carbohydrate" — 10.6g reads as comfortably under a 20g
        # limit — while the identical 10.6g was being penalised as sugar. One
        # quantity, counted twice, in opposite directions.
        "threshold_scale": {"total_sugars_g": 0.5, "total_carbohydrates_g": 0.5},
        "note": "Scored on the stricter scale used for drinks.",
    },
    "added_fats": {
        "label": "Cooking fat or oil",
        # Every cooking oil is ~100g fat and ~900 kcal per 100g, and none has
        # protein, fibre, sugar or carbohydrate. Scoring those gives every oil
        # in existence the same zeros and buries the one thing that separates
        # a good fat from a bad one.
        "not_applicable": {
            "energy_kcal", "total_fat_g", "protein_g", "fiber_g",
            "total_carbohydrates_g", "total_sugars_g",
        },
        # What does separate them: how much of that fat is saturated.
        "derived": {"saturated_fat_g": "saturated_share_of_fat"},
        "thresholds": {"saturated_fat_g": {"good": 20.0, "bad": 50.0}},
        # Bands for the nutritional-quality index, in the same units as the
        # derived value (percent of total fat).
        "quality_bands": {
            "saturated_fat_g": {"high": 50.0, "mid": 35.0, "low": 20.0},
        },
        "labels": {"saturated_fat_g": "saturated fat (% of total fat)"},
        "units": {"saturated_fat_g": "%"},
        "note": (
            "Judged as a fat: what matters is how much of the fat is "
            "saturated, not how much fat there is."
        ),
    },
    # Portion-only categories. They change nothing about which nutrients are
    # scored or against what thresholds — they exist solely so the engine knows
    # roughly how much of the product a person actually consumes.
    "seasonings": {
        "label": "Spice or seasoning",
        "not_applicable": set(),
        "note": "Used by the spoonful, so its per-100g figures are judged against a realistic portion.",
    },
    "condiments": {
        "label": "Sauce or condiment",
        "not_applicable": set(),
        "note": "Used by the spoonful, so its per-100g figures are judged against a realistic portion.",
    },
    # Deliberately given no reference portion. A portion would SOFTEN the
    # score, and these do not need softening — 30g of crisps still delivers
    # what makes crisps crisps. What they needed was to stop being praised for
    # the one bad thing they happen not to contain.
    "savoury_snacks": {
        "label": "Savoury snack",
        # Sugar is not the axis these live or die on, and scoring it handed a
        # bag of crisps a "low sugar" bonus worth +12 — the largest single
        # positive in its whole breakdown, for not being a biscuit. Removing
        # it does not penalise them; it stops them being rewarded on an axis
        # that carries no information about them.
        "not_applicable": {"total_sugars_g"},
        "note": (
            "Judged as a savoury snack: sugar says nothing useful about one, "
            "so it is scored on salt and energy density instead."
        ),
    },
}


# Text fragments that identify a category, checked in order. Open Food Facts
# categories are free text and frequently not English — this catalogue alone
# holds "Bonbons de chocolat", "Polvos de proteína" and "en:Confectionary based
# spreads" — so matching is on substrings across a few languages rather than on
# an exact taxonomy.
#
# Order matters: nut butters must not read as fats because they contain
# "butter", so the exclusions are checked first.
_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "added_fats": (
        # Contain "butter" but are not cooking fats.
        "nut butter", "peanut butter", "almond butter", "cashew butter",
        "beurre de cacahuete", "mantequilla de mani",
        # Cocoa butter is an ingredient of confectionery, not a cooking fat.
        "cocoa butter", "buttermilk", "butter biscuit", "butter cookie",
        "shortbread", "butter chicken", "buttercream", "oil free",
    ),
    "condiments": (
        # Eaten by the plateful, not the spoonful, whatever the word "sauce"
        # suggests. Giving these a 15g portion would understate them.
        "pasta sauce", "cooking sauce", "curry sauce", "simmer sauce",
        "baked bean", "soup",
    ),
    "savoury_snacks": (
        # "chip" and "cracker" are the broad ones. Each of these is sweet, or
        # is a meal, and would be scored on the wrong axis — a chocolate chip
        # cookie judged as a savoury snack would have its sugar ignored, which
        # is the one number that matters for it.
        "chocolate chip", "choc chip", "chocolate", "cookie", "biscuit",
        "graham cracker", "sweet cracker", "fruit chip", "banana chip",
        "apple chip", "candy", "toffee", "caramel",
        # A cooked meal, not a snack by the handful.
        "fish and chips", "chips and", "french fries", "frozen chip",
    ),
    "seasonings": (
        # A "spice mix" sold as a meal base is not a seasoning by the spoonful.
        "spice mix meal", "seasoned meal",
    ),
    "beverages": (
        # A powder you make a drink from is not itself a drink.
        "drinking chocolate", "drink mix", "drink powder",
        # Open Food Facts has genuinely mixed categories. "Christmas foods and
        # drinks" is a real one in this catalogue, and it is on a hazelnut
        # spread — matching it as a beverage would score the spread on the
        # stricter drinks sugar scale for no reason.
        "foods and drinks", "food and drink", "food and beverage",
        "foods and beverages",
    ),
}

# Written singular; the matcher allows an optional trailing "s".
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "added_fats": (
        "olive oil", "sunflower oil", "vegetable oil", "cooking oil",
        "coconut oil", "mustard oil", "sesame oil", "groundnut oil",
        "rapeseed oil", "canola", "ghee", "vanaspati", "margarine",
        "shortening", "lard", "tallow", "dripping", "added fat",
        "oil", "huile", "aceite", "azeite", "olio", "olie",
        "butter", "beurre", "mantequilla", "matiere grasse",
    ),
    "seasonings": (
        "spice", "seasoning", "herb", "dried herb", "masala",
        "pepper", "cinnamon", "turmeric", "oregano", "paprika", "epice",
        # Salt is a seasoning, not a condiment: a tablespoon of it is not a
        # portion anyone uses.
        "salt", "table salt", "sea salt",
    ),
    "condiments": (
        "condiment", "sauce", "ketchup", "mustard", "mayonnaise",
        "dressing", "pickle", "chutney", "vinegar", "salsa", "relish",
    ),
    "beverages": (
        "beverage", "drink", "juice", "soda", "cola", "soft drink",
        "water", "tea", "coffee", "smoothie", "cordial", "lemonade",
        "energy drink", "boisson", "bebida", "nectar", "infusion",
    ),
    "savoury_snacks": (
        "crisp", "potato chip", "savoury snack", "savory snack",
        "namkeen", "bhujia", "sev", "mixture", "extruded snack",
        "tortilla chip", "nacho", "pretzel", "papad", "pappadam",
        "salted popcorn", "cracker", "wafer snack", "chips",
    ),
}

# Matching is on whole words, not substrings. Both of the false positives this
# caught were substrings of ordinary words: "cola" inside "cho-cola-te
# biscuits", and "chocolat" likewise. A bare `in` test would have scored a
# chocolate biscuit on the drinks scale.
_PATTERNS: dict[str, list] = {}
_EXCLUSION_PATTERNS: dict[str, list] = {}


def _compile(terms: tuple[str, ...]) -> list:
    import re
    return [re.compile(rf"\b{re.escape(t)}s?\b") for t in terms]


for _key, _terms in _KEYWORDS.items():
    _PATTERNS[_key] = _compile(_terms)
for _key, _terms in _EXCLUSIONS.items():
    _EXCLUSION_PATTERNS[_key] = _compile(_terms)


# A category name alone is not proof. Open Food Facts categories are
# user-contributed free text, and a wrong profile changes which nutrients are
# scored at all — so a category that claims to be a cooking fat also has to
# look like one. Oils and butter are almost entirely fat with negligible
# protein; peanut butter (50g fat but 25g protein) is not a cooking fat
# however it is labelled.
_COMPOSITION_GUARDS = {
    "added_fats": lambda n: (
        (n.get("total_fat_g") or 0) >= 50 and (n.get("protein_g") or 0) <= 5
    ),
}

# Ingredient names that identify a product with no usable category. A single
# ingredient that is itself a cooking fat is a strong signal: "olive oil" as the
# sole ingredient IS olive oil.
_SOLE_INGREDIENT_FATS = (
    "olive oil", "sunflower oil", "vegetable oil", "coconut oil",
    "mustard oil", "sesame oil", "groundnut oil", "rapeseed oil",
    "canola oil", "ghee", "clarified butter", "butter", "margarine",
    "rice bran oil", "palm oil", "soybean oil", "corn oil", "avocado oil",
)


def _clean(text: Optional[str]) -> str:
    """Lower-case, strip the `en:` style prefixes OFF uses, collapse spaces."""
    if not text:
        return ""
    value = str(text).strip().lower()
    if ":" in value[:4]:
        value = value.split(":", 1)[1]
    return " ".join(value.replace("-", " ").replace(",", " ").split())


def resolve_category(
    category: Optional[str],
    ingredients: Optional[list[dict]] = None,
    nutrition: Optional[dict] = None,
) -> Optional[str]:
    """
    Work out which category profile applies, or None.

    None means "score it the ordinary way" — an unknown or missing category
    must never be guessed at, because a wrong profile silently changes which
    nutrients are scored at all. Roughly one product in seven here has no
    category, so this path is the common one and has to be harmless.

    Three gates, all of which must pass: no exclusion matches, a keyword
    matches as a whole word, and the composition is consistent with the
    category.
    """
    text = _clean(category)
    nutrition = nutrition or {}

    for key in CATEGORY_PROFILES:
        if any(p.search(text) for p in _EXCLUSION_PATTERNS.get(key, ())):
            continue
        if not any(p.search(text) for p in _PATTERNS.get(key, ())):
            continue
        guard = _COMPOSITION_GUARDS.get(key)
        if guard and not guard(nutrition):
            continue
        return key

    # No usable category. A product whose entire ingredient list is one cooking
    # fat is one — this is how an oil with no category still gets judged as an
    # oil rather than as a food that failed to contain protein.
    if not text and ingredients and len(ingredients) == 1:
        only = _clean(ingredients[0].get("name"))
        if any(only == fat or only.endswith(f" {fat}") for fat in _SOLE_INGREDIENT_FATS):
            guard = _COMPOSITION_GUARDS["added_fats"]
            if guard(nutrition):
                return "added_fats"

    return None


def get_profile(category_key: Optional[str]) -> Optional[dict]:
    return CATEGORY_PROFILES.get(category_key) if category_key else None


def apply_category_view(
    nutrition: Optional[dict],
    profile: Optional[dict],
    *,
    include_derived: bool = True,
) -> Optional[dict]:
    """
    Present the nutrition as this category should be judged.

    Nutrients that carry no information for the category become None — the same
    thing "we have no data" looks like, which every scorer already skips
    correctly. Derived nutrients are replaced by the more meaningful basis.

    The original dict is left alone; flags that quote a raw figure keep quoting
    the real one.
    """
    if not nutrition or not profile:
        return nutrition

    view = dict(nutrition)

    for key in profile.get("not_applicable", ()):  # noqa: SIM118 — set or dict
        view[key] = None

    # A caller whose thresholds are in absolute grams must NOT be handed the
    # derived share, which is a percentage. The health path was, and so
    # compared butter's "63" — 63% of its fat being saturated — against a
    # cut-off meaning 5 grams, and printed it back to the user as "saturated
    # fat 63g per 100g" for a product containing 51g. The number, the unit and
    # the comparison were all wrong at once.
    for key, rule in ((profile.get("derived") or {}) if include_derived else {}).items():
        if rule != "saturated_share_of_fat":
            continue
        saturated = nutrition.get("saturated_fat_g")
        total_fat = nutrition.get("total_fat_g")
        # Needs both, and a total that is actually fat — otherwise leave the
        # raw value rather than inventing a share.
        if saturated is None or not total_fat or total_fat <= 0:
            view[key] = None
        else:
            view[key] = round(min(100.0, saturated / total_fat * 100.0), 1)

    return view


def nutrient_thresholds(profile: Optional[dict], nutrient: str) -> Optional[dict]:
    """A category's replacement {"good", "bad"} for one nutrient, if any."""
    if not profile:
        return None
    return (profile.get("thresholds") or {}).get(nutrient)


def threshold_scale(profile: Optional[dict], nutrient: str) -> float:
    """Multiplier for the built-in cut-offs — 1.0 leaves them alone."""
    if not profile:
        return 1.0
    return float((profile.get("threshold_scale") or {}).get(nutrient, 1.0))


def quality_bands(profile: Optional[dict], nutrient: str) -> Optional[dict]:
    """A category's replacement bands for the nutritional-quality index."""
    if not profile:
        return None
    return (profile.get("quality_bands") or {}).get(nutrient)


def nutrient_label(profile: Optional[dict], nutrient: str, default: str) -> str:
    if not profile:
        return default
    return (profile.get("labels") or {}).get(nutrient, default)


def nutrient_unit(profile: Optional[dict], nutrient: str, default: str) -> str:
    if not profile:
        return default
    return (profile.get("units") or {}).get(nutrient, default)


def reference_portion(category_key: Optional[str]) -> Optional[float]:
    """Grams of this category a person realistically consumes at once."""
    return REFERENCE_PORTIONS_G.get(category_key) if category_key else None


def portion_share(nutrient: str, per_100g: float, portion_g: float) -> Optional[float]:
    """
    What one realistic portion delivers, as a share of the daily reference.

    None when we have no reference for the nutrient — the caller then falls
    back to the per-100g rule rather than guessing.
    """
    reference = REFERENCE_INTAKES.get(nutrient)
    if not reference:
        return None
    return (per_100g * portion_g / 100.0) / reference


def portion_amount(per_100g: float, portion_g: float) -> float:
    """How much of a nutrient one realistic portion contains."""
    return per_100g * portion_g / 100.0


def is_negligible_portion(
    nutrition: Optional[dict], portion_g: Optional[float]
) -> bool:
    """
    Whether a realistic portion contributes so little of everything that the
    product cannot be meaningfully rated on nutrition.

    True for spices and dried herbs: half a teaspoon of cinnamon delivers
    hundredths of a percent of a day's sugar, salt or fat. Scoring that 93/100
    reads as a strong dietary endorsement of something that is neither good
    nor bad for you in the amount you use.
    """
    if not nutrition or not portion_g:
        return False

    measured = 0
    for nutrient, reference in REFERENCE_INTAKES.items():
        value = nutrition.get(nutrient)
        if value is None:
            continue
        measured += 1
        if portion_amount(value, portion_g) / reference >= PORTION_NEGLIGIBLE_SHARE:
            return False

    # Needs something to have been measured — a product with no nutrition data
    # is unknown, not negligible.
    return measured >= 2
