# 🌿 Greenhouse Job Tracker

Auto-polls Greenhouse job boards every 15 minutes, filters for **USA/Remote** + **Software & IT** roles, scores each new job against your resume using **Gemini 2.5 Flash-Lite**, and sends Slack alerts for strong matches.

Zero infrastructure. No database. Just GitHub Actions + a JSON file.

---

## How it works

```
Every 15 min (GitHub Actions cron)
    │
    ▼
Read companies.txt          ← 200+ curated Greenhouse board slugs
    │
    ▼
Fetch /v1/boards/{board}/jobs?content=true for each company
    │
    ▼
Filter 1: Already seen & unchanged?  → SKIP (deduplication via seen_jobs.json)
Filter 2: Already scored/alerted?    → SKIP (alerted flag — never score twice)
Filter 3: USA/Remote location?       → SKIP if non-US
Filter 4: Software/IT title?         → SKIP if unrelated
    │
    ▼
NEW jobs → Score against resume via GPT-5 mini (JSON score 0–100)
    │
    ├── Score ≥ 65% → Send Slack alert with score + Apply button
    └── Score < 65% → Log only, no alert
    │
    ▼
Write new/updated jobs to output/jobs.md (newest first)
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
The workflow fires automatically every 15 minutes. Trigger manually anytime via **Actions → Greenhouse Job Poller → Run workflow**.

---

## Slack Alerts

When a new job scores **≥ 65%** match against your resume, you get a Slack message:

```
🎯 78% Match — Senior Backend Engineer
Company: Stripe · Engineering
Location: Remote, USA    Score: ████████░░ 78/100
[🔗 Apply Now]
Job ID: 4829301 · Updated: 2026-04-05T14:28:00Z
```

Score icons:
- 🔥 85–100% — Strong match
- 🎯 70–84%  — Good match
- ✅ 65–69%  — Above threshold
- (no alert)  — Below 65%

---

## Scoring

Each **new** job is scored once against `resume.txt` using **Gemini 2.5 Flash-Lite**. The score is a single integer 0–100 reflecting how well your background matches the role.

- Updated jobs (existing jobs with a changed `updated_at`) are **never re-scored** — only logged in `jobs.md`
- Once a job is scored it is permanently marked as `alerted: true` in state — it will never be scored or alerted again even if the posting is later modified
- Estimated cost: **free tier** if you stay within Gemini quotas; the current free tier for Flash-Lite is **15 RPM, 250,000 TPM, 1,000 RPD**

To change the alert threshold edit `scorer.py`:
```python
SCORE_THRESHOLD = 65  # raise to reduce alerts, lower to catch more
```

---

## Output

`output/jobs.md` is updated in-place, newest runs at the top:

```markdown
## 📅 Run: 2026-04-05 14:32 UTC

### 🆕 Senior Backend Engineer
**Stripe** · Engineering · 🎯 78%
📍 Remote, USA | 🔗 [Apply Here](https://boards.greenhouse.io/...)
🕐 Updated: 2026-04-05T14:28:00Z | ID: 4829301

---

### 🔄 Staff Infrastructure Engineer
**Coinbase** · Infrastructure
📍 San Francisco, CA | 🔗 [Apply Here](...)
🕐 Updated: 2026-04-05T13:55:00Z | ID: 3912847
```

Icons:
- 🆕 = new job, never seen before (scored)
- 🔄 = job posting was updated (not re-scored)

---

## Customizing companies

Edit `companies.txt` — one Greenhouse board token per line, `#` lines are comments:

```
# Example
stripe
coinbase
yourcompany
```

`companies.txt` comes pre-seeded with **200+ curated companies** across:
- Fintech & payments (Stripe, Marqeta, Brex, Mercury, Ramp...)
- Banking-as-a-service (Unit, Lithic, Synctera, Column...)
- Crypto (Coinbase, Anchorage, Bitgo, Fireblocks, Gemini...)
- Cloud & infra (Databricks, Cloudflare, Datadog, Snowflake...)
- DevOps & security (PagerDuty, Wiz, Snyk, Okta, Sentry...)
- AI/ML (Anthropic, Cohere, Scale, Anyscale...)
- SaaS (Notion, Figma, Linear, Retool, HubSpot...)

Companies that return 404 are silently skipped — they either don't use Greenhouse or use a different slug. Find a company's slug by checking `boards.greenhouse.io/{slug}` in your browser.

---

## Resume

Your resume lives in `resume.txt` (plain text). It is sent to Claude on every scoring call. To update it just edit the file and push — no other changes needed.

---

## Storage & cost

| Item | Detail |
|---|---|
| State file | `data/seen_jobs.json` — 7-day TTL, auto-pruned each run |
| Actions minutes | **Free** on public repos (unlimited) |
| Greenhouse API | Public, no auth, no rate limits |
| Gemini API | free tier, if within quota: 15 RPM / 250k TPM / 1,000 RPD for Flash-Lite |
| Commit frequency | Only when new/updated jobs are found |

---

## Files

```
greenhouse-job-tracker/
├── .github/workflows/poll_jobs.yml   # cron scheduler + runner
├── data/seen_jobs.json               # dedup state with 7-day TTL + alerted flags
├── output/jobs.md                    # your job feed (newest first)
├── companies.txt                     # 200+ Greenhouse board slugs
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

Google `site:boards.greenhouse.io <company name>` or check a company's careers page URL. If it's `boards.greenhouse.io/acme`, the token is `acme`. Add it to `companies.txt` and push.
