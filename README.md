# 🌿 ATS Job Tracker

Auto-polls confirmed public job boards from **Greenhouse, Lever, Ashby, and SmartRecruiters** every 15 minutes, filters for **USA/Remote** + **Software & IT** roles, scores the newest jobs against your resume using **Gemini 2.5 Flash-Lite**, and sends Slack alerts for strong matches.

Zero infrastructure. No database. Just GitHub Actions + a JSON file.

---

## How it works

```
Every 15 min (GitHub Actions cron)
    │
    ▼
Read companies/greenhouse.txt, companies/lever.txt, companies/ashby.txt, and companies/smartrecruiters.txt
    │
    ▼
Fetch each ATS API for the enabled company slug
    │
    ▼
Filter 1: Already seen & unchanged?  → SKIP (deduplication via seen_jobs.json)
Filter 2: Already scored/alerted?    → SKIP (alerted flag — never score twice)
Filter 3: USA/Remote location?       → SKIP if non-US
Filter 4: Software/IT title?         → SKIP if unrelated
    │
    ▼
NEW jobs → Keep only the newest 3 per ATS for scoring
    │
    ▼
Scored jobs → Gemini 2.5 Flash-Lite (JSON score 0–100)
    │
    ├── Score ≥ 65% → Send Slack alert with score + Apply button
    └── Score < 65% → Log only, no alert
    │
    ▼
Remaining new jobs → Mark seen only, skip scoring
    │
    ▼
Write ATS-categorized run output and update state
Update data/seen_jobs.json (7-day rolling TTL)
    │
    ▼
git commit + push (only if changes exist)
```

---

## Setup

### 1. Create a public repo and push all files
Make it **public** for free unlimited GitHub Actions minutes.

### 2. Grant write permissions
Repo → **Settings → Actions → General → Workflow permissions** → set to **"Read and write permissions"**.

### 3. Add GitHub Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `GEMINI_API_KEY` | Your Gemini API key from [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `SLACK_WEBHOOK_URL` | Your Slack incoming webhook URL from [api.slack.com/apps](https://api.slack.com/apps) |

### 4. That's it. Push and wait.
The workflow fires automatically every 15 minutes. Trigger manually anytime via **Actions → ATS Job Poller → Run workflow**.

---

## Slack Alerts

When a new job scores **≥ 65%** match against your resume, you get a Slack message with the ATS name front and center:

```
🎯 78% Match — Senior Backend Engineer
Greenhouse · Stripe · Engineering · Remote, USA    Score: ████████░░ 78/100
[🔗 Apply Now]
Greenhouse · Job ID: 4829301 · Updated: 2026-04-05T14:28:00Z
```

Score icons:
- 🔥 85–100% — Strong match
- 🎯 70–84%  — Good match
- ✅ 65–69%  — Above threshold
- (no alert)  — Below 65%

---

## Scoring

Each **new** job is scored once against `resume.txt` using **Gemini 2.5 Flash-Lite**. The score is a single integer 0–100 reflecting how well your background matches the role.

- To protect the GitHub Actions 10-minute limit, only the **newest 3 jobs per ATS** are scored on each run. The rest are recorded as seen and skipped.
- Updated jobs (existing jobs with a changed `updated_at`) are **never re-scored** — only logged in `output/jobs.md`
- Once a job is scored it is permanently marked as `alerted: true` in state — it will never be scored or alerted again even if the posting is later modified
- Estimated cost: **free tier** if you stay within Gemini quotas; the current free tier for Flash-Lite is **15 RPM, 250,000 TPM, 1,000 RPD**

To change the alert threshold edit `scorer.py`:
```python
SCORE_THRESHOLD = 65  # raise to reduce alerts, lower to catch more
```

---

## Output

`output/jobs.md` is updated in-place, newest runs at the top. Entries are grouped by ATS:

