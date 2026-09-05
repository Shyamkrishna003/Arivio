# ARIVIO — Look Beyond the Label

> AI-Powered Personalized Product Intelligence Platform

## Overview

ARIVIO helps users understand whether a consumer product is appropriate for **their individual context** by analyzing product composition, ingredients, nutrition, allergens, scientific evidence, and community experiences.

The core of the system is a **personalization engine** that turns a product's label into a single 0–100 Personal Suitability Score, explained by structured flags rather than presented as a black box. See [How Scoring Works](#how-scoring-works).

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | React 18, TypeScript, Vite, React Router v6, Redux Toolkit, TanStack Query |
| **Backend** | Python 3.11+, FastAPI, SQLAlchemy 2.0 (async), Alembic |
| **Database** | PostgreSQL 16 + pgvector |
| **Cache** | Redis 7 |
| **Auth** | JWT (access + refresh tokens) |
| **AI** | Provider-independent gateway — Groq, Google Gemini, or OpenAI, with a rule-based fallback |

## Quick Start

### Prerequisites

- Docker & Docker Compose (Option A needs nothing else)
- Node.js 18+ (Option B)
- Python 3.11+ (Option B)

### Option A — Everything in Docker

```bash
cp backend/.env.example backend/.env   # add AI_API_KEY if you have one
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6379` |

Migrations run automatically on backend start. Both source trees are
bind-mounted, so uvicorn `--reload` and Vite HMR pick up edits without a
rebuild — rebuild only when `requirements.txt` or `package.json` changes:

```bash
docker compose up --build backend    # after a dependency change
docker compose logs -f backend       # follow logs
docker compose down -v               # stop and wipe volumes
```

Notes on the compose setup:

- Inside the network, services resolve each other by name (`postgres:5432`,
  `redis:6379`). `VITE_API_URL` instead points at `localhost:8000`, because it
  is resolved by your **browser**, not by the frontend container.
- `backend/.env` is loaded if present but the compose `environment` block wins,
  so a local `DATABASE_URL` pointing at `localhost` cannot break the container.
- The frontend serves plain HTTP in Docker (`VITE_HTTPS=false`). Vite's dev
  server otherwise uses a self-signed certificate, and an HTTPS page cannot call
  the HTTP backend without the browser blocking it as mixed content.
- Ports 5173 and 8000 must be free — stop any local `npm run dev` / `uvicorn`
  first.
- `backend/uploads/` (label photos) is bind-mounted and the container runs as
  root, so a directory first created under Docker is **root-owned** and a
  later local `uvicorn` cannot write to it. Label scanning then answers `503`.
  Fix it once with:

  ```bash
  sudo chown -R "$USER" backend/uploads
  ```

### Option B — Run services locally

Start only the infrastructure:

```bash
docker compose up -d postgres redis
```

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API docs available at: http://localhost:8000/docs

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

App available at: http://localhost:5173

### Configuration

`backend/.env` (git-ignored — never commit real keys):

```bash
DATABASE_URL=postgresql+asyncpg://arivio:arivio_dev@localhost:5433/arivio
SECRET_KEY=change-me-in-production

# AI is optional. Without a key the system still works — reports become
# rule-based and custom goals cannot be created.
AI_PROVIDER=groq            # groq | gemini | openai
AI_MODEL=openai/gpt-oss-20b
AI_API_KEY=...
```

| Setting | Default | Notes |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Short-lived; auto-refreshed by the client |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Session length before re-login |

---

## How Scoring Works

`GET /api/v1/personalization/suitability/{product_id}` runs a four-stage pipeline in
`backend/app/personalization/engine.py`. The engine is **pure and synchronous** —
it performs no I/O. Routers gather data, call it, and serialize the result, which
keeps it fully testable without a database or network.

```mermaid
flowchart TD
    A[Product label + user profile] --> B[1. Allergen check]
    B --> B2[1b. Dietary pattern]
    B2 --> C[2. Goal alignment]
    C --> D[3. Nutritional quality]
    D --> E[4. Ingredient profile]
    E --> E2[5. Nutrient preferences]
    E2 --> F[Weighted composite]
    F --> G{Allergen conflict?}
    G -- confirmed --> H[Hard cap 15 / 30]
    G -- trace --> I[Cap 45, x0.6]
    G -- preference --> J[x0.9 - 5]
    G -- none --> K[Score stands]
    H & I & J & K --> M{Diet incompatible?}
    M -- yes --> N[Hard cap 15]
    M -- no --> L[Score + verdict + flags]
    N --> L
```

### 1. Allergen check — three resolution tiers

A declared allergen is resolved through progressively weaker methods, and the
system always reports which tier answered.

**Tier 1 — synonym groups.** Every term names the *same* allergen, so declaring
any one pulls in all of them.

```python
ALLERGEN_SYNONYMS = {
    "milk": ["milk", "dairy", "lactose", "casein", "whey", "cream", "butter", ...],
    "peanuts": ["peanut", "groundnut", "arachis", ...],
    # + soy, eggs, sesame, mustard, celery, sulphites
}
```

**Tier 2 — category groups.** Members are *distinct* allergens under an umbrella.
The umbrella matches every member; a single member matches only itself — a cashew
allergy does not imply an almond allergy.

```python
ALLERGEN_CATEGORIES = {
    "tree nuts": ["almond", "cashew", "walnut", "pistachio", ...],
    "gluten":    ["gluten", "wheat", "barley", "rye", "oats", ...],
    "fish":      ["fish", "cod", "salmon", "tuna", ...],
    "shellfish": ["shrimp", "prawn", "crab", "lobster", ...],
}
```

Matching is length-aware: terms of **5+ characters** match as substrings (so
`butter` catches `buttermilk`), while shorter terms require **word boundaries**
(so `egg` does not match `eggplant`, and `cod` does not match `cocoa`).

Dairy words are the exception, because substring matching gets them badly
wrong: `butter` matched `peanut butter`, `cocoa butter` and `shea butter`, and
`milk` matched every plant milk — each one capping the score at 15 and telling a
milk-allergic user that almond milk contains milk. `butter`, `milk`, `cream`,
`cheese` and `yog(h)urt` are therefore ignored when preceded by a plant source
(`almond`, `coconut`, `soy`, `oat`, `cocoa`, `shea`, …). A bare or compound
occurrence still counts, so `buttermilk`, `milk solids` and `butter oil` are
unaffected.

Free-text input falls back to token matching — `"Sesame Seeds"` resolves via the
`sesame` token. Bigrams are tried before single words so `"brazil nut"` matches
that specific nut rather than the whole tree-nut category, and the **first hit
wins** so `"peanut butter"` resolves to peanuts without dragging in dairy.

**Tier 3 — AI inference.** For allergens with *no* synonym coverage **and** no
literal hit, an LLM reads the ingredient list. This catches spelling variants
(`dragonfruit` / `dragon fruit`), translations, E-numbers, and derived
ingredients (`copra` → coconut, `semolina` → wheat).

