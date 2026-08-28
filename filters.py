"""Keyword filters, match scoring, and deadline/freshness helpers.

Tune ROLE_LEVEL_KEYWORDS and FIELD_KEYWORDS at the top — they drive all matching.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# EDIT THESE LISTS when you want to broaden/narrow matches
# ---------------------------------------------------------------------------
ROLE_LEVEL_KEYWORDS = [
    "entry level",
    "entry-level",
    "graduate trainee",
    "graduate",
    "internship",
    "intern",
    "trainee",
    "junior",
    "associate",
    "assistant",  # Accounts Assistant, Audit Assistant, etc.
    "attachment",  # Kenyan industrial attachment / student placement
]

FIELD_KEYWORDS = [
    "finance",
    "financial",
    "accounting",
    "accountant",
    "accounts",  # common KE title form: "Accounts Intern / Assistant"
    "audit",
    "auditor",
    "tax",
    "taxation",
    "actuarial",
    "actuary",
    "risk",
    "compliance",
    "credit control",
    "credit",
    "treasury",
    "bookkeeping",
    "cpa",
    "acca",
    "actuarial science",
]

# Drop if any of these appear in the TITLE (whole-word), even when role+field match.
# Keeps senior / leadership noise out of an entry-level digest.
EXCLUDE_TITLE_KEYWORDS = [
    "senior",
    "sr",
    "manager",
    "head of",
    "director",
    "chief",
    "principal",
    "lead",
    "supervisor",
    "vice president",
    "vp",
    "general manager",
    "country head",
    "executive",  # C-suite / "Finance Executive"; tweak if you want exec-assistant roles
]

# If the title contains one of these, do NOT apply EXCLUDE_TITLE_KEYWORDS.
# Needed so "Graduate Management Trainee" / "Management Trainee" stay in.
EXCLUDE_ALLOW_PHRASES = [
    "management trainee",
    "graduate management",
    "trainee programme",
    "trainee program",
]

# How long a listing stays in digests when no explicit deadline is found
FRESHNESS_DAYS = 14

# Drop URLs from state.json after this many days
STATE_RETENTION_DAYS = 30

# Month names used in Kenyan job-board deadline prose
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def find_keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Return keywords that appear as case-insensitive *whole-word* substrings.

    Whole-word matching avoids 'intern' hitting 'Internal Control', 'tax' in
    unrelated tokens, etc. Hyphenated forms in the keyword list still work
    because we normalize whitespace but keep hyphens.
    """
    hay = _normalize(text)
    hits: list[str] = []
    for kw in keywords:
        needle = kw.lower()
        # Allow the keyword to be bounded by non-alphanumeric chars
        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        if re.search(pattern, hay):
            hits.append(kw)
    return hits


def is_excluded_title(title: str) -> bool:
    """True if the title looks senior/leadership rather than entry-level."""
    low = _normalize(title)
    if any(phrase in low for phrase in EXCLUDE_ALLOW_PHRASES):
        return False
    return bool(find_keyword_hits(title, EXCLUDE_TITLE_KEYWORDS))


def is_match(
    title: str,
    snippet: str = "",
    company: Optional[str] = None,
) -> tuple[bool, list[str], list[str]]:
    """A match needs ≥1 role-level keyword in the TITLE and ≥1 field keyword
    in title or snippet, and must not hit EXCLUDE_TITLE_KEYWORDS.

    Role keywords are title-only so page chrome / other listings can't invent
    seniority. Company names are stripped from the snippet before field
    matching so 'Frankfurt School of Finance' doesn't qualify a graphic-design role.
    """
    if is_excluded_title(title):
        return False, [], []

    role_hits = find_keyword_hits(title, ROLE_LEVEL_KEYWORDS)
    snip = snippet or ""
    if company:
        snip = re.sub(re.escape(company), " ", snip, flags=re.I)
    # Drop the title prefix from snippet if present (avoid double-counting noise)
    if snip.lower().startswith(title.lower()):
        snip = snip[len(title) :]
    field_blob = f"{title} {snip}"
    field_hits = find_keyword_hits(field_blob, FIELD_KEYWORDS)
    return bool(role_hits and field_hits), role_hits, field_hits


def parse_deadline(text: str, today: Optional[date] = None) -> Optional[date]:
    """Best-effort parse of 'Apply by / Deadline / Closing' dates from free text.

    Returns None if nothing reliable is found. Patterns covered (examples):
      - Apply by 26th August 2026
      - Deadline: 26 Aug 2026
      - Closing date: 26/08/2026
      - Closing today / closes tomorrow
    Fragile by nature — boards invent new wording constantly.
    """
    today = today or date.today()
    if not text:
        return None

    low = _normalize(text)

    if re.search(r"\b(closing today|closes today|deadline today)\b", low):
        return today
    if re.search(r"\b(closing tomorrow|closes tomorrow)\b", low):
        return today + timedelta(days=1)

    # "apply by / deadline / closing [date:] 26th August 2026"
    m = re.search(
        r"(?:apply\s+by|deadline|closing(?:\s+date)?|closes(?:\s+on)?)"
        r"\s*:?\s*"
        r"(\d{1,2})(?:st|nd|rd|th)?\s+"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)"
        r"\.?\s*,?\s*(\d{4})?",
        low,
    )
    if m:
        day = int(m.group(1))
        month_token = m.group(2)
        month = _MONTHS.get(month_token) or _MONTHS.get(month_token[:3])
        if not month:
            return None
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, month, day)
            # If year omitted and date already passed by >60 days, assume next year
            if not m.group(3) and d < today - timedelta(days=60):
                d = date(year + 1, month, day)
            return d
        except ValueError:
            pass

    # Numeric: Deadline: 26/08/2026 or 2026-08-26
    m = re.search(
        r"(?:apply\s+by|deadline|closing(?:\s+date)?)\s*:?\s*"
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",
        low,
    )
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        # Prefer DMY (Kenya) when day>12; otherwise assume DMY still
        day, month = (a, b) if a > 12 or b <= 12 else (b, a)
        if month > 12:
            day, month = b, a
        try:
            return date(y, month, day)
        except ValueError:
            pass

    m = re.search(
        r"(?:apply\s+by|deadline|closing(?:\s+date)?)\s*:?\s*"
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
        low,
    )
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    return None


def is_expired(deadline: Optional[date], first_seen: date, today: Optional[date] = None) -> bool:
    """Drop if past deadline, else drop if first_seen older than FRESHNESS_DAYS."""
    today = today or date.today()
    if deadline is not None:
        return deadline < today
    return first_seen < today - timedelta(days=FRESHNESS_DAYS)


def company_from_title(title: str) -> Optional[str]:
    """Many Kenyan boards use 'Role at Company' in the title."""
    m = re.search(r"\bat\b\s+(.+)$", title or "", re.I)
    if not m:
        return None
    company = m.group(1).strip(" -\u2013\u2014")
    # Strip trailing "in Nairobi" style location suffixes when present
    company = re.sub(r"\s+in\s+[A-Za-z .'-]+$", "", company, flags=re.I).strip()
    return company or None


def iso(d: date) -> str:
    return d.isoformat()


def parse_iso(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()
