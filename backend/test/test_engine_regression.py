import sys
sys.path.insert(0, '.')
from app.personalization.engine import (
    calculate_suitability, _resolve_goal_key, _build_allergen_synonyms,
    _text_contains_allergen, _resolve_preference_key, GOAL_PROFILES,
)

fails = []
def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
    if not ok:
        fails.append(name)

print("=== goal alias resolution ===")
for raw, want in [("muscle_building","muscle gain"),("weight_management","weight loss"),
                  ("Blood Sugar Control","diabetes management"),("cardiovascular health","heart health"),
                  ("energy","energy boost"),("muscle_gain","muscle gain"),("keto diet","keto diet")]:
    check(f"resolve {raw}", _resolve_goal_key(raw), want)

print("\n=== allergen matching: false positives ===")
syn_egg, rec = _build_allergen_synonyms("egg")
check("egg recognized", rec, True)
check("egg vs 'eggplant'", _text_contains_allergen("eggplant", syn_egg), False)
check("egg vs 'egg white'", _text_contains_allergen("egg white", syn_egg), True)
syn_fish, _ = _build_allergen_synonyms("cod")
check("cod vs 'cocoa powder'", _text_contains_allergen("cocoa powder", syn_fish), False)
syn_milk, _ = _build_allergen_synonyms("milk")
check("milk vs 'buttermilk'", _text_contains_allergen("buttermilk", syn_milk), True)

print("\n=== allergen categories: no over-broad expansion ===")
syn_cashew, rec_c = _build_allergen_synonyms("cashew")
check("cashew recognized", rec_c, True)
check("cashew vs 'almond flour'", _text_contains_allergen("almond flour", syn_cashew), False)
check("cashew vs 'roasted cashews'", _text_contains_allergen("roasted cashews", syn_cashew), True)
syn_tn, _ = _build_allergen_synonyms("tree nuts")
check("tree nuts vs 'almond flour'", _text_contains_allergen("almond flour", syn_tn), True)
syn_wheat, _ = _build_allergen_synonyms("wheat")
check("wheat vs 'rye flour'", _text_contains_allergen("rye flour", syn_wheat), False)
syn_gluten, _ = _build_allergen_synonyms("gluten")
check("gluten vs 'rye flour'", _text_contains_allergen("rye flour", syn_gluten), True)
_, rec_kiwi = _build_allergen_synonyms("kiwi")
check("kiwi unrecognized", rec_kiwi, False)

NUT = {'energy_kcal':250,'protein_g':20,'total_fat_g':8,'saturated_fat_g':3,
       'total_carbohydrates_g':25,'total_sugars_g':12,'fiber_g':3,'sodium_mg':200}
ING = [{'name':f'ing_{i}','position':i} for i in range(8)]

def score(**kw):
    base = dict(product_nutrition=NUT, product_allergens=[], product_ingredients=ING,
                user_goals=[{'goal_type':'muscle gain','priority':0}],
                user_allergies=[], user_preferences=[])
    base.update(kw)
    return calculate_suitability(**base)

print("\n=== allergen severity tiers ===")
clean = score()
confirmed = score(product_allergens=[{'allergen':'milk','certainty':'confirmed'}],
                  user_allergies=[{'allergen':'milk','allergy_type':'allergy','severity':'severe'}])
trace = score(product_allergens=[{'allergen':'milk','certainty':'may_contain'}],
              user_allergies=[{'allergen':'milk','allergy_type':'allergy','severity':'severe'}])
pref = score(product_allergens=[{'allergen':'milk','certainty':'confirmed'}],
             user_allergies=[{'allergen':'milk','allergy_type':'preference','severity':None}])
print(f"  clean={clean.overall_score} confirmed={confirmed.overall_score} "
      f"trace={trace.overall_score} preference={pref.overall_score}")
check("confirmed hard-capped <=15", confirmed.overall_score <= 15, True)
check("trace between confirmed and clean", confirmed.overall_score < trace.overall_score < clean.overall_score, True)
check("trace still unsafe", trace.allergen_safe, False)
check("preference stays safe", pref.allergen_safe, True)
check("preference near clean", pref.overall_score > trace.overall_score, True)

print("\n=== unknown allergen disclosure ===")
unk = score(user_allergies=[{'allergen':'kiwi','allergy_type':'allergy','severity':'mild'}])
check("coverage flag raised", any(f.title=="Limited allergen coverage" for f in unk.flags), True)
known_clean = score(user_allergies=[{'allergen':'milk','allergy_type':'allergy','severity':'mild'}])
check("no flag for known allergen", any(f.title=="Limited allergen coverage" for f in known_clean.flags), False)