Three safety rules are enforced:

1. Inference runs **only** when string matching found nothing.
2. It can **add** a conflict, never clear one — a literal match always stands, so
   a model error cannot make an unsafe product look safe.
3. A "not found" verdict is **never** presented as proof of safety; the user is
   told the check was AI-based and to verify the packaging.

Model certainty maps to conflict severity, and results are attributed in the flag
text along with the ingredient that triggered them.

#### Conflict tiers

`allergy_type` and `severity` both affect the outcome:

| Conflict | Trigger | Score | `allergen_safe` |
|---|---|---|---|
| `confirmed` | declared allergen, named ingredient, or high-certainty AI match | `min(15, base)` — `30` if *all* conflicts are mild | `False` |
| `trace` | "may contain", or medium/low-certainty AI match | `min(45, base × 0.6)` | `False` |
| `preference` | any match where `allergy_type = "preference"` | `base × 0.9 − 5` | `True` |
| `none` | no match | `base` | `True` |

A soft preference lowers the score but keeps the product recommendable. Severity
is collected across **every** conflict at the worst level, so the cap only
softens when all of them are mild — the result never depends on the order
allergies were added.

### 1b. Dietary pattern — the second hard constraint

A declared dietary pattern rules a product out on composition, the same way an
allergen does. A vegan product containing gelatin is not "slightly less
suitable" — it does not qualify — so an incompatible product is **capped at 15**
regardless of how well it scores nutritionally.

| Pattern | Excludes |
|---|---|
| `vegan` | meat, fish, shellfish, dairy, egg, animal-derived, bee products |
| `vegetarian` | meat, fish, shellfish, egg, animal-derived |
| `eggetarian` | meat, fish, shellfish, animal-derived |
| `pescatarian` | meat, animal-derived |
| `omnivore`, `other` | nothing |

Dairy, egg, fish and shellfish reuse the allergen tables rather than duplicating
them, so a term added for allergen coverage improves diet matching for free.

Two deliberate decisions:

- **The vegetarian/eggetarian split follows Indian usage**, which is what this
  catalogue is built around (the ingredient tables carry ghee, paneer, maida,
  vanaspati). `vegetarian` excludes eggs; `eggetarian` is the pattern that
  permits them. Western lacto-ovo vegetarians should select eggetarian.
- **`keto` is not treated here.** It is a macronutrient target, not an
  ingredient-exclusion list, and belongs in goal alignment where carbohydrate
  thresholds already live. Excluding foods on that basis would be the wrong
  mechanism entirely.

Genuinely ambiguous ingredients — `mono- and diglycerides`, `glycerin`, `E471`,
`rennet`, `collagen` — raise a **"check the label"** warning and deliberately
**do not change the score**. They are plant-derived more often than not, and a
guess that lowers a score reads as a finding.

`diet_compatible` is reported separately from `allergen_safe`: one is a
compatibility question, the other a safety one, and a caller filtering for
allergen safety should not silently inherit dietary filtering. Both can be false
at once, in which case the allergen verdict leads. The UI shows a compatibility
issue as its own banner rather than leaving it to be inferred from the number.

### 2. Goal alignment — three resolution tiers

Goals are free text, resolved in order:

1. **Hardcoded profiles** — `weight loss`, `muscle gain`, `heart health`,
   `diabetes management`, `general health`, `energy boost`.
2. **Aliases** (31 of them) map natural phrasing onto those keys:
   `muscle_building → muscle gain`, `blood sugar control → diabetes management`.
3. **AI-generated custom profiles** for anything else, produced once per goal and
   stored in `custom_goal_profiles`.

Each profile declares `prefer_low` / `prefer_high` nutrients with `good` and
`bad` thresholds per 100g. Values are linearly interpolated to 0–100 and combined
by per-nutrient weight:

```python
# prefer_low: at or below "good" scores 100, at or above "bad" scores 0
ratio = (value - good) / (bad - good)
score = int((1 - ratio) * 100)
```

Multiple goals are averaged by `priority`, weighted `1.0 + max(0, priority) × 0.5`.

**The same goal cannot be active twice.** Because goals are combined as a
weighted average, a duplicate votes twice and quietly skews every score — adding
`muscle gain`, `bulking` and `muscle building` alongside `diabetes management`
moved a high-sugar bar from **57 → 65**. `POST /profile/goals` therefore returns
`409` when a goal resolves to a profile the user already has active, comparing
*after* alias resolution so `weight loss` and `weight management` are recognised
as one goal. A partial unique index (`uq_user_goal_active`) backs this at the
database level for exact repeats; removing a goal still lets you re-add it.

#### The sanity gate

Because goals are free text, users can enter things that are not scoreable
("no muscle", "sleep better"). The profile generator is allowed to **decline**:

```json
{"valid": false, "reason": "This goal is nutritionally harmful and cannot be safely scored."}
```

It rejects non-nutrition goals, goals not expressible as nutrient targets, and
eating-disorder patterns. The prompt states plainly that *answering a nonsensical
goal with plausible-looking numbers is worse than refusing*.

Every goal therefore carries a status, surfaced in the UI:

| `profile_status` | Meaning |
|---|---|
| `ready` | Scoreable — hardcoded or custom profile exists |
| `pending` | Profile being generated in the background |
| `unsupported` | Declined, with a user-facing `status_message` |

**An unscoreable goal does not vote.** It returns `score: null` with
`alignment: "not_evaluated"` rather than a neutral 50 — a placeholder number
reads as a real verdict and would inflate scores for products the user's goals
would have penalised.

### 3. Nutritional quality

A goal-independent 0–100 index built from an additive baseline of 60, adjusted by
sugar, sodium, saturated fat, protein and fibre thresholds. Each adjustment emits
a flag carrying its own impact, so the number is fully explainable.

**Credit for absence has to be earned.** Of the available bonuses, 32 points were
for what a product does *not* contain and only 24 for what it does — free money
for an empty product. Diet cola scored **92**: no sugar, no sodium, no saturated
fat, nothing at all. Plain yoghurt scored **88**, because yoghurt has 2.1g of
saturated fat and forfeited a bonus the cola kept by being water and sweetener.

So a product that supplies **no protein, no fibre and no meaningful
micronutrient** earns nothing for lacking the bad things either. It sits at the
neutral baseline, which is what "contributes nothing" should look like:

```
diet cola          92 → 60      sugar-free jelly   92 → 60
sparkling water    92 → 60      plain yoghurt      88 → 88   (now clearly ahead)
lentils, almonds, bread, milk — all unchanged
```

"Supplies something" uses the **EU definition of a *source of* a nutrient** — 15%
of the Nutrition Reference Value per 100g — rather than a threshold we invented.
A fortified drink with 30mg of vitamin C keeps its credit.

Three things it deliberately does not do:

- **Missing data is not emptiness.** `_provides_nutrition` returns `None` when
  protein and fibre were never recorded, and the bonuses stand. Absence of
  evidence is not evidence of absence.
