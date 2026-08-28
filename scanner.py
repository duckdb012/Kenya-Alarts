#!/usr/bin/env python3
"""Kenya Finance/Actuarial Job Scanner — daily digest entrypoint.

Flow: fetch all sources → keyword filter → expiry/freshness → dedupe via state.json
→ email digest → write pruned state.json.

Usage:
  python scanner.py            # full run (requires EMAIL_* env vars)
  python scanner.py --dry-run  # fetch/filter/update state; print summary; no email
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from emailer import build_html, send_email
from filters import (
    STATE_RETENTION_DAYS,
    is_expired,
    is_match,
    iso,
    parse_deadline,
    parse_iso,
)
from sources import SOURCE_FETCHERS, Job

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("scanner")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        log.warning("state.json corrupt — starting fresh")
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prune_state(state: dict, today: date) -> dict:
    cutoff = today - timedelta(days=STATE_RETENTION_DAYS)
    pruned = {}
    for url, meta in state.items():
        try:
            first = parse_iso(meta.get("first_seen", "1970-01-01"))
        except ValueError:
            continue
        if first >= cutoff:
            pruned[url] = meta
    removed = len(state) - len(pruned)
    if removed:
        log.info("Pruned %d state entries older than %d days", removed, STATE_RETENTION_DAYS)
    return pruned


def collect_jobs() -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    warnings: list[str] = []
    for name, fetcher in SOURCE_FETCHERS:
        log.info("Scanning %s …", name)
        try:
            batch, err = fetcher()
        except Exception as exc:
            # Belt-and-suspenders: fetchers already catch, but never let one abort the run
            log.exception("Unhandled error in %s", name)
            warnings.append(f"⚠️ {name} scan failed — check manually ({exc})")
            continue
        if err:
            msg = err if err.startswith("⚠️") else f"⚠️ {err}"
            warnings.append(msg)
            log.warning("%s: %s", name, err)
        jobs.extend(batch)
    return jobs, warnings


def filter_and_annotate(raw: list[Job], state: dict, today: date) -> list[Job]:
    """Apply keyword match + expiry, update state, tag new vs still-open."""
    matched: list[Job] = []
    seen_urls: set[str] = set()

    for job in raw:
        if job.url in seen_urls:
            continue
        seen_urls.add(job.url)

        ok, role_hits, field_hits = is_match(job.title, job.snippet, company=job.company)
        if not ok:
            continue

        job.role_hits = role_hits
        job.field_hits = field_hits

        # Prefer newly parsed deadline; else keep any previously stored one
        deadline = parse_deadline(f"{job.title} {job.snippet}")
        if job.deadline_raw:
            try:
                deadline = parse_iso(job.deadline_raw)
            except ValueError:
                pass

        meta = state.get(job.url)
        if meta:
            first_seen = parse_iso(meta["first_seen"])
            job.is_new = False
            if not deadline and meta.get("deadline"):
                try:
                    deadline = parse_iso(meta["deadline"])
                    job.deadline_raw = meta["deadline"]
                except ValueError:
                    pass
        else:
            first_seen = today
            job.is_new = True

        if is_expired(deadline, first_seen, today):
            log.info("Dropping expired/stale: %s", job.title[:80])
            # Keep in state so we don't re-announce if it reappears briefly; prune later
            state[job.url] = {
                "first_seen": iso(first_seen),
                "title": job.title,
                "source": job.source,
                "deadline": iso(deadline) if deadline else None,
            }
            continue

        if deadline:
            job.deadline_raw = iso(deadline)

        state[job.url] = {
            "first_seen": iso(first_seen),
            "title": job.title,
            "source": job.source,
            "deadline": iso(deadline) if deadline else None,
            "last_seen": iso(today),
        }
        matched.append(job)

    # Sort: new first, then by source/title
    matched.sort(key=lambda j: (not j.is_new, j.source, j.title.lower()))
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(description="Kenya finance/actuarial job scanner")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch, filter, and update state.json but do not send email",
    )
    args = parser.parse_args()

    today = date.today()
    log.info("Starting scan for %s%s", today.isoformat(), " (dry-run)" if args.dry_run else "")

    state = load_state()
    raw, warnings = collect_jobs()
    log.info("Fetched %d raw listings; %d source warnings", len(raw), len(warnings))

    matched = filter_and_annotate(raw, state, today)
    log.info("%d listings after keyword + freshness filter", len(matched))

    state = prune_state(state, today)
    save_state(state)
    log.info("Wrote state.json (%d entries)", len(state))

    html = build_html(matched, warnings, today)
    new_n = sum(1 for j in matched if j.is_new)
    if matched:
        subject = (
            f"[Job Digest] {len(matched)} finance/actuarial match(es) — "
            f"{today.isoformat()} ({new_n} new)"
        )
    else:
        subject = f"[Job Digest] No new matches today — {today.isoformat()}"

    if args.dry_run:
        log.info("DRY-RUN subject: %s", subject)
        for w in warnings:
            log.info("Warning: %s", w)
        for j in matched[:20]:
            tag = "NEW" if j.is_new else "OPEN"
            log.info("[%s] %s | %s | %s", tag, j.source, j.title[:70], j.url)
        if len(matched) > 20:
            log.info("… and %d more", len(matched) - 20)
        # Write a preview HTML next to the script for visual checking
        preview = ROOT / "digest-preview.html"
        preview.write_text(html, encoding="utf-8")
        log.info("Wrote %s", preview)
        return 0

    try:
        send_email(subject, html)
    except Exception:
        log.exception("Email send failed")
        # Exit non-zero so Actions surfaces the failure after state is saved
        return 1

    log.info("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())