print("\n=== priority weighting ===")
two_equal = score(user_goals=[{'goal_type':'muscle gain','priority':0},
                              {'goal_type':'weight loss','priority':0}])
muscle_first = score(user_goals=[{'goal_type':'muscle gain','priority':5},
                                 {'goal_type':'weight loss','priority':0}])
print(f"  equal={two_equal.breakdown['goal_alignment']} muscle-priority={muscle_first.breakdown['goal_alignment']}")
check("priority shifts goal score", muscle_first.breakdown['goal_alignment'] != two_equal.breakdown['goal_alignment'], True)

print("\n=== missing nutrition ===")
nodata = score(product_nutrition=None)
check("verdict flags limited data", "Limited data" in nodata.verdict, True)
check("confidence low", nodata.confidence < 60, True)
check("confidence spread vs full data", clean.confidence > nodata.confidence, True)
print(f"  full-data confidence={clean.confidence} no-nutrition confidence={nodata.confidence}")

print("\n=== unmatched goal is not silently neutral-scored ===")
bogus = score(user_goals=[{'goal_type':'become a wizard','priority':0}])
check("bogus goal -> no profile match", bogus.goal_alignments[0].matched_profile, False)
check("bogus goal confidence penalized", bogus.confidence < clean.confidence, True)

print("\n=== non-directional threshold keys skipped ===")
custom = {"my goal": {"label":"My Goal","prefer_low":["total_sugars_g"],"prefer_high":[],
                      "thresholds":{"total_sugars_g":{"good":5,"bad":20},
                                    "protein_g":{"good":15,"bad":3}},
                      "weights":{"total_sugars_g":1.0,"protein_g":1.0}}}
r = calculate_suitability(product_nutrition=NUT, product_allergens=[], product_ingredients=ING,
                          user_goals=[{'goal_type':'my goal','priority':0}], user_allergies=[],
                          user_preferences=[], custom_profiles=custom)
# only total_sugars_g scored: 12 between 5 and 20 -> (1-(7/15))*100 = 53
check("orphan protein_g excluded from average", r.breakdown['goal_alignment'], 53)
check("custom profile matched", r.goal_alignments[0].matched_profile, True)

print("\n=== AI allergen inference (engine side) ===")
from app.personalization.engine import find_unresolved_allergens
ING_D = [{'name':'copra oil','position':1},{'name':'wheat semolina','position':2}]
# only allergens with no synonym coverage AND no literal hit should reach the AI
check("unresolved excludes known allergens",
      find_unresolved_allergens([{'allergen':'wheat','allergy_type':'allergy'},
                                 {'allergen':'coconut','allergy_type':'allergy'}], [], ING_D),
      ['coconut'])
check("unresolved excludes literal hits",
      find_unresolved_allergens([{'allergen':'copra','allergy_type':'allergy'}], [], ING_D),
      [])

def infer(certainty, found=True):
    return calculate_suitability(NUT, [], ING_D,
        [{'goal_type':'general health','priority':0}],
        [{'allergen':'coconut','allergy_type':'allergy','severity':'severe'}], [], {},
        inferred_allergen_matches={'coconut': {'found':found,'certainty':certainty,
            'matched_ingredient':'copra oil','reason':'copra is coconut.'}})

hi, med, none_ = infer('high'), infer('medium'), infer('low', found=False)
check("high certainty -> confirmed", hi.breakdown['allergen_conflict'], 'confirmed')
check("medium certainty -> trace", med.breakdown['allergen_conflict'], 'trace')
check("not found -> no conflict", none_.breakdown['allergen_conflict'], 'none')
check("high certainty unsafe", hi.allergen_safe, False)
check("trace scores above confirmed", med.overall_score > hi.overall_score, True)
check("AI match is attributed",
      any('identified by AI' in f.description for f in hi.flags if f.category=='allergen'), True)
check("not-found still warns user to check label",
      any(f.title=='Ingredients checked by AI' for f in none_.flags), True)

# AI must never be able to clear a real literal match
lit = calculate_suitability(NUT, [], [{'name':'copra oil','position':1}],
    [{'goal_type':'general health','priority':0}],
    [{'allergen':'copra','allergy_type':'allergy','severity':'severe'}], [], {},
    inferred_allergen_matches={'copra': {'found':False,'certainty':'high'}})
check("AI cannot clear a literal match", lit.breakdown['allergen_conflict'], 'confirmed')