- **A cooking oil is exempt**, because it has no protein or fibre *by nature* —
  the same distinction category awareness draws above. Penalising it here would
  undo that.
- **No NOVA classification was needed.** The processing signal already exists in
  the ingredient profile, which separates diet cola (52) from sparkling water
  (86) on additives. Formal NOVA is still the right next step if you want
  processing to carry more weight, but it is not what made this bug.

**A disqualifying nutrient caps the score rather than deducting from it.**
Additive scoring reads fairly across the normal range and fails at the extremes,
because one catastrophic nutrient gets outvoted by several mild positives. Soy
sauce was the case that proved it: 5,493mg of sodium per 100g — nine times the
"high" line — cost it 12 points, which low sugar, low saturated fat and some
protein more than repaid. It scored **73 overall and 85 against a weight-loss
goal**, ahead of olive oil.

So a nutrient at 2× the high line caps the score, and 3× caps it harder:

| | Nutritional quality | Overall |
|---|---|---|
| Severe (≥2×) | 40 | 55 |
| Extreme (≥3×) | 25 | 35 |

Capping *overall*, not just this component, is the part that matters: a goal
profile only scores the nutrients it names, so a weight-loss goal never looks at
sodium at all. This is also how every other disqualifying finding already works
— allergen conflicts, incompatible dietary patterns and violated strict
preferences all cap rather than subtract.

Only nutrients whose extremes are bad **regardless of what the food is** qualify:
sodium, sugar and trans fat. Saturated fat is deliberately excluded — 14g/100g is
extreme for a biscuit and unremarkable for olive oil, and telling those apart
needs category awareness the engine does not have yet. Capping on it would push
olive oil further down when it is already scored too harshly.

```
soy sauce            73 → 35     (85 → 35 against a weight-loss goal)
honey                    35      extreme, 3.6× the sugar line
milk chocolate           55      severe, 2.5×
olive oil            58 → 58     unchanged — not a capping nutrient
white bread          86 → 86     unchanged — 490mg sodium is under the line
lentils              98 → 98     unchanged
```

The cap is reported in `breakdown.nutrient_extreme` (`none` / `severe` /
`extreme`) and explained in a flag that states how far past the line the value
is. When a nutrient is capped, the additive scoring drops its own warning for
that same nutrient so the user sees one finding rather than two.

> **All nutrition figures are per 100g, not per serving.** Open Food Facts is
> ingested from its `*_100g` fields and every threshold in the engine is
> calibrated on that basis. `products.serving_size` is descriptive only — the
> stored numbers are never scaled to it. Flags say "per 100g" for this reason,
> and values are rounded to one decimal place so a converted figure reads as
> `55.5g` rather than `55.4545454545455g`.

### 3b. Category awareness

Every threshold above is written for a food you eat by the plate. Applied
unchanged to a cooking oil or a soft drink they mislead, because the same number
means something different depending on what the product is.

Extra virgin olive oil scored **58 — below white bread at 86**. Its goal
alignment was 40, and the arithmetic shows why:

```
sugar    0g   → 100 ✓
sodium   2mg  → 100 ✓
sat fat  14g  →   0 ✗   judged on a threshold meant for foods
protein  0g   →   0 ✗   an oil has no protein
fibre    0g   →   0 ✗   an oil has no fibre
                ────
                 40
```

Two of those zeros are not failures. **Oil contains no protein or fibre by
nature**, and marking it down for that is like failing a fish for not climbing.
The engine already skips a nutrient it has no data for — it just couldn't tell
*not measured* from *not applicable*.

`CATEGORY_PROFILES` in `app/personalization/categories.py` declares two things
per category:

- **`not_applicable`** — nutrients that carry no information for this food type,
  mapped to `None` so every existing scorer skips them.
- **`derived` + `thresholds`** — a more meaningful basis for a nutrient that
  does matter. For oils, absolute saturated fat says little (every oil is nearly
  all fat); the **share of that fat which is saturated** says a lot. Olive oil is
  14%, butter 63%, coconut oil 87%.

Resolved once and applied to goal alignment, nutritional quality, preferences
and the disqualifying-nutrient check alike, so every scorer judges the product
as the same kind of thing.

```
olive oil        58 → 91     coconut oil      58     regular cola  61 (vs 65 as a food)
butter                60     peanut butter    84     everything else unchanged
```

Scope is deliberately narrow: only **beverages** and **added fats**, the two
categories with a demonstrated failure here — and the same two Nutri-Score
singles out for special handling. Cheese and nuts are the usual next candidates
and the framework takes them unchanged, but nothing in this catalogue currently
mis-scores because of them, so they are not guessed at.

A category may also declare **how much of it a person actually uses** — see
below.

#### Resolution is guarded three ways

Open Food Facts categories are user-contributed free text — this catalogue holds
`Bonbons de chocolat`, `Polvos de proteína`, `en:Confectionary based spreads`,
and one product in seven has no category at all. A wrong profile changes which
nutrients are scored, so:

1. **Exclusions** — `nut butter` is not a cooking fat; `foods and drinks` is not
   a drink.
2. **Whole-word matching** — both false positives found in testing were
   substrings of ordinary words: `cola` inside "cho**cola**te biscuits", and
   inside "cho**cola**t". A bare `in` test scored a chocolate biscuit on the
   drinks scale.
3. **A composition guard** — a product claiming to be a cooking fat must look
   like one (≥50g fat, ≤5g protein). Peanut butter filed under `Nut butters` has
   25g of protein and is correctly refused.

An unresolved category returns `None`, meaning "score it the ordinary way" —
which is the common path and has to be harmless. Reported in
`breakdown.category`.

### 3c. Portion realism

Every threshold is per 100g, which is right for a food you eat by the plate and
meaningless for one you use by the teaspoon. Ground cinnamon scored **93** — above
white bread, near lentils — on the figures for 100g of it. Nobody eats 100g of
cinnamon.

| Category | Realistic portion |
|---|---|
| Spice or seasoning | 2g |
| Sauce or condiment | 15g |
| Cooking fat or oil | 14g |
| Drink | 250g |

**These are ours, per category — deliberately not the label's `serving_size`.**
A declared serving is set by the manufacturer and is the classic thing to shrink
when the numbers look bad; per-100g labelling is mandated precisely because of
that. A category-typical amount can't be gamed by the product being scored.

Where a portion is known, the disqualifying-nutrient check asks **what one
serving delivers as a share of the daily reference** instead of how large the
per-100g figure is. That changes the question from "is this number big?" to "how
much of this are you getting when you use the thing":

```
soy sauce    a tablespoon carries 824mg sodium — 41% of a day  → still capped
table salt   2g carries 776mg — 39% of a day                   → still capped
ketchup      a tablespoon carries 136mg — 7%                   → not capped
regular cola a 250ml glass carries 26g sugar — 29% of a day    → newly capped
cinnamon     half a teaspoon carries ~nothing                  → held near neutral
```

