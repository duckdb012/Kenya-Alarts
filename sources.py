"""Per-source job fetchers.

Each public `fetch_*` function returns (jobs, error_message).
A failure in one source must never raise out of these wrappers.

GUESS FLAGS — structures verified on 2026-08-28; double-check on first run:
  - JobWebKenya / Corporate Staffing: WordPress RSS confirmed working.
  - MyJobMag: no public RSS found; HTML scrape of search + homepage.
  - BrighterMonday: /listings/ anchors in search HTML (Tailwind classes change often —
    we key off href path, not class names).
  - Fuzu: Cloudflare challenge returned 403 from this environment — expect warnings.
  - CampusBiz: vacancy URLs under /careers/vacancy/{id}-...
  - ICPAK jobs page: embeds BrighterMonday listing links (finance-heavy).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

import feedparser
import requests
from bs4 import BeautifulSoup

from filters import company_from_title, parse_deadline

log = logging.getLogger("scanner.sources")

USER_AGENT = (
    "Mozilla/5.0 (compatible; KenyaJobAgent/1.0; +https://github.com/kenya-job-agent)"
)
TIMEOUT = 30

# Pre-filled LinkedIn search for manual checking (not scraped).
LINKEDIN_MANUAL_URL = (
    "https://www.linkedin.com/jobs/search/?"
    + urlencode(
        {
            "keywords": "finance OR actuarial OR accounting OR audit OR tax OR compliance",
            "location": "Kenya",
            "f_E": "1,2",  # Internship / Entry level
            "f_TPR": "r86400",  # Past 24 hours
        }
    )
)


@dataclass
class Job:
    title: str
    url: str
    source: str
    company: Optional[str] = None
    snippet: str = ""
    deadline_raw: Optional[str] = None
    # filled later by scanner after filter pass
    role_hits: list[str] = field(default_factory=list)
    field_hits: list[str] = field(default_factory=list)
    is_new: bool = False


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-KE,en;q=0.9",
        }
    )
    return s


def _get(url: str) -> requests.Response:
    r = _session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _clean_url(url: str) -> str:
    """Strip tracking query params so the same job doesn't duplicate across UTM variants."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    keep = {k: v for k, v in qs.items() if not k.lower().startswith("utm_")}
    query = urlencode({k: v[0] for k, v in keep.items()}) if keep else ""
    # Drop fragments
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/", "", query, ""))


