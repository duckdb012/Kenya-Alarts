# Kenya Finance / Actuarial Job Scanner

Daily GitHub Actions job that scans Kenyan job boards for entry-level, graduate-trainee, and internship roles in finance, accounting, audit, tax, actuarial science, and risk & compliance — then emails you an HTML digest at **9:00 AM East Africa Time**.

Runs entirely on GitHub Actions (free tier). No server of your own.

## What you get each morning

- Matches grouped by source (JobWebKenya, Corporate Staffing, MyJobMag, BrighterMonday, Fuzu, CampusBiz, ICPAK)
- 🆕 New today vs ⏳ Still open labels
- Keyword hits that caused the match
- One-line warnings if a source failed
- A pre-filled LinkedIn search link (manual — LinkedIn is never scraped)

Zero matches still send a short “no new matches today” email so you know the run happened.

---

## One-time setup

### 1. Push this repo to GitHub

```bash
cd kenya-job-agent
git init
git add .
git commit -m "Initial kenya-job-agent"
gh repo create kenya-job-agent --private --source=. --remote=origin --push
```

(Or create an empty repo on GitHub and `git remote add origin … && git push -u origin main`.)

### 2. Generate a Gmail App Password

Gmail SMTP needs an **App Password**, not your normal login password.

1. Use the Google account **tonnyngich12@gmail.com**
2. Turn on [2-Step Verification](https://myaccount.google.com/signinoptions/two-step-verification) if it is not already on
3. Open [App Passwords](https://myaccount.google.com/apppasswords)
4. App name: e.g. `Kenya Job Agent`
5. Click **Create** and copy the 16-character password (spaces optional)

If “App Passwords” is missing: the account may be a Google Workspace account with the feature disabled, or 2SV is not fully enabled.

### 3. Add GitHub Actions secrets

In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name   | Value                                      |
|---------------|--------------------------------------------|
| `EMAIL_USER`  | `tonnyngich12@gmail.com`                   |
| `EMAIL_PASS`  | the 16-character Gmail App Password        |
| `EMAIL_TO`    | `tonnyngich12@gmail.com`                   |

Never commit these values. `scanner.py` reads them only from the environment.

### 4. Manual test run (`workflow_dispatch`)

Before trusting the daily cron:

1. GitHub repo → **Actions** → **Daily Kenya Job Scan**
2. Click **Run workflow** → **Run workflow**
3. Open the run log — confirm sources fetch and email sends
4. Check your inbox (and spam) for the digest

The schedule is `0 6 * * *` (06:00 UTC = 09:00 EAT). Cron can drift by a few minutes on the free tier.

---

## Local dry run (optional)

```bash
cd kenya-job-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Fetch + filter only (no email) — writes digest-preview.html
python scanner.py --dry-run

# Full send
export EMAIL_USER='tonnyngich12@gmail.com'
export EMAIL_PASS='your-app-password'
export EMAIL_TO='tonnyngich12@gmail.com'
python scanner.py
```

`--dry-run` updates `state.json` locally. Reset it to `{}` before the first Actions run if you want every current match treated as 🆕 New today.

---

## Tuning match keywords

Edit the lists at the top of `filters.py`:

- `ROLE_LEVEL_KEYWORDS` — entry level, intern, junior, …
- `FIELD_KEYWORDS` — finance, actuarial, CPA, …

A listing must hit **at least one from each list** (title + snippet, case-insensitive).

Also adjustable there:

- `FRESHNESS_DAYS = 14` — drop undated jobs after first-seen window
- `STATE_RETENTION_DAYS = 30` — prune `state.json`

---

## How freshness / dedup works

- `state.json` maps each job URL → `first_seen` (and optional deadline)
- Explicit deadlines in listing text are parsed when possible; past deadlines are dropped
- No deadline → keep for 14 days from first seen, then drop from digests
- Entries older than 30 days are pruned from the file
- After each Actions run, updated `state.json` is committed back with `GITHUB_TOKEN`

---

## Project layout

```
kenya-job-agent/
├── .github/workflows/daily-scan.yml
├── scanner.py       # entrypoint
├── sources.py       # per-board fetchers (isolated try/except)
├── filters.py       # keyword lists + matching + deadlines
├── emailer.py       # HTML digest + Gmail SMTP
├── state.json       # dedup / freshness store
├── requirements.txt
└── README.md
```

---

## First-run double-checks (things that were guessed)

Verified live on 2026-08-28 where noted; still re-check after your first Actions run:

| Source | Approach | Notes |
|--------|----------|--------|
| JobWebKenya | RSS `/feed/` | Confirmed working |
| Corporate Staffing | RSS `/category/finance-jobs-in-kenya/feed/` | Confirmed working |
| MyJobMag | HTML search pages | **No public RSS** (404). Scrapes `/` + `/search/jobs?q=…` for `/job/` links |
| BrighterMonday | HTML `/jobs?experience=entry-level` etc. | Keys off `/listings/` hrefs, not CSS classes |
| Fuzu | HTML | **Cloudflare 403** observed — expect ⚠️ warnings until/unless that softens |
| CampusBiz | HTML `/careers/jobs-in-kenya/…` | Keys off `/careers/vacancy/` links |
| ICPAK | HTML `/jobs/` | Embeds BrighterMonday listing links (finance-heavy) |

If a source starts returning 0 jobs, open `sources.py` for that fetcher — comments mark the fragile bits.