The rule cuts both ways, which is the point. It is **not a loophole**: salt and
soy sauce stay capped because a realistic amount genuinely does deliver a large
share of a day's sodium. And it newly catches drinks, because a glass is 250ml —
a per-100ml figure understates what you actually drink.

A product whose realistic portion contributes under 2% of everything gets its
score **held below 70** with an explanation. Half a teaspoon of cinnamon delivers
hundredths of a percent of a day's sugar, salt and fat; 93/100 read as a dietary
endorsement of something that is neither good nor bad for you in the amount you
use.

That is a **ceiling only, never a floor.** An earlier version applied a neutral
minimum too, which would have lifted an allergen-capped 15 to 45. Every cap in
the engine exists to hold a score *down*, and nothing may undo one — a negligible
portion is a reason not to praise a product, never a reason to reassure someone
about it.

Two calibration details worth knowing:

- **Total sugars are judged against 90g** (the EU Reference Intake), not the WHO
  50g free-sugars limit, because `total_sugars_g` includes the lactose in milk
  and the fructose in fruit. The stricter figure would mark plain milk down like
  a soft drink — the test suite checks it doesn't.
- **Bulk sauces are excluded** from the 15g condiment portion. Pasta sauce, curry
  sauce and soup are eaten by the plateful whatever the word "sauce" suggests.

Reported in `breakdown.reference_portion_g` — null means the per-100g basis was
used unchanged, which is the common case.

### 4. Ingredient profile

Ingredient **count** is a poor proxy for quality — `sugar, palm oil` is two
ingredients and not a wholesome product. Count sets only a narrow base (78 → 45),
and composition dominates:

- **Penalised:** refined sugars, refined/hydrogenated fats, additives, E-numbers
  (regex `\be\s?\d{3}\b`)
- **Rewarded:** whole grains, nuts, seeds, legumes, fruit, vegetables

Labels are ordered by quantity, so position is weighted **3× / 2× / 1×** for the
first three, next three, and remainder. Penalties cap at 45 and bonuses at 25.

| Ingredients | Count-only | Quality-aware |
|---|---|---|
| `sugar, glucose syrup, palm oil, artificial flavour, E102` | 90 | **33** |
| `whole wheat flour, almonds, oats, raisins, cinnamon` | 90 | **100** |
| `sugar, palm oil, whole oats, almonds` | 90 | **66** |
| `whole oats, almonds, dates, water, salt, soy lecithin, E322` | 80 | **93** |

The last two rows contain similar ingredients in opposite order — position
weighting is what separates them.

### 5. Nutrient preferences — soft, unless marked strict

A preference sits between a goal and a hard constraint. A goal is a whole
nutritional profile (`heart health` also weighs saturated fat, total fat and
cholesterol); a preference is **one nutrient** the user asked us to watch.
Without these, the only way to say "I want low sodium" was to adopt a goal that
drags in four other thresholds.

| Preference | Nutrient | Good | Bad |
|---|---|---|---|
| `low_sugar` | `total_sugars_g` | <= 5g | >= 22.5g |
| `low_sodium` | `sodium_mg` | <= 120mg | >= 600mg |
| `low_fat` | `total_fat_g` | <= 3g | >= 17.5g |
| `low_saturated_fat` | `saturated_fat_g` | <= 1.5g | >= 5g |
| `high_protein` | `protein_g` | >= 15g | <= 3g |
| `high_fiber` | `fiber_g` | >= 6g | <= 1.5g |

Thresholds follow front-of-pack traffic-light bands per 100g. Each preference
contributes **+6 when satisfied, -8 when violated**, interpolated between —
asymmetric because failing what the user explicitly asked for matters more than
meeting it. The total swing is capped at **+/-15** so a handful of preferences
cannot swamp the nutritional assessment underneath.

```text
no preferences                 52
low sugar (violated)           44   -8   soft
+ high protein (met)           50   -2   soft
low sugar marked strict        25        strict  <- capped
```

**Strict** escalates a preference to a hard constraint, capping the score at
**25** — deliberately above the allergen (15) and dietary (15) ceilings, so the
three remain distinguishable by score alone and a self-imposed limit never
outranks a safety one.

Two rules keep this honest:

- A preference whose nutrient is **absent from the label is skipped**, never
  counted as satisfied — missing data must not earn a bonus.
- `preference_type` is validated against the table above. Free text used to be
  accepted and then silently ignored at scoring time, which is worse than
  refusing it: the user believes a preference is being applied when it is not.

### Composite score

```python
base = goal × 0.50 + nutritional_quality × 0.30 + ingredient_profile × 0.20
```

When **no goal could be evaluated**, the goal term is dropped and the remainder
renormalized — deliberately *not* to a straight 3:2 split, because ingredient
profile is the weaker signal and doubling its weight would reward a
two-ingredient candy:

```python
base = nutritional_quality × 0.75 + ingredient_profile × 0.25
```

`breakdown.weights` always reports the weights actually applied, and
`breakdown.goal_alignment` is `null` in this case.

### Confidence

Derived from how much real data was available, not from the score:

```python
confidence = 25
confidence += min(35, non_null_nutrient_count * 5)
confidence += min(15, len(ingredients) * 3)
confidence += 10 if product_allergens else 0
confidence += 10 if any_goal_matched else 0
confidence -= 5  if not user_goals else 0
confidence = max(20, min(95, confidence))
```

---

## Request Flow

Viewing a product end to end:

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend
    participant API as FastAPI
    participant DB as PostgreSQL
    participant AI as AI Gateway

    U->>F: Scan barcode
    F->>API: POST /products/scan
    API-->>F: Product (immediate)
    API-)AI: prime allergen cache (background)
    F->>API: GET /personalization/suitability/{product_id}
    API->>DB: product + profile + cached verdicts
    API->>API: calculate_suitability()
    API-->>F: score, verdict, flags, breakdown
    F->>API: GET /ai/report/{product_id}
    API->>AI: prompt with scores + past feedback
    AI-->>F: narrative report