print("\n=== ingredient fingerprint (cache invalidation) ===")
from app.allergens.inference import ingredients_fingerprint as _fp
_a = [{'name':'copra oil','position':1},{'name':'salt','position':2}]
_b = [{'name':'Salt','position':1},{'name':'COPRA OIL','position':2}]
_c = _a + [{'name':'natural flavouring','position':3}]
check("reorder + recase keeps hash", _fp(_a,[]) == _fp(_b,[]), True)
check("added ingredient changes hash", _fp(_a,[]) != _fp(_c,[]), True)
check("added declared allergen changes hash", _fp(_a,[]) != _fp(_a,[{'allergen':'milk'}]), True)
check("hash is sha256 hex", len(_fp(_a,[])), 64)

from app.allergens.inference import resolve_model, PROMPT_VERSION
from app.allergens.models import AllergenInference
_cols = {c.name for c in AllergenInference.__table__.columns}
_uq = next(c for c in AllergenInference.__table__.constraints
           if getattr(c, "name", "") == "uq_allergen_inference")
check("cache key includes model + prompt_version",
      sorted(c.name for c in _uq.columns),
      ['allergen', 'model', 'product_id', 'prompt_version'])
check("ingredients_hash stays out of the key (stale label replaces)",
      'ingredients_hash' in {c.name for c in _uq.columns}, False)
check("model resolves without calling the API", isinstance(resolve_model(), str), True)
check("prompt version is an int", isinstance(PROMPT_VERSION, int), True)

print("\n=== ingredient profile reflects quality, not just count ===")
from app.personalization.engine import _calculate_ingredient_profile as _ip
def _score(names):
    return _ip([{'name':n,'position':i} for i,n in enumerate(names)])[0]
_junk = _score(['sugar','glucose syrup','palm oil','artificial flavour','E102'])
_good = _score(['whole wheat flour','almonds','oats','raisins','cinnamon'])
_long_good = _score(['whole oats','almonds','walnuts','dates','cinnamon','sea salt',
                     'quinoa','flaxseed','chia','raisins','pumpkin seeds','vanilla'])
check("same count, junk scores far below wholesome", _junk < _good - 40, True)
check("candy no longer scores like whole food", _score(['sugar','palm oil']) < 60, True)
check("long wholesome list beats short junk list", _long_good > _junk, True)
# labels are ordered by quantity, so position must matter
check("sugar first is worse than additive last",
      _score(['sugar','palm oil','whole oats','almonds'])
      < _score(['whole oats','almonds','dates','water','salt','soy lecithin','E322']), True)
check("E-numbers are detected", _score(['whole oats','E621']) < _score(['whole oats','water']), True)
check("empty list unchanged", _ip([])[0], 60)

print("\n=== severity must not depend on declaration order ===")
_pa=[{'allergen':'milk','certainty':'confirmed'},{'allergen':'peanuts','certainty':'confirmed'}]
def _two(order):
    return calculate_suitability(NUT,_pa,ING,[{'goal_type':'general health','priority':0}],
                                 order,[],{}).overall_score
_mild={'allergen':'milk','allergy_type':'allergy','severity':'mild'}
_sev={'allergen':'peanuts','allergy_type':'allergy','severity':'severe'}
check("mild-then-severe == severe-then-mild", _two([_mild,_sev]), _two([_sev,_mild]))
check("any non-mild severity uses the hard cap", _two([_mild,_sev]), 15)
check("all-mild softens the cap",
      _two([_mild,{'allergen':'peanuts','allergy_type':'allergy','severity':'mild'}]), 30)

print("\n=== rule-based fallback with an unscored goal ===")
from app.ai.gateway import _generate_fallback
_r=calculate_suitability(NUT,[],ING,[{'goal_type':'no muscle','priority':0}],[],[],{})
_gas=[{"goal":g.goal,"alignment":g.alignment,"score":g.score,"reason":g.reason,
       "evaluated":g.matched_profile} for g in _r.goal_alignments]
_fl=[{"flag_type":f.flag_type,"category":f.category,"title":f.title,
      "description":f.description,"impact":f.impact} for f in _r.flags]
_rep=_generate_fallback("X",_r.overall_score,_r.verdict,_r.allergen_safe,_fl,_gas)
check("no 'None/100' in fallback report", "None/100" in _rep.detailed_analysis, False)
check("no raw 'Not_evaluated' in fallback report", "Not_evaluated" in _rep.detailed_analysis, False)
check("unscored goal shown as not assessed", "not assessed" in _rep.detailed_analysis, True)

