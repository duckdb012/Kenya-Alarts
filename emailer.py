"""HTML email composition + Gmail SMTP send."""

from __future__ import annotations

import html
import logging
import os
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from sources import LINKEDIN_MANUAL_URL, Job

log = logging.getLogger("scanner.emailer")


def _esc(s: Optional[str]) -> str:
    return html.escape(s or "")


def build_html(
    jobs: list[Job],
    warnings: list[str],
    today: date,
) -> str:
    new_count = sum(1 for j in jobs if j.is_new)
    open_count = len(jobs) - new_count

    parts: list[str] = [
        "<!DOCTYPE html><html><body style='font-family:Georgia,serif;color:#1a1a1a;"
        "line-height:1.45;max-width:720px;margin:0 auto;padding:16px'>",
        f"<h1 style='font-size:22px;margin:0 0 4px'>Kenya Finance/Actuarial Jobs</h1>",
        f"<p style='margin:0 0 16px;color:#444'>{_esc(today.strftime('%A, %d %B %Y'))} · "
        f"<strong>{len(jobs)}</strong> match(es) "
        f"(🆕 {new_count} new · ⏳ {open_count} still open)</p>",
    ]

    if warnings:
        parts.append("<div style='background:#fff8e6;border-left:4px solid #e6a800;padding:8px 12px;margin:0 0 16px'>")
        parts.append("<strong>Source warnings</strong><ul style='margin:6px 0 0 18px;padding:0'>")
        for w in warnings:
            parts.append(f"<li>{_esc(w)}</li>")
        parts.append("</ul></div>")

    if not jobs:
        parts.append(
            "<p>No matching entry-level / graduate / internship finance roles today. "
            "The scanner ran successfully — check again tomorrow, or use the LinkedIn "
            "link below for a manual sweep.</p>"
        )
    else:
        by_source: dict[str, list[Job]] = {}
        for j in jobs:
            by_source.setdefault(j.source, []).append(j)

        for source, items in by_source.items():
            parts.append(f"<h2 style='font-size:17px;border-bottom:1px solid #ddd;padding-bottom:4px'>{_esc(source)}</h2>")
            parts.append("<ul style='padding-left:18px'>")
            for j in items:
                badge = "🆕 New today" if j.is_new else "⏳ Still open"
                company = f" · {_esc(j.company)}" if j.company else ""
                deadline = f" · deadline {_esc(j.deadline_raw)}" if j.deadline_raw else ""
                kw = ", ".join(_esc(k) for k in (j.role_hits + j.field_hits))
                parts.append(
                    "<li style='margin-bottom:12px'>"
                    f"<a href='{_esc(j.url)}' style='color:#0b57d0;font-weight:600'>{_esc(j.title)}</a>"
                    f"{company}<br>"
                    f"<span style='color:#555;font-size:13px'>{badge}{deadline}"
                    f"{' · matched: ' + kw if kw else ''}</span>"
                    "</li>"
                )
            parts.append("</ul>")

    parts.append(
        "<hr style='border:none;border-top:1px solid #ddd;margin:24px 0 12px'>"
        "<p style='font-size:14px'><strong>LinkedIn (manual check)</strong><br>"
        "Not scraped (ToS). Open this pre-filled search for finance/actuarial "
        "entry-level roles in Kenya posted in the last 24h:<br>"
        f"<a href='{_esc(LINKEDIN_MANUAL_URL)}'>{_esc(LINKEDIN_MANUAL_URL)}</a></p>"
        "<p style='font-size:12px;color:#888'>Sent by kenya-job-agent · GitHub Actions</p>"
        "</body></html>"
    )
    return "\n".join(parts)


def send_email(subject: str, html_body: str) -> None:
    user = os.environ.get("EMAIL_USER", "").strip()
    password = os.environ.get("EMAIL_PASS", "").strip()
    to_addr = os.environ.get("EMAIL_TO", user).strip()

    if not user or not password or not to_addr:
        raise RuntimeError(
            "Missing EMAIL_USER / EMAIL_PASS / EMAIL_TO environment variables"
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    log.info("Sending email via Gmail SMTP to %s", to_addr)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
    log.info("Email sent")