```

**Scan-time priming.** `POST /products/scan` accepts an optional bearer token
(`get_optional_user`, which returns `None` instead of 401). For a signed-in user
it queues a background task that warms the allergen inference cache, so the
suitability request that follows is served from cache — measured **1.2s → 0.02s**.
Anonymous scans are unaffected. Priming runs both for a product already on file
and for one just imported from Open Food Facts.

**Unknown barcode.** A scan that matches nothing locally and nothing in Open Food
Facts returns `404`, and the frontend routes to `/products/submit` with the
barcode prefilled. The same page backs the "Submit this Product" action on an
empty search. Ingredients entered there are parsed into ordered rows, so a
submitted product is immediately scoreable rather than an empty shell.

The frontend fetches suitability **before** the AI report, so inference is always
cached by report time and the report costs exactly one model call.

---

## AI Integration

### Provider-independent gateway

`backend/app/ai/gateway.py` targets Groq, Gemini or OpenAI through one
OpenAI-compatible interface, degrading gracefully:

1. No `AI_API_KEY` → rule-based report
2. Provider call fails → rule-based report
3. Malformed JSON → coerced (`_coerce_str_list`) or rule-based

The rule-based fallback is a real report built from the engine's flags and goal
alignments, not an error message. Reports run at `temperature=0.4`; allergen
inference at `0.1`, since label reading is factual rather than creative.

### Allergen inference cache

Verdicts are cached in `allergen_inferences`, keyed on everything that
determines the answer:

| Column | Role |
|---|---|
| `allergen`, `product_id` | what was asked |
| `model`, `prompt_version` | **in the unique key** — verdicts from different models coexist, so switching `AI_MODEL` and back is free |
| `ingredients_hash` | **read filter only** — a changed label *replaces* the stale verdict via upsert rather than accumulating beside it |

`ingredients_fingerprint()` normalizes, deduplicates and sorts ingredient names
plus declared allergens before hashing, so re-scraping a label with different
casing or ordering does not needlessly invalidate a verdict — only a genuine
change of contents does.

Bump `PROMPT_VERSION` in `app/allergens/inference.py` when the prompt changes
materially; every cached verdict then recomputes.

`GET /personalization/alternatives/{id}` scores up to 50 candidates and therefore
calls `resolve_allergens(..., allow_ai=False)` — **cache reads only**, never a
model call per candidate.

**An alternative must actually beat the product you are looking at.** The
endpoint scores the original product with the same engine and then keeps only
candidates that are allergen-safe, clear a quality floor of 70, **and** score
strictly higher than the original. A flat threshold alone would let a product
scoring 71 be offered as an upgrade on one scoring 84, contradicting the
"these products scored higher" claim the UI makes.

### Feedback-conditioned reports

`ReportFeedback` rows (rating 1–5, type, optional comment) are injected into the
report prompt — the last 8, **including rating-only rows**, since a low rating
with no comment is still signal. An average of ≤ 2.5 escalates the instruction to
change approach materially.

> **Note:** this conditions the *narrative only*. The suitability score is fully
> deterministic and is never influenced by feedback.

---

## Community Experiences

Real-world reports of what happened after using a product, kept deliberately
separate from the label-derived score above it. Three rules from the product
requirements shape the implementation.

### It starts empty, and says so

Nothing is seeded, simulated or backfilled. A product with no reports says it
has none rather than implying a consensus, and below five reports the UI shows
raw counts instead of percentages — "100% reported headaches" off a single
review reads as a finding rather than one person's account.

### People Like You, not most-liked

The differentiating feature is not a popularity list. Each experience carries
the anonymised context its author consented to share, scored against the
reader's own profile by `app/community/similarity.py` — deterministic, no model
call, so the same pair always yields the same explanation.

| Dimension | Weight |
|---|---|
| Health context | 30% *(specified, no field to store it yet)* |
| Allergy / intolerance | 25% |
| Dietary pattern | 15% |
| Goal | 10% |
| Activity level | 10% |
| Age range | 5% |
| Usage duration | 5% |

Age range, dietary pattern and activity level are collected by the **About You**
panel on the profile page, which reads its options from `/profile/options` so the
dropdowns cannot drift from what the API validates — or from the ordered scale
relevance compares age bands on.

Only dimensions **both** sides disclosed are scored, and the weights are
renormalised over those — a reader who shared little is not penalised for what
they withheld; the match simply rests on less, and the untouched dimensions are
reported as "not compared". Goals are compared after alias resolution, so
`bulking` matches `muscle gain`. Ordinal dimensions give partial credit to
adjacent bands: `very active` and `moderately active` are closer than `very
active` and `sedentary`.

A match needs both a score above `0.35` **and** at least two agreeing
dimensions. One coincidence is not a similar profile.

**Relevance is always explained.** A bare "Similarity: 87%" is not an
acceptable answer, so every match ships the dimensions that agreed *and* the
ones that differ — the differences are never trimmed to make a match look
stronger. The UI frames these as *"A user with a similar profile reported..."*,
never as an outcome the reader should expect.

### An experience is not evidence

Overall and personalized aggregates are reported as separate figures, and every
aggregate carries the statement that these are personal experiences which do
not establish causation. The engine never converts a community signal into a
claim about the product.

### Privacy

Identity, private context and community contribution stay separate. No response
in this module carries the author's identity. Context sharing is opt-in twice
over — the account's privacy settings (`GET`/`PUT /profile/privacy`) and a
per-review checkbox — and a review can never share more than the profile
permits. Health context is never shared. Withdrawing consent and re-submitting
strips the context already attached.

### Manipulation prevention

| Guard | Mechanism |
|---|---|
| Duplicate experiences | One review per user per product (`uq_community_review_user_product`); re-submitting updates it |
| Vote stuffing | A row per voter (`uq_review_vote_user_review`); counts are recomputed from rows, never incremented |
| Spam / bot content | Links, contact details, promotional phrasing, shouting, character and word repetition |
| Burst submission | More than 5 reviews from one account in 10 minutes |
| Abuse | Reports accumulate; at 3 the review is withdrawn from the public aggregate pending moderation |
| Self-dealing | You cannot vote on or report your own experience |

A review that trips a check is **held, not discarded** — it stays visible to its
author with the reason, and stays out of the public aggregate. Hiding someone's
genuine experience on a false positive is a real cost, and there is no moderator
queue to drain, so clean reviews publish immediately.

---

## Project Structure

```
arivio/
├── frontend/                   # React + TypeScript + Vite
│   └── src/
│       ├── components/         # Navbar, CommunitySection
│       ├── pages/              # Profile, ProductDetail, ProductSubmit, Scan, Dashboard
│       ├── services/api.ts     # Axios client + token-refresh interceptor
│       ├── store/slices/       # Redux Toolkit slices
│       └── index.css           # Design system tokens
├── backend/
│   ├── app/
│   │   ├── auth/               # Register, login, refresh, me
│   │   ├── users/              # Profile, goals, allergies, preferences
│   │   │   └── tasks.py        # AI custom-goal generation + sanity gate
│   │   ├── products/           # Search, scan, submit, OpenFoodFacts ingest
│   │   ├── personalization/
│   │   │   ├── engine.py       # ← Scoring engine (pure, no I/O)
│   │   │   └── router.py       # Suitability + alternatives
│   │   ├── allergens/
│   │   │   ├── inference.py    # AI fallback + cache
│   │   │   ├── models.py       # AllergenInference
│   │   │   └── tasks.py        # Background cache priming
│   │   ├── ai/                 # Gateway, report routes, feedback
│   │   ├── ingredients/        # Ingredient knowledge base
│   │   ├── community/          # Reviews & experiences
│   │   ├── core/               # Config, security
│   │   └── db/                 # Async session
│   ├── alembic/versions/       # Migrations
│   └── test/                   # Regression + integration scripts
├── docker-compose.yml
└── README.md
```

---

## API Endpoints

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/register` | Register new user |
| `POST` | `/api/v1/auth/login` | Login (returns access + refresh) |
| `POST` | `/api/v1/auth/refresh` | Exchange refresh token |
| `GET` | `/api/v1/auth/me` | Current user |

