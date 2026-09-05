"""
Fuzzy product matching.

The requirement is that typing part of a product's name — misspelled, in the
wrong order, or with the brand attached — returns the products it could be, so
the user can pick. That is a ranked-candidates problem, not a filter problem,
so nothing here returns a single "the" product; callers present the list.

Ranking runs on PostgreSQL's pg_trgm rather than in Python because the same
GIN index that makes it fuzzy is what makes it fast (migration a7c31f9d5b60).
Two similarity measures are combined, because they answer different questions:

  similarity(a, b)       — how alike are the two strings *as wholes*
  word_similarity(a, b)  — how well does `a` match the best *portion* of `b`

A short query against a long name ("butter" vs "Amul Pasteurised Butter 500g")
scores badly on the first and well on the second; a full-name query with a
typo scores the other way round. Taking the greater of the two means neither
kind of query is penalised for being the wrong shape.

This module is deliberately independent of any HTTP concern: the label-OCR
flow needs exactly the same "text in, ranked product candidates out" call.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import String, and_, bindparam, func, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.models import Product

# How much of the query must match before a row is a candidate at all.
#
# pg_trgm's own default for the `<%` operator is 0.6, which is tuned for
# matching whole words and drops real hits on short or misspelled queries.
# 0.32 is permissive enough for "maggie" → "Maggi", while still excluding rows
# that merely share a common substring. Set per-transaction rather than
# globally so nothing else in the database inherits it.
WORD_SIMILARITY_THRESHOLD = 0.32

# Below this combined score the local catalogue has nothing convincing, and
# the router widens the search to Open Food Facts.
WEAK_MATCH_SCORE = 0.45

# Ceiling on how many words of the query are scored individually. Each one
# adds a comparison to the ranking expression, and past a handful they stop
# discriminating between products anyway.
MAX_TOKENS = 6


def _searchable():
    """
    The text products are matched against: brand and name joined.

    A query like "amul butter" scores poorly against the name ("Butter") and
    against the brand ("Amul") taken separately, but well against the two
    joined.

    The empty string and the separator are emitted as SQL literals rather than
    bound parameters on purpose. PostgreSQL only uses an expression index when
    the query expression matches the indexed one structurally, and a bind
    parameter is not a constant — passing them as parameters would render
    `coalesce(brand, $1) || $2 || name`, which does not match
    `coalesce(brand, '') || ' ' || name` and would silently fall back to a
    sequential scan. Keep this identical to the index in a7c31f9d5b60.
    """
    return (
        func.coalesce(Product.brand, literal_column("''"))
        + literal_column("' '")
        + Product.name
    )


def _query_param():
    """The search term, typed so the `<%` operator resolves to text/text."""
    return bindparam("q", type_=String)


def _token_param(i: int):
    """One word of the query, scored on its own."""
    return bindparam(f"q_tok_{i}", type_=String)


def _word_similarity(term, searchable):
    """
    `word_similarity(term, text)` — how well `term` matches the best
    *contiguous* run of words in `text`.
    """
    return func.word_similarity(term, searchable)


def _contains(term, searchable):
    """
    `term <% text` — true when the term matches some run of words in the text.

    self_group() forces parentheses around the concatenation. PostgreSQL puts
    `<%` and `||` at the same precedence level and resolves ties left to
    right, so the unparenthesised form parses as
    `(term <% coalesce(brand,'')) || ' ' || name` — a text expression where a
    boolean is required, which fails at execution time.
    """
    return term.op("<%", is_comparison=True)(searchable.self_group())


def _match_score(token_count: int):
    """
    How well the query matched, in 0..1.

    Three measures, best one wins. The first two treat the query as a single
    string and answer different questions — `similarity` compares the two as
    wholes, `word_similarity` finds the best contiguous run of the product
    text that the query matches.

    Neither of those handles the commonest query shape, brand plus name.
    "amul butter" is not contiguous in "Amul Pasteurised Butter 500g", so
    word_similarity has to span the intervening word and is diluted by it —
    scoring that product *below* "Jif Peanut Butter", where "butter" sits at
    the end uninterrupted. Wrong product, and reordering to "butter amul"
    fails the same way.

    So the third measure scores each word of the query separately and takes
    the WEAKEST: it asks "does every word you typed appear somewhere in this
    product?", which is order-independent and indifferent to what sits
    between the words. "amul" and "butter" both match the Amul product fully
    (1.0), while "amul" barely matches Jif at all — so the weakest-word score
    separates them decisively where the whole-string measures could not.

    Taking the greatest of the three means the per-word measure can only ever
    promote a product, never demote one: a query with one unmatched extra word
    ("amul butter organic") falls back to the whole-string score rather than
    being dragged to zero by the word that missed.
    """
    searchable = _searchable()
    measures = [
        func.similarity(_query_param(), searchable),
        _word_similarity(_query_param(), searchable),
    ]
    if token_count > 1:
        # LEAST over the per-word scores — the weakest word. Skipped for a
        # single-word query, where it is identical to the measure above.
        measures.append(func.least(*(
            _word_similarity(_token_param(i), searchable)
            for i in range(token_count)
        )))
    return func.greatest(*measures)


def _match_filter(token_count: int):
    """
    Index-backed candidate filter.

    `<%` and ILIKE are both served by the same GIN trigram index, so these ORs
    do not force a scan. ILIKE is kept alongside the fuzzy operator because an
    exact substring the user typed deliberately ("500g") should always match,
    whatever its trigram score.

    The third branch mirrors the per-word measure in _match_score: a product
    containing *every* word of the query is a candidate even when the query as
    one string scores below the threshold — which is exactly the brand+name
    case. Requiring all words rather than any keeps this from widening recall
    to everything that shares a single common word.
    """
    searchable = _searchable()
    branches = [
        _contains(_query_param(), searchable),
        searchable.ilike(bindparam("q_like", type_=String)),
    ]
    if token_count > 1:
        branches.append(and_(*(
            _contains(_token_param(i), searchable)
            for i in range(token_count)
        )))
    return or_(*branches)


def _bind_values(q: str) -> tuple[dict, int]:
    """Bound values for one query, and how many of its words are scored."""
    tokens = q.split()[:MAX_TOKENS]
    params: dict[str, object] = {"q": q, "q_like": _like_pattern(q)}
    for i, token in enumerate(tokens):
        params[f"q_tok_{i}"] = token
    return params, len(tokens)


@dataclass
class ProductMatch:
    """A product and how well it matched the query."""
    product: Product
    score: float


def normalize_query(raw: str) -> str:
    """
    Collapse whitespace and trim.

    Nothing more aggressive: pg_trgm already folds case, and stripping
    punctuation would break deliberate matches like "7-up" or "500g".
    """
    return " ".join((raw or "").split())


def _like_pattern(q: str) -> str:
    """
    Wrap the query for a contains-match, neutralising LIKE metacharacters.

    Without this a query containing `%` or `_` matches far more than the user
    typed — and `\\` has to be escaped first, or it would escape the escapes.
    """
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def _apply_threshold(db: AsyncSession) -> None:
    """
    Lower the word-similarity threshold for this transaction only.

    Issued as its own statement rather than inlined into the search query:
    set_config() inside a SELECT has no guaranteed evaluation order relative to
    the WHERE clause that depends on it.
    """
    await db.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
        {"t": str(WORD_SIMILARITY_THRESHOLD)},
    )


async def search_products(
    db: AsyncSession,
    query: str,
    *,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ProductMatch], int]:
    """
    Rank local products against a free-text query.

    Returns (matches ordered best-first, total number of matching rows).
    """
    q = normalize_query(query)
    if not q:
        return [], 0

    await _apply_threshold(db)

    params, token_count = _bind_values(q)

    conditions = [_match_filter(token_count)]
    if category:
        conditions.append(Product.category.ilike(f"%{category}%"))
    if brand:
        conditions.append(Product.brand.ilike(f"%{brand}%"))

    total = (await db.execute(
        select(func.count()).select_from(Product).where(*conditions),
        params,
    )).scalar_one()

    if not total:
        return [], 0

    score = _match_score(token_count)
    # id is the tie-breaker so pagination is stable: without it two products
    # with identical scores can swap places between pages, and one of them is
    # then missing from both.
    rows = (await db.execute(
        select(Product, score.label("match_score"))
        .where(*conditions)
        .order_by(score.desc(), Product.id)
        .limit(limit)
        .offset(offset),
        params,
    )).all()

    return [ProductMatch(product=row[0], score=float(row.match_score)) for row in rows], total


async def suggest_products(
    db: AsyncSession,
    query: str,
    *,
    limit: int = 8,
) -> list[ProductMatch]:
    """
    Typeahead candidates: local only, no pagination, no total count.

    Kept separate from search_products because this one runs while the user is
    still typing. It must never reach an external API, and it skips the count
    query, which is the expensive half of a search.
    """
    q = normalize_query(query)
    if not q:
        return []

    await _apply_threshold(db)

    params, token_count = _bind_values(q)
    score = _match_score(token_count)
    rows = (await db.execute(
        select(Product, score.label("match_score"))
        .where(_match_filter(token_count))
        .order_by(score.desc(), Product.id)
        .limit(limit),
        params,
    )).all()

    return [ProductMatch(product=row[0], score=float(row.match_score)) for row in rows]


def best_score(matches: list[ProductMatch]) -> float:
    """Score of the strongest match, or 0.0 when there are none."""
    return matches[0].score if matches else 0.0


# How close a name has to be before we treat a submission as a repeat of a
# product we already hold. Deliberately much stricter than the search
# threshold: search is offering candidates to choose from, where a loose match
# costs nothing, whereas this blocks someone from adding a product. "Amul
# Butter 500g" and "Amul Butter 100g" are different products and must both be
# allowed, so only a near-identical name trips this.
DUPLICATE_MATCH_SCORE = 0.85


async def find_probable_duplicates(
    db: AsyncSession,
    name: str,
    brand: Optional[str] = None,
    *,
    limit: int = 3,
) -> list[ProductMatch]:
    """
    Products that look like the one someone is about to add.

    Used by the submission paths so a user photographing a label we already
    hold — or submitting the same label twice — is shown the existing product
    instead of silently creating a second copy of it. Returns candidates for
    the caller to offer; it never decides on its own, because "is this the same
    product?" is a judgement only the person holding the packet can make.
    """
    terms = " ".join(filter(None, [(brand or "").strip(), (name or "").strip()])).strip()
    if not terms:
        return []

    matches, _ = await search_products(db, terms, limit=limit)
    return [m for m in matches if m.score >= DUPLICATE_MATCH_SCORE]