print("\n=== plant-qualified dairy words are not dairy ===")
# "butter" and "milk" are >=5 chars, so they matched as substrings: peanut
# butter, cocoa butter and every plant milk were reported as containing milk,
# hard-capping the score at 15 for a milk-allergic user.
_milk, _ = _build_allergen_synonyms("milk")
for _ing in ["peanut butter", "cocoa butter", "shea butter", "almond milk",
             "coconut milk", "soy milk", "oat milk", "cashew cream", "vegan cheese"]:
    check(f"milk allergy vs {_ing!r}", _text_contains_allergen(_ing, _milk), False)
for _ing in ["butter", "buttermilk", "milk solids", "skimmed milk powder",
             "butter oil", "cream", "milk chocolate", "condensed milk"]:
    check(f"milk allergy vs {_ing!r}", _text_contains_allergen(_ing, _milk), True)

print("\n=== dietary pattern is a hard constraint ===")
def _diet(pattern, ingredients):
    r = calculate_suitability(
        NUT, [], [{"name": n, "position": i + 1} for i, n in enumerate(ingredients)],
        [{"goal_type": "general health", "priority": 0}], [], [], {},
        dietary_pattern=pattern)
    return r

# Ruled out on composition, however well it scores nutritionally.
for _pattern, _ings, _compatible in [
    ("vegan", ["oats", "whey protein"], False),
    ("vegan", ["oats", "honey"], False),
    ("vegan", ["oats", "gelatin"], False),
    ("vegan", ["oats", "almond milk", "cocoa butter"], True),
    ("vegetarian", ["oats", "whey protein"], True),      # dairy is fine
    ("vegetarian", ["oats", "egg white"], False),
    ("vegetarian", ["oats", "chicken fat"], False),
    ("eggetarian", ["oats", "egg white"], True),         # eggs are the point
    ("eggetarian", ["oats", "beef extract"], False),
    ("pescatarian", ["oats", "salmon oil"], True),
    ("pescatarian", ["oats", "bacon"], False),
    ("omnivore", ["oats", "gelatin"], True),
    ("keto", ["oats", "gelatin"], True),                 # macro target, not exclusion
    (None, ["oats", "gelatin"], True),
    ("vegan", ["oats", "coconut meat"], True),           # a plant
]:
    check(f"{_pattern} + {_ings[1]}", _diet(_pattern, _ings).diet_compatible, _compatible)

check("incompatible product is hard-capped",
      _diet("vegan", ["oats", "gelatin"]).overall_score <= 15, True)
check("compatible product is not penalised",
      _diet("vegan", ["oats", "almond milk"]).overall_score > 70, True)
check("verdict names the pattern",
      "Vegan" in _diet("vegan", ["oats", "gelatin"]).verdict, True)
check("breakdown reports the conflict",
      _diet("vegan", ["oats", "gelatin"]).breakdown["diet_conflict"], "incompatible")

# An ambiguous ingredient warns without changing the number — a guess that
# lowers a score reads as a finding.
_amb = _diet("vegan", ["oats", "mono and diglycerides"])
check("ambiguous origin -> uncertain", _amb.breakdown["diet_conflict"], "uncertain")
check("ambiguous origin does not cap", _amb.diet_compatible, True)
check("ambiguous origin scores the same as a clean product",
      _amb.overall_score, _diet("vegan", ["oats"]).overall_score)

# Allergen safety and diet compatibility stay separate signals.
_both = calculate_suitability(
    NUT, [], [{"name": "whey protein", "position": 1}],
    [{"goal_type": "general health", "priority": 0}],
    [{"allergen": "milk", "allergy_type": "allergy", "severity": "severe"}], [], {},
    dietary_pattern="vegan")
check("allergen conflict and diet conflict coexist",
      (_both.allergen_safe, _both.diet_compatible), (False, False))
check("allergen verdict leads when both apply", "allergen" in _both.verdict.lower(), True)

print("\n=== nutrient preferences are soft, until marked strict ===")
# A high-sugar, high-sodium, high-protein snack.
_PN = {"energy_kcal": 400, "protein_g": 18, "total_sugars_g": 30, "sodium_mg": 700,
       "saturated_fat_g": 2, "fiber_g": 1, "total_fat_g": 10}
def _pref(prefs):
    return calculate_suitability(
        _PN, [], [{"name": "oats", "position": 1}],
        [{"goal_type": "general health", "priority": 0}], [], prefs, {})

_none = _pref([])
_violated = _pref([{"preference_type": "low_sugar", "is_hard_constraint": False}])
_met = _pref([{"preference_type": "high_protein", "is_hard_constraint": False}])
check("a violated preference lowers the score", _violated.overall_score < _none.overall_score, True)
check("a met preference raises it", _met.overall_score > _none.overall_score, True)
check("a violated preference does NOT block", _violated.overall_score > 25, True)
check("violated -> soft", _violated.breakdown["preference_conflict"], "soft")
check("met -> none", _met.breakdown["preference_conflict"], "none")