### Profile

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` `PUT` | `/api/v1/profile` | Get / update profile (age range, dietary pattern, activity level) |
| `GET` | `/api/v1/profile/options` | Accepted values for those fields — the UI builds its dropdowns from this |
| `GET` | `/api/v1/profile/dashboard` | Dashboard summary |
| `POST` `DELETE` | `/api/v1/profile/goals[/{id}]` | Manage goals — `409` if the goal (or an alias of it) is already active |
| `POST` `DELETE` | `/api/v1/profile/allergies[/{id}]` | Manage allergies — `409` on an exact repeat of the same allergen and type |
| `POST` `DELETE` | `/api/v1/profile/preferences[/{id}]` | Manage nutrient preferences — re-posting updates strictness rather than duplicating |
| `GET` | `/api/v1/profile/allergens/known` | Canonical allergen names (autocomplete) |
| `POST` | `/api/v1/profile/history/{product_id}` | Record a scan |
| `GET` `PUT` | `/api/v1/profile/privacy` | Anonymous context-sharing consent (opt-in; all off by default) |

### Community

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/community/products/{product_id}` | Aggregates + relevant experiences (auth optional; personalization needs a profile) |
| `POST` | `/api/v1/community/reviews` | Share an experience — one per user per product, re-submitting updates it |
| `DELETE` | `/api/v1/community/reviews/{id}` | Withdraw your own experience |
| `POST` | `/api/v1/community/reviews/{id}/vote` | Helpful / not helpful — one vote per reader, changeable |
| `POST` | `/api/v1/community/reviews/{id}/flag` | Report for moderation — accumulates to a threshold |

### Products

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/products/suggest` | Typeahead candidates — local only, safe to call while typing |
| `GET` | `/api/v1/products/search` | Fuzzy product search, ranked; falls back to Open Food Facts when local matches are weak |
| `GET` | `/api/v1/products/{product_id}` | Product details |
| `POST` | `/api/v1/products/scan` | Barcode scan (auth optional) |
| `POST` | `/api/v1/products/import` | Import an external search candidate into the catalogue — idempotent |
| `POST` | `/api/v1/products/images` | Store a photo for a product about to be submitted; returns a content-hash id |
| `POST` | `/api/v1/products/submit` | Submit a product — parses `ingredients_text` into ordered rows; `409` if the barcode is already on file |

#### Product photos

Three paths create products, and all three can carry an image:

| Path | Image |
|---|---|
| Barcode / OFF import | Open Food Facts' own photo |
| Label scan | The photo that was read |
| Manual submit | Optional upload |

Uploading is a **separate step** from `/products/submit`, which stays JSON. The
duplicate guard answers `409` and the user resubmits with `force` — making them
re-pick their photo for that second attempt would be poor, so the image is
already stored and referenced by id.

Only a **front-of-pack** photo becomes `Product.image_url`. An ingredients or
nutrition close-up is still attached to the product but is not used as the
thumbnail — it is the likeliest thing to photograph and the worst thing to show
in a results grid.

The id is a content hash and is **validated, not trusted**: it must be 64 hex
characters and name a file that exists, since it ends up in a URL. Without the
first check a caller could point a product's image at any path the static mount
can reach.

Uploads reuse the label-scanning pipeline — decoded, **EXIF stripped** (phone
photos carry GPS), downscaled, re-encoded, stored by content hash. Submitted
photos are visible to other users, so `uploaded_by` is recorded on every one;
there is no moderation queue yet.

### Label scanning (OCR)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/ocr/label` | Read a product label from a photo — returns an extraction plus catalogue matches |
| `GET` | `/api/v1/ocr/label/{extraction_id}` | Re-read a pending extraction (survives a page reload) |
| `POST` | `/api/v1/ocr/confirm` | Create a product from the extraction as the user corrected it |

#### How label scanning works

Identification climbs a ladder, cheapest and most reliable first:

1. **Barcode decoded in the browser** — an exact identity, goes to `/products/scan`.
2. **Barcode legible in the photo** — accepted only if its GTIN check digit
   agrees, which rejects a single misread digit ~90% of the time. That is what
   makes an OCR'd barcode safe to look a product up with.
3. **Name and brand read off the pack** — run through the *same* fuzzy matcher
   as a typed search, so an OCR'd "dairy milk choclate" resolves to the
   existing product instead of creating a near-duplicate.
4. **Nothing matched** — the user confirms the extraction into a new product.

**Nothing read from a photo becomes a product on its own.** `/ocr/label` reads
and proposes; `/ocr/confirm` writes only what the user approved (PRD §14). This
is not a nicety: an OCR misread that silently became an ingredient list would
be scored by the allergen engine as though it came from a real label.

Confirmed products are written as **tier 4 / `low` data quality /
`user_submitted`**, with a `data_provenance` row per field recording which read
produced it and whether the user corrected it (PRD §21).

Two safeguards worth knowing about:

- **Uploads are re-encoded, never stored as received.** Pillow decodes the
  image and re-saves it from raw pixels, which strips all EXIF — including the
  GPS coordinates phone cameras write. A user photographing a product at home
  must not have their location travel with it to a vision API or to other
  users. Orientation is applied before the metadata is dropped, and the image
  is downscaled to 1600px.
- **Impossible nutrition values are dropped, not shown.** A misplaced decimal
  ("0.5g" → "5000g") looks unremarkable in a form field but moves a suitability
  score hard, so anything over 100g per 100g or 900kcal/100g becomes "not
  detected" rather than a number the user might rubber-stamp.

#### Configuring the reader

```bash
# backend/.env
OCR_PROVIDER=gemini
OCR_API_KEY=<your Google AI Studio key>
```

`OCR_API_KEY` and `OCR_MODEL` fall back to `AI_API_KEY` and a per-provider
default when blank, so labels can be read on Gemini while reports keep running
on Groq.

`auto` picks a vision model when one is configured and otherwise falls back to
Tesseract, but it only auto-selects **gemini** or **openai**. Groq is excluded
because its vision line-up varies by account (this project's Groq account
serves none), so a Groq key alone would otherwise send an image to a model that
404s. To use Groq, name it explicitly and set `OCR_MODEL` to a vision model
your account lists.

**Under Docker, editing `.env` is not enough.** Compose's `env_file` copies the
file into the container's environment *at creation*, and pydantic-settings
prefers process environment over the file — so a container created while
`OCR_PROVIDER=tesseract` keeps using Tesseract no matter what the file says
later. `docker compose restart` reuses the same environment too. Recreate it:

```bash
sudo docker compose up -d --force-recreate backend
```

