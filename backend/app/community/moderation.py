"""
Automated checks applied to a community review at submission.

The requirement lists spam, duplicate, bot, suspicious-pattern and coordinated
manipulation detection. What is implementable deterministically today lives
here; each check returns a reason rather than a bare boolean so a held review
can tell its author what tripped, and a moderator can see why.

The bar is deliberately set to *hold for review*, not *silently discard*. A
false positive that hides someone's genuine experience is a real cost, so a
held review is still visible to its author and still recoverable by a
moderator — it is only kept out of the public aggregate.

Reviews that pass are published immediately. Queueing every experience behind
manual approval would stall the dataset the platform depends on growing, and
there is no moderator queue to drain it.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.community.models import CommunityReview

# Reports needed before a published review is withdrawn pending moderation.
# One reader disagreeing with an experience is not grounds to remove it.
FLAG_THRESHOLD = 3

# More than this many reviews from one account in the window looks automated
# rather than considered.
_BURST_LIMIT = 5
_BURST_WINDOW = timedelta(minutes=10)

_URL = re.compile(r"(https?://|www\.)", re.I)
_CONTACT = re.compile(r"(\+?\d[\d\s\-()]{7,}\d|[\w.+-]+@[\w-]+\.[\w.]+)")
_PROMOTIONAL = re.compile(
    r"\b(buy now|discount code|coupon|promo code|use code|order now|whatsapp|"
    r"click here|visit our|free shipping|limited offer|dm me|follow me)\b", re.I
)


def _looks_like_spam(text: Optional[str]) -> Optional[str]:
    """Content checks on the free-text explanation."""
    if not text:
        return None
    stripped = text.strip()

    if _URL.search(stripped):
        return "The explanation contains a link."
    if _CONTACT.search(stripped):
        return "The explanation contains contact details."
    if _PROMOTIONAL.search(stripped):
        return "The explanation reads as promotional."

    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 20 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
        return "The explanation is mostly capital letters."

    # "aaaaaaaa" or a single word pasted repeatedly — a bot signature, and never
    # how someone describes a real experience.
    if re.search(r"(.)\1{9,}", stripped):
        return "The explanation repeats a single character."
    words = [w for w in re.split(r"\s+", stripped.lower()) if w]
    if len(words) >= 8 and len(set(words)) / len(words) < 0.3:
        return "The explanation repeats the same few words."

    return None


async def screen_review(
    db: AsyncSession,
    user_id: int,
    experience_text: Optional[str],
) -> Optional[str]:
    """
    Decide whether a new review can be published immediately.

    Returns None to publish, or a reason to hold it for moderation.
    """
    content_reason = _looks_like_spam(experience_text)
    if content_reason:
        return content_reason

    # Burst detection: a person writing about their own experiences does not
    # file six of them in ten minutes.
    since = datetime.now(timezone.utc) - _BURST_WINDOW
    recent = await db.execute(
        select(func.count(CommunityReview.id)).where(
            CommunityReview.user_id == user_id,
            CommunityReview.created_at >= since,
        )
    )
    if (recent.scalar() or 0) >= _BURST_LIMIT:
        return "Several reviews were submitted in quick succession."

    return None