_strict = _pref([{"preference_type": "low_sugar", "is_hard_constraint": True}])
check("strict + violated -> strict", _strict.breakdown["preference_conflict"], "strict")
check("strict + violated is capped", _strict.overall_score <= 25, True)
check("strict + met is not penalised",
      _pref([{"preference_type": "high_protein", "is_hard_constraint": True}]).overall_score,
      _met.overall_score)

# Several preferences must not be able to swamp the assessment beneath them.
_many = _pref([{"preference_type": k, "is_hard_constraint": False} for k in
               ("low_sugar", "low_sodium", "low_fat", "low_saturated_fat", "high_fiber")])
check("total preference swing is capped at 15",
      abs(_many.breakdown["preference_adjustment"]) <= 15, True)

# A duplicate would be counted twice; the engine de-duplicates defensively.
_dupe = _pref([{"preference_type": "low_sugar", "is_hard_constraint": False}] * 3)
check("duplicate preferences count once",
      _dupe.breakdown["preference_adjustment"], _violated.breakdown["preference_adjustment"])

# An unmeasurable preference must not be scored as satisfied.
_no_nutrient = calculate_suitability(
    {"energy_kcal": 100}, [], [], [{"goal_type": "general health", "priority": 0}], [],
    [{"preference_type": "low_sodium", "is_hard_constraint": False}], {})
check("unmeasurable preference is skipped, not rewarded",
      _no_nutrient.breakdown["preference_adjustment"], 0)

check("aliases resolve", _resolve_preference_key("Low Salt"), "low_sodium")
check("canonical keys pass through", _resolve_preference_key("high_protein"), "high_protein")

# A hard constraint the user set on themselves must not outrank a safety one.
_allergen_and_pref = calculate_suitability(
    _PN, [{"allergen": "milk", "certainty": "declared"}], [{"name": "whey", "position": 1}],
    [{"goal_type": "general health", "priority": 0}],
    [{"allergen": "milk", "allergy_type": "allergy", "severity": "severe"}],
    [{"preference_type": "low_sugar", "is_hard_constraint": True}], {})
check("allergen cap still wins over a strict preference",
      _allergen_and_pref.overall_score <= 15, True)

print("\n=== gateway output coercion ===")
from app.ai.gateway import _coerce_str_list
check("string -> list", _coerce_str_list("one insight"), ["one insight"])
check("None -> []", _coerce_str_list(None), [])
check("list of dicts", _coerce_str_list([{"insight":"a"},{"insight":"b"}]), ["a","b"])
check("nulls stripped", _coerce_str_list(["a",None,"","b"]), ["a","b"])

print("\n=== fallback recommendations also guard the unscored goal ===")
# The 'incomplete' feedback branch builds its own per-goal lines. It used to
# skip the score=None guard the analysis section applies, emitting
# "scored None/100 (not_evaluated alignment)".
_incomplete = [{"rating": 2, "feedback_type": "incomplete", "comment": ""},
               {"rating": 2, "feedback_type": "incomplete", "comment": ""}]
_rep2 = _generate_fallback("X", _r.overall_score, _r.verdict, _r.allergen_safe,
                           _fl, _gas, _incomplete)
_recs = " | ".join(_rep2.recommendations)
check("no 'None/100' in recommendations", "None/100" in _recs, False)
check("unscored goal shown as not assessed", "not assessed" in _recs, True)

print("\n=== nutrition figures are per 100g, and readable ===")
from app.personalization.engine import _fmt, _calculate_nutritional_quality
# Source values carry full float precision; a label figure must not read as
# "55.4545454545455g".
check("long float rounded", _fmt(55.4545454545455), "55.5")
check("whole number stays whole", _fmt(12.0), "12")
check("one decimal preserved", _fmt(0.5), "0.5")
check("int passes through", _fmt(3), "3")
_, _nflags = _calculate_nutritional_quality(
    {'total_sugars_g': 55.4545454545455, 'saturated_fat_g': 17.2727272727273,
     'sodium_mg': 123.456, 'protein_g': 20.0, 'fiber_g': 5.0})
_ntext = " | ".join(f.description for f in _nflags)
# Values are ingested from Open Food Facts' *_100g fields and the thresholds are
# calibrated on that basis, so the copy must not call them servings.
check("no 'per serving' claim", "per serving" in _ntext, False)
check("states the per-100g basis", "per 100g" in _ntext, True)
check("no runaway precision", "55.4545" in _ntext, False)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