#### Which model

Measured on this repo's sample label (`test/seed_label_scan.py`) and a
deliberately degraded copy — blurred, rotated, low-contrast, heavily
compressed, glare over one corner. Scores are ingredients matched / nutrients
matched exactly:

| Model | Clean | Degraded |
|---|---|---|
| `gemini-3.5-flash-lite` *(default)* | 2.0s — 4/4, 7/7 | 2.8s — 4/4, 5/7 |
| `gemini-3.6-flash` | 23s — 4/4, 7/7 | 34s — 4/4, 5/7 |
| `gemini-3.1-flash-lite` | 3.1s — 4/4, 7/7 | 4.5s — 4/4, **2/7** |

flash-lite 3.5 is the default because it reads labels as accurately as the full
flash model at a tenth of the latency, and that latency sits on a user's
critical path. 3.1-flash-lite is not a safe substitute — it misread five of
seven figures on the harder image.

Two things that table makes plain, and that the design depends on:

- **Every model misread numbers on the degraded image** (sodium 88 → 68 or 90;
  energy 534 → 531). All of them lowered their confidence and warned about
  image quality, but none of them refused to answer. That is precisely why the
  confirmation screen is mandatory rather than a courtesy.
- Even on a clean read, a real call transcribed "Flavours" as "Flours". Close
  enough to look right in a form field, wrong enough to matter in an ingredient
  list.

Google retires Gemini models on a schedule — `gemini-2.0-flash`, the previous
default throughout this codebase, now returns `404 no longer available`. The
current default lives in one constant (`GEMINI_DEFAULT_MODEL` in
`app/ai/gateway.py`, and `DEFAULT_VISION_MODELS` in `app/ocr/gateway.py`). To
see what your key can reach:

```bash
curl -H "Authorization: Bearer $OCR_API_KEY" \
  https://generativelanguage.googleapis.com/v1beta/openai/models
```

#### How product search works

Matching is trigram-based (`pg_trgm`), so a partial, misspelled or reordered
name still finds the product: "maggie", "butter amul" and "choclate" all
resolve. Results come back **ranked with a `match_score`** for the user to
choose from, rather than filtered to a single answer — the UI labels anything
below 65% as a close match rather than an exact one.

Two similarity measures are combined, because a short query against a long
name and a full name with a typo fail in opposite directions:

| Measure | Answers |
|---|---|
| `similarity(a, b)` | how alike the two strings are *as wholes* |
| `word_similarity(a, b)` | how well `a` matches the best *portion* of `b` |

Both run against `coalesce(brand,'') || ' ' || name` — "amul butter" scores
poorly against the name and the brand separately, but well against the two
joined — and a GIN index over that same expression is what keeps it fast. The
expression in the query must stay character-identical to the one in migration
`a7c31f9d5b60`, or PostgreSQL silently stops using the index and every search
goes back to a sequential scan. `backend/test/test_search.py` asserts against
`EXPLAIN` that it hasn't.

When the local catalogue has nothing convincing, Open Food Facts is searched
by name as well. Those hits are returned as **candidates, not products**: they
are cached in Redis and only written to the database when the user picks one
(`POST /products/import`). Importing every hit would add dozens of
loosely-matched rows per search, which would then compete against real
products in every later search.

### AI provider chain

Model calls walk a chain and take the first success:

```
gemini ──▶ groq ──▶ cerebras ──▶ openrouter ──▶ rule-based fallback
```

A provider with no API key is **skipped, not failed**, so you configure only
the ones you have. The rule-based fallback runs only when *every* configured
provider has failed — which is what it was always meant for.

Before the chain, one provider was tried once and any error dropped the user
to rule-based prose. That happened constantly, and the usual cause was not an
outage: `max_tokens=2000` was too small for a full report, so a reasoning
model would spend its budget thinking, get cut off mid-JSON, and the provider
would reject the truncated document with `json_validate_failed`. Measured at
roughly **1 failure in 3**. Two fixes, both needed — a bigger budget stops it
happening, and the chain stops it mattering.

Everything routes through `app/ai/providers.py`: report generation, allergen
inference, goal-profile generation, label OCR and health-document extraction.
Provider selection used to be written out at five call sites that had drifted
apart — which is how `gemini-2.0-flash` stayed hardcoded in all of them long
after Google retired it.

| Setting | Purpose |
|---|---|
| `AI_PROVIDER_CHAIN` | Order to try, e.g. `gemini,groq,cerebras,openrouter` |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | Per-provider keys — each is only ever offered to its own provider |
| `GEMINI_MODEL`, `GROQ_MODEL`, … | Per-provider model overrides (a model name is not portable) |
| `AI_TIMEOUT` | Per-attempt timeout, so a stalled provider is abandoned quickly |

Legacy `AI_PROVIDER` + `AI_API_KEY` still work: that provider is appended to
the chain — but only when `AI_PROVIDER_CHAIN` is left at its default, since an
explicitly written chain states the order you want.

**Model choice was measured, not assumed.** On a full product report, 3 runs each:

| Model | Latency | Analysis length |
|---|---|---|
| `gemini-3.5-flash-lite` *(default)* | **3.0s** | 1426 chars |
| `gemini-3.1-flash-lite` | 4.3s | 1309 |
| `gemini-3.5-flash` | 11.5s | 1762 |
| `gemini-3.6-flash` | — | 503 "experiencing high demand" |

flash-lite is fastest *and* most reliable, with no thinner output.

Two behaviours worth knowing:

- **Truncation retries the same provider once** with double the token budget,
  rather than moving on — a different provider would just be cut off too.
- **A non-object response is a failure.** Models without JSON mode sometimes
  return a bare array; every caller does `data.get(...)`, so that would crash
  them. It counts as a failed attempt and the chain moves on.

```bash
python test/test_ai_chain.py          # offline: resolution, ordering, fall-through
python test/test_ai_chain.py --live   # also calls the real providers
```

### Health context

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/health/context` | The caller's documents, the patterns they activate, and any conflicting goals |
| `PUT` | `/api/v1/health/consent` | Give consent, or withdraw it — withdrawal **deletes** every document |
| `POST` | `/api/v1/health/documents` | Read a lab report (PDF or photo) into markers, pending review |
| `GET` | `/api/v1/health/documents/{id}` | One document's markers |
| `PUT` | `/api/v1/health/documents/{id}` | Save markers as corrected, and mark reviewed |
| `DELETE` | `/api/v1/health/documents/{id}` | Delete a document and its markers |

Upload a blood test and the engine takes your readings into account: products
that work against a marker score lower and say why, and a goal pulling the
other way is flagged on both the product and your profile.

#### The model does not compute the score

A vision or text model reads numbers off the document. **Everything after that
is a table.** `HEALTH_CONDITION_PROFILES` in `app/health/profiles.py` uses the
same `prefer_low` / `prefer_high` / `thresholds` / `weights` vocabulary as
`GOAL_PROFILES`, and the engine scores it with the same arithmetic. So the
result is deterministic, reproducible, testable, and explainable as a rule
rather than asserted as a verdict — the same properties the rest of the engine
already had.

Sharing one vocabulary buys the goal-conflict feature for free. It is set
intersection, not inference:

```
"muscle gain"              prefer_high: protein_g
"reduced kidney function"  prefer_low:  protein_g
                        →  conflict on protein
