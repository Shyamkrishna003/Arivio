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

- Node.js 18+
- Python 3.11+
- Docker & Docker Compose

### 1. Start Infrastructure

```bash
docker-compose up -d
```

This starts PostgreSQL (with pgvector) and Redis.

### 2. Backend Setup

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

### 3. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

App available at: http://localhost:5173

### 4. Configuration

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
    B --> C[2. Goal alignment]
    C --> D[3. Nutritional quality]
    D --> E[4. Ingredient profile]
    E --> F[Weighted composite]
    F --> G{Allergen conflict?}
    G -- confirmed --> H[Hard cap 15 / 30]
    G -- trace --> I[Cap 45, x0.6]
    G -- preference --> J[x0.9 - 5]
    G -- none --> K[Score stands]
    H & I & J & K --> L[Score + verdict + flags]
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
Anonymous scans are unaffected.

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

### Feedback-conditioned reports

`ReportFeedback` rows (rating 1–5, type, optional comment) are injected into the
report prompt — the last 8, **including rating-only rows**, since a low rating
with no comment is still signal. An average of ≤ 2.5 escalates the instruction to
change approach materially.

> **Note:** this conditions the *narrative only*. The suitability score is fully
> deterministic and is never influenced by feedback.

---

## Project Structure

```
arivio/
├── frontend/                   # React + TypeScript + Vite
│   └── src/
│       ├── components/         # Reusable UI components
│       ├── pages/              # Profile, ProductDetail, Scan, Dashboard
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
| `GET` `PUT` | `/api/v1/profile` | Get / update profile |
| `GET` | `/api/v1/profile/dashboard` | Dashboard summary |
| `POST` `DELETE` | `/api/v1/profile/goals[/{id}]` | Manage goals |
| `POST` `DELETE` | `/api/v1/profile/allergies[/{id}]` | Manage allergies |
| `POST` `DELETE` | `/api/v1/profile/preferences[/{id}]` | Manage preferences |
| `GET` | `/api/v1/profile/allergens/known` | Canonical allergen names (autocomplete) |
| `POST` | `/api/v1/profile/history/{product_id}` | Record a scan |

### Products

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/products/search` | Search products |
| `GET` | `/api/v1/products/{product_id}` | Product details |
| `POST` | `/api/v1/products/scan` | Barcode scan (auth optional) |
| `POST` | `/api/v1/products/submit` | Submit a product |

### Personalization & AI

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/personalization/suitability/{product_id}` | Personal Suitability Score |
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

`goal_type` is stored **normalized** (lowercased, `-`/`_` → space) on write, so it
matches both hardcoded profile keys and generated rows.

```bash
alembic upgrade head          # apply
alembic current               # show current revision
alembic history               # full chain
```

---

## Testing

```bash
cd backend
./venv/bin/python test/test_engine_regression.py   # 74 assertions, no DB required
./venv/bin/python test/test_scoring.py             # scoring smoke test
```

`test_engine_regression.py` is the safety net for the engine and covers goal
alias resolution, allergen false positives (`egg` vs `eggplant`), category
isolation (`cashew` vs `almond`), conflict tiers, order-independent severity,
ingredient quality ordering, cache-key composition, and the rule that **AI can
never clear a literal allergen match**.

Frontend type checking:

```bash
cd frontend && npx tsc --noEmit -p tsconfig.app.json
```

---

## Initial Market

🇮🇳 **India** — Packaged Food & Beverages

## License

Private / Proprietary