def _strip_html(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


def _jobs_from_rss(feed_url: str, source: str) -> list[Job]:
    resp = _get(feed_url)
    feed = feedparser.parse(resp.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"RSS parse failed for {feed_url}: {getattr(feed, 'bozo_exception', '')}")

    jobs: list[Job] = []
    for entry in feed.entries:
        title = _strip_html(getattr(entry, "title", "") or "")
        link = getattr(entry, "link", "") or ""
        if not title or not link:
            continue
        summary = getattr(entry, "summary", "") or ""
        content = ""
        if getattr(entry, "content", None):
            content = entry.content[0].value
        snippet = _strip_html(f"{summary} {content}")[:600]
        deadline = parse_deadline(snippet)
        jobs.append(
            Job(
                title=title,
                url=_clean_url(link),
                source=source,
                company=company_from_title(title),
                snippet=snippet,
                deadline_raw=deadline.isoformat() if deadline else None,
            )
        )
    return jobs


def _anchor_jobs(
    html: str,
    base_url: str,
    source: str,
    href_predicate,
) -> list[Job]:
    """Generic resilient scraper: find anchors matching href_predicate, use nearby text.

    Avoids brittle CSS class names — boards redesign those constantly.

    Context climb is capped so we don't swallow page-wide filter chrome
    (e.g. BrighterMonday's 'Entry level' facet) or sibling job titles into
    the keyword blob — that caused false positives in early dry runs.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    jobs: list[Job] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        abs_url = urljoin(base_url, href)
        if not href_predicate(abs_url):
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 5:
            continue
        # Skip nav-ish short labels
        if title.lower() in {"jobs", "careers", "apply", "read more", "view job"}:
            continue

        clean = _clean_url(abs_url)
        if clean in seen:
            continue
        seen.add(clean)

        # Climb parents for company / deadline / snippet, but stop before
        # the block balloons into a whole listing grid or filter sidebar.
        ctx_node = a
        context = title
        for _ in range(5):
            parent = ctx_node.parent
            if parent is None:
                break
            text = parent.get_text(" ", strip=True)
            # Too many peer job links ⇒ we've left the card
            peer_links = parent.find_all("a", href=True)
            peer_jobs = sum(
                1 for p in peer_links if href_predicate(urljoin(base_url, p["href"]))
            )
            if peer_jobs > 2 or len(text) > 420:
                break
            ctx_node = parent
            context = text

        deadline = parse_deadline(context)
        # Prefer "Role at Company" in the title; leave None rather than guess wrong
        company = company_from_title(title)

        jobs.append(
            Job(
                title=title,
                url=clean,
                source=source,
                company=company,
                snippet=context[:500],
                deadline_raw=deadline.isoformat() if deadline else None,
            )
        )
    return jobs


# ---------------------------------------------------------------------------
# Individual sources
# ---------------------------------------------------------------------------

def fetch_jobwebkenya() -> tuple[list[Job], Optional[str]]:
    try:
        # WordPress site-wide feed — confirmed live. Category feeds also exist if
        # you later want to narrow: e.g. /category/accounting/feed/
        jobs = _jobs_from_rss("https://jobwebkenya.com/feed/", "JobWebKenya")
        log.info("JobWebKenya: %d raw items", len(jobs))
        return jobs, None
    except Exception as exc:
        log.exception("JobWebKenya failed")
        return [], f"JobWebKenya scan failed — {exc}"


def fetch_corporatestaffing() -> tuple[list[Job], Optional[str]]:
    try:
        url = "https://www.corporatestaffing.co.ke/category/finance-jobs-in-kenya/feed/"
        jobs = _jobs_from_rss(url, "Corporate Staffing")
        log.info("Corporate Staffing: %d raw items", len(jobs))
        return jobs, None
    except Exception as exc:
        log.exception("Corporate Staffing failed")
        return [], f"Corporate Staffing scan failed — {exc}"


def fetch_myjobmag() -> tuple[list[Job], Optional[str]]:
    """MyJobMag has no working public RSS ( /feed and /jobsrss return 404 ).

    GUESS: scrape homepage + keyword search pages for /job/{slug} anchors.
    li.job-list-li / .job-info structure observed 2026-08-28 — we still key off href.
    """
    try:
        urls = [
            "https://www.myjobmag.co.ke/",
            "https://www.myjobmag.co.ke/search/jobs?q=accountant",
            "https://www.myjobmag.co.ke/search/jobs?q=actuarial",
            "https://www.myjobmag.co.ke/search/jobs?q=audit+intern",
            "https://www.myjobmag.co.ke/search/jobs?q=finance+trainee",
            "https://www.myjobmag.co.ke/search/jobs?q=compliance",
        ]
        all_jobs: list[Job] = []
        seen: set[str] = set()
        for url in urls:
            try:
                html = _get(url).text
            except Exception as page_exc:
                log.warning("MyJobMag page failed %s: %s", url, page_exc)
                continue

            def pred(u: str, _base=url) -> bool:
                p = urlparse(u)
                return p.netloc.endswith("myjobmag.co.ke") and "/job/" in p.path

            for job in _anchor_jobs(html, url, "MyJobMag", pred):
                if job.url in seen:
                    continue
                seen.add(job.url)
                all_jobs.append(job)

        log.info("MyJobMag: %d raw items", len(all_jobs))
        if not all_jobs:
            return [], "MyJobMag scan returned 0 listings — check HTML structure"
        return all_jobs, None
    except Exception as exc:
        log.exception("MyJobMag failed")
        return [], f"MyJobMag scan failed — {exc}"


def fetch_brightermonday() -> tuple[list[Job], Optional[str]]:
    """HTML scrape of entry-level (+ finance query) listing pages.

    GUESS: listing URLs are /listings/{slug-id}. Company sits as sibling text in
    the card; we use title + surrounding block as snippet for keyword matching.
    """
    try:
        urls = [
            "https://www.brightermonday.co.ke/jobs?experience=entry-level",
            "https://www.brightermonday.co.ke/jobs?q=finance&experience=entry-level",
            "https://www.brightermonday.co.ke/jobs?q=actuarial",
            "https://www.brightermonday.co.ke/jobs?q=accountant&experience=entry-level",
            "https://www.brightermonday.co.ke/jobs?q=internship+finance",
        ]
        all_jobs: list[Job] = []
        seen: set[str] = set()
        for url in urls:
            try:
                html = _get(url).text
            except Exception as page_exc:
                log.warning("BrighterMonday page failed %s: %s", url, page_exc)
                continue

            def pred(u: str) -> bool:
                p = urlparse(u)
                return "brightermonday.co.ke" in p.netloc and "/listings/" in p.path

            for job in _anchor_jobs(html, url, "BrighterMonday", pred):
                if job.url in seen:
                    continue
                seen.add(job.url)
                # Try to peel company from card context (often right after title)
                if not job.company and job.snippet.startswith(job.title):
                    rest = job.snippet[len(job.title) :].strip()
                    # First chunk before location-ish words
                    chunk = re.split(
                        r"\b(?:Nairobi|Mombasa|Kisumu|Kenya|Remote|Full Time|Part Time|"
                        r"Confidential|KSh)\b",
                        rest,
                        maxsplit=1,
                    )[0].strip(" -|")
                    if 2 < len(chunk) < 90:
                        job.company = chunk
                all_jobs.append(job)

        log.info("BrighterMonday: %d raw items", len(all_jobs))
        if not all_jobs:
            return [], "BrighterMonday scan returned 0 listings — check HTML structure"
        return all_jobs, None
    except Exception as exc:
        log.exception("BrighterMonday failed")
        return [], f"BrighterMonday scan failed — {exc}"


def fetch_fuzu() -> tuple[list[Job], Optional[str]]:
    """Fuzu is behind Cloudflare; expect this to fail from GitHub Actions often.

    GUESS: if HTML ever gets through, job links look like /kenya/job/... —
    not verified (403 challenge page observed).
    """
    try:
        urls = [
            "https://www.fuzu.com/kenya/job",
            "https://www.fuzu.com/kenya/jobs",
        ]
        all_jobs: list[Job] = []
        seen: set[str] = set()
        last_err: Optional[str] = None
        for url in urls:
            try:
                resp = _session().get(url, timeout=TIMEOUT)
                if resp.status_code == 403 or "Just a moment" in resp.text[:500]:
                    last_err = "Cloudflare challenge (403)"
                    continue
                resp.raise_for_status()
                html = resp.text
            except Exception as page_exc:
                last_err = str(page_exc)
                continue

            def pred(u: str) -> bool:
                p = urlparse(u)
                return "fuzu.com" in p.netloc and "/job" in p.path.lower()

            for job in _anchor_jobs(html, url, "Fuzu", pred):
                if job.url in seen:
                    continue
                seen.add(job.url)
                all_jobs.append(job)

        if all_jobs:
            log.info("Fuzu: %d raw items", len(all_jobs))
            return all_jobs, None
        msg = f"Fuzu scan failed — check manually ({last_err or 'no listings'})"
        log.warning(msg)
        return [], msg
    except Exception as exc:
        log.exception("Fuzu failed")
        return [], f"Fuzu scan failed — check manually ({exc})"


def fetch_campusbiz() -> tuple[list[Job], Optional[str]]:
    """CampusBiz careers board — vacancy pages for internships / trainees / entry-level.

    GUESS: individual jobs at /careers/vacancy/{id}-{slug}/ (confirmed 2026-08-28).
    Site-wide /feed/ is blog content, not vacancies — not used.
    """
    try:
        urls = [
            "https://campusbiz.co.ke/careers/jobs-in-kenya/",
            "https://campusbiz.co.ke/careers/jobs-in-kenya/internships/",
            "https://campusbiz.co.ke/careers/jobs-in-kenya/graduate-trainee/",
            "https://campusbiz.co.ke/careers/jobs-in-kenya/entry-level/",
        ]
        all_jobs: list[Job] = []
        seen: set[str] = set()
        for url in urls:
            try:
                html = _get(url).text
            except Exception as page_exc:
                log.warning("CampusBiz page failed %s: %s", url, page_exc)
                continue

            def pred(u: str) -> bool:
                p = urlparse(u)
                return "campusbiz.co.ke" in p.netloc and "/careers/vacancy/" in p.path

            for job in _anchor_jobs(html, url, "CampusBiz", pred):
                if job.url in seen:
                    continue
                seen.add(job.url)
                # Titles often look like "Junior X at Company Closing today"
                job.title = re.sub(r"\s+Closing\s+(today|tomorrow).*$", "", job.title, flags=re.I).strip()
                if not job.company:
                    job.company = company_from_title(job.title)
                all_jobs.append(job)

        log.info("CampusBiz: %d raw items", len(all_jobs))
        if not all_jobs:
            return [], "CampusBiz scan returned 0 listings — check HTML structure"
        return all_jobs, None
    except Exception as exc:
        log.exception("CampusBiz failed")
        return [], f"CampusBiz scan failed — {exc}"


def fetch_icpak() -> tuple[list[Job], Optional[str]]:
    """ICPAK member jobs dashboard — currently proxies BrighterMonday finance listings.

    GUESS: anchors point at brightermonday.co.ke/listings/... with ICPAK UTM tags.
    High signal for accounting/finance even when title lacks 'junior'.
    """
    try:
        url = "https://www.icpak.com/jobs/"
        html = _get(url).text

        def pred(u: str) -> bool:
            p = urlparse(u)
            return "/listings/" in p.path and "brightermonday" in p.netloc

        jobs = _anchor_jobs(html, url, "ICPAK", pred)
        # Titles arrive as "Senior Accountant in Nairobi" — peel location
        for job in jobs:
            job.title = re.sub(r"\s+in\s+[A-Za-z .'-]+$", "", job.title).strip()
        log.info("ICPAK: %d raw items", len(jobs))
        if not jobs:
            return [], "ICPAK scan returned 0 listings — check HTML structure"
        return jobs, None
    except Exception as exc:
        log.exception("ICPAK failed")
        return [], f"ICPAK scan failed — {exc}"


# Ordered list of (name, fetcher) — used by scanner.py
SOURCE_FETCHERS = [
    ("JobWebKenya", fetch_jobwebkenya),
    ("Corporate Staffing", fetch_corporatestaffing),
    ("MyJobMag", fetch_myjobmag),
    ("BrighterMonday", fetch_brightermonday),
    ("Fuzu", fetch_fuzu),
    ("CampusBiz", fetch_campusbiz),
    ("ICPAK", fetch_icpak),
]