```

Every future goal/condition pair is covered the moment both are defined.

#### Privacy

PRD §10 requires health context to be private by default, explicitly consented,
minimised, and never exposed through community profiles. How that is enforced:

- **The document is never stored.** There is no path or blob column — a report
  is parsed in memory and discarded. That removes the retention policy, the
  encrypted blob store, and any chance of a lab report landing under the public
  `/uploads` mount that serves product photos. The markers are the useful part.
- **Markers are one encrypted blob**, not queryable columns. Encrypting only
  the number would leave `analyte='hba1c', flag='high'` readable, which *is*
  the sensitive fact. The schema cannot answer "which users have elevated
  glucose".
- **Fails closed.** With no `HEALTH_ENCRYPTION_KEY`, every health endpoint
  answers 503 and nothing is stored — rather than writing blood results in
  plaintext because a config value was missed.
- **No `share_health_context` flag exists.** Every other profile attribute can
  be opted into for community matching; this one cannot be, at any setting. Not
  offering the switch is the enforcement.
- **Withdrawal deletes.** Not hides, not deactivates.
- **Ownership is scoped in the query**, and another user's document returns
  404 rather than 403 — a 403 would confirm it exists.

#### It does not diagnose

Conditions are named as patterns of readings ("elevated blood sugar"), never as
diseases. Health context adjusts a score and raises flags but never caps it or
vetoes a food — that would be software making a clinical decision. A disclaimer
is attached to every product where health context applied, and the AI report
prompt forbids naming a disease or implying a diagnosis. The test suite asserts
that no diagnostic language appears in any health flag.

Nothing influences a score until the user **confirms** it: an unreviewed
misread must never move a number, and unit conversion matters here — a glucose
in mmol/L scored against a mg/dL threshold is wrong by 18×.

```bash
# Enable it
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → backend/.env as HEALTH_ENCRYPTION_KEY
```

Losing that key makes existing health data permanently unreadable. That is the
intended property; back it up like a password.

### Personalization & AI

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/personalization/suitability/{product_id}` | Personal Suitability Score (incl. `diet_compatible`) |
| `GET` | `/api/v1/personalization/alternatives/{product_id}` | Better-matching alternatives |
| `GET` | `/api/v1/ai/report/{product_id}` | Natural-language report |
| `POST` | `/api/v1/ai/feedback` | Submit report feedback |
| `GET` | `/api/v1/ai/feedback/{product_id}` | Retrieve own feedback |

### Sample response

`GET /api/v1/personalization/suitability/5`

```json
{
  "overall_score": 45,
  "verdict": "Caution — may contain an allergen from your profile",
  "confidence": 85,
  "allergen_safe": false,
  "goal_alignments": [
    {
      "goal": "no muscle",
      "alignment": "not_evaluated",
      "score": null,
      "reason": "This goal is nutritionally harmful and cannot be safely scored.",
      "evaluated": false
    }
  ],
  "flags": [
    {
      "flag_type": "warning",
      "category": "allergen",
      "title": "May contain coconut",
      "description": "This product may contain coconut, which is on your allergy list. (identified by AI ingredient analysis, not a declared label.) We matched the ingredient 'mono- and diglycerides'.",
      "impact": -25
    }
  ],
  "breakdown": {
    "goal_alignment": null,
    "nutritional_quality": 74,
    "ingredient_profile": 80,
    "allergen_conflict": "trace",
    "unscored_goals": ["no muscle"],
    "weights": {
      "goal_alignment": 0.0,
      "nutritional_quality": 0.75,
      "ingredient_profile": 0.25
    }
  }
}
```

---

## Database

Key tables beyond the obvious:

| Table | Purpose |
|---|---|
| `user_goals` | Goals with `profile_status` + `status_message` |
| `custom_goal_profiles` | AI-generated thresholds, **global** — unique on `goal_type` |
| `allergen_inferences` | Cached AI allergen verdicts |
| `report_feedback` | Ratings and comments on AI reports |
| `user_scan_history` | Scan history for the dashboard |
| `community_reviews` | One experience per user per product |
| `review_contexts` | The anonymised context an author consented to share |
| `review_votes` | Helpful votes, one row per voter |
| `review_flags` | Abuse reports, one row per reporter |
| `privacy_settings` | Per-attribute context-sharing consent |

`goal_type` is stored **normalized** (lowercased, `-`/`_` → space) on write, so it
matches both hardcoded profile keys and generated rows.

### Uniqueness guards

Three reads assume at most one row. Each is enforced in the schema, because an
application check alone cannot close a concurrent-request race:

| Constraint | Table | Why |
|---|---|---|
| `uq_product_identifier_value` | `product_identifiers` | The barcode lookup keys on the value alone. A repeated value made it ambiguous and broke **every later scan of that barcode**. Two concurrent scans of an unknown barcode could each import it. |
| `uq_report_feedback_user_product` | `report_feedback` | One rating per user per product; re-rating updates the existing row. |
| `uq_user_goal_active` | `user_goals` | Partial (`WHERE is_active`) — a duplicate goal is double-counted in the weighted average. Partial so removing a goal does not block re-adding it. |

The endpoints return a `409` before these fire, so the constraint is a backstop
rather than the user-facing error. For goals the application check is also
*stronger* than the index: it compares after alias resolution, which the stored
text cannot express.

```bash
alembic upgrade head          # apply
alembic current               # show current revision
alembic history               # full chain
```

---

## Testing

```bash
cd backend
./venv/bin/python test/test_engine_regression.py   # 132 assertions, no DB required
./venv/bin/python test/test_community.py           # 41 assertions, no DB required
./venv/bin/python test/test_scoring.py             # scoring smoke test
```

`test_engine_regression.py` is the safety net for the engine and covers goal
alias resolution, allergen false positives (`egg` vs `eggplant`), category
isolation (`cashew` vs `almond`), conflict tiers, order-independent severity,
ingredient quality ordering, nutrient preferences (soft vs strict, the +/-15
cap, and skipping what the label cannot measure), cache-key composition, the
per-100g wording and
rounding of nutrition flags, dietary-pattern exclusions per pattern, the
plant-qualifier rule that keeps `almond milk` out of a milk allergy, the rule
that an unscored goal is never rendered as `None/100`, and the rule that **AI
can never clear a literal allergen match**.

Frontend type checking:

```bash
cd frontend && npx tsc --noEmit -p tsconfig.app.json
```

---

## Initial Market

🇮🇳 **India** — Packaged Food & Beverages

## License

Private / Proprietary