```markdown
## 📅 Run: 2026-04-05 14:32 UTC

### Greenhouse
#### 🆕 Senior Backend Engineer
**Stripe** · Engineering · 🎯 78%
📍 Remote, USA | 🔗 [Apply Here](https://boards.greenhouse.io/...)
🕐 Updated: 2026-04-05T14:28:00Z | ID: 4829301

### Lever
#### 🆕 Staff Platform Engineer
**Kraken** · Platform · ✅ 67%
📍 Remote, USA | 🔗 [Apply Here](https://jobs.lever.co/...)
🕐 Updated: 2026-04-05T14:30:00Z | ID: abc123

### Ashby
#### 🔄 Staff Infrastructure Engineer
**Coinbase** · Infrastructure
📍 San Francisco, CA | 🔗 [Apply Here](...)
🕐 Updated: 2026-04-05T13:55:00Z | ID: 3912847
```

Icons:
- 🆕 = new job, never seen before (scored)
- 🔄 = job posting was updated (not re-scored)

---

## Customizing companies

Edit the ATS-specific files under `companies/`:

* `companies/greenhouse.txt` — Greenhouse board tokens
* `companies/lever.txt` — Lever account slugs
* `companies/ashby.txt` — Ashby job board names
* `companies/smartrecruiters.txt` — SmartRecruiters company identifiers

Each file uses one confirmed slug per line. `#` lines are comments.
`companies/lever.txt` and `companies/ashby.txt` ship with a small confirmed starter set you can trim or expand.

`companies/greenhouse.txt` comes pre-seeded with **200+ curated companies** across:
- Fintech & payments (Stripe, Marqeta, Brex, Mercury, Ramp...)
- Banking-as-a-service (Unit, Lithic, Synctera, Column...)
- Crypto (Coinbase, Anchorage, Bitgo, Fireblocks, Gemini...)
- Cloud & infra (Databricks, Cloudflare, Datadog, Snowflake...)
- DevOps & security (PagerDuty, Wiz, Snyk, Okta, Sentry...)
- AI/ML (Anthropic, Cohere, Scale, Anyscale...)
- SaaS (Notion, Figma, Linear, Retool, HubSpot...)

Companies that return 404 are silently skipped. Use only confirmed public slugs for Lever and Ashby so the poller stays on valid board pages.

---

## Resume

Your resume lives in `resume.txt` (plain text). It is sent to Gemini on every scoring call. To update it just edit the file and push — no other changes needed.

---

## Storage & cost

| Item | Detail |
|---|---|
| State file | `data/seen_jobs.json` — 7-day TTL, auto-pruned each run |
| Actions minutes | **Free** on public repos (unlimited) |
| Greenhouse API | Public, no auth, no rate limits |
| Lever API | Public postings API for confirmed slugs |
| Ashby API | Public job-board API for confirmed board names |
| SmartRecruiters API | Public Posting API for confirmed company identifiers |
| Gemini API | free tier, if within quota: 15 RPM / 250k TPM / 1,000 RPD for Flash-Lite |
| Commit frequency | Only when new/updated jobs are found |

---

## Files

```
greenhouse-job-tracker/
├── .github/workflows/poll_jobs.yml   # cron scheduler + runner
├── ats_sources.py                   # ATS configuration, fetch, and normalization
├── output_writer.py                 # ATS-grouped Markdown run log writer
├── data/seen_jobs.json               # dedup state with 7-day TTL + alerted flags
├── output/jobs.md                    # your job feed (newest first)
├── companies/greenhouse.txt         # Greenhouse board slugs
├── companies/lever.txt               # Lever account slugs
├── companies/ashby.txt               # Ashby job board names
├── companies/smartrecruiters.txt     # SmartRecruiters company identifiers
├── resume.txt                        # your resume in plain text (used for scoring)
├── poller.py                         # main orchestrator
├── filters.py                        # USA location + software title filtering
├── state.py                          # state CRUD, TTL pruning, alerted flag
├── scorer.py                         # Gemini 2.5 Flash-Lite resume match scorer (returns int)
├── notifier.py                       # Slack webhook alerter
├── requirements.txt
└── README.md
```

---

## Adding more companies

Greenhouse: `boards.greenhouse.io/<slug>`, Lever: `api.lever.co/v0/postings/<slug>`, Ashby: `jobs.ashbyhq.com/<board-name>`, SmartRecruiters: `careers.smartrecruiters.com/<companyIdentifier>`.. Add confirmed slugs to the matching file under `companies/` and push.
