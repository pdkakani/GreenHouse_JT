# 🌿 Greenhouse Job Tracker

Auto-polls Greenhouse job boards every 15 minutes, filters for **USA/Remote** + **Software & IT** roles, and stores results in `output/jobs.md`.

Zero infrastructure. No database. Just GitHub Actions + a JSON file.

---

## How it works

```
Every 15 min (GitHub Actions cron)
    │
    ▼
Read companies.txt          ← your list of Greenhouse board slugs
    │
    ▼
Fetch /v1/boards/{board}/jobs for each company
    │
    ▼
Filter 1: Already seen & unchanged? → SKIP (deduplication)
Filter 2: USA/Remote location?      → SKIP if non-US
Filter 3: Software/IT title?        → SKIP if unrelated
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

### 1. Fork or create this repo (make it **public** for free unlimited Actions minutes)

### 2. Enable GitHub Actions
Go to your repo → **Actions** tab → enable workflows.

### 3. Grant write permissions
Repo → **Settings → Actions → General → Workflow permissions** → set to **"Read and write permissions"**.

### 4. That's it. Push the code.
The workflow fires automatically every 15 minutes. You can also trigger it manually from the **Actions** tab.

---

## Customizing companies

Edit `companies.txt`:
- One Greenhouse board token per line
- Lines starting with `#` are comments
- Find a company's token: go to `https://boards.greenhouse.io/{token}` — if it loads their job board, that's the token

```
# Add your companies here
stripe
coinbase
yourcompany
```

---

## Output

`output/jobs.md` is updated in-place, newest runs at the top:

```markdown
## 📅 Run: 2026-04-05 14:32 UTC

### 🆕 Senior Backend Engineer
**Stripe** · Engineering
📍 Remote, USA | 🔗 [Apply Here](https://boards.greenhouse.io/...)
🕐 Updated: 2026-04-05T14:28:00Z | ID: 4829301

---

### 🔄 Staff Infrastructure Engineer   ← job was updated since last seen
**Coinbase** · Infrastructure
📍 San Francisco, CA | 🔗 [Apply Here](...)
🕐 Updated: 2026-04-05T13:55:00Z | ID: 3912847
```

Icons:
- 🆕 = new job not seen before
- 🔄 = job was updated (e.g. salary info added, description changed)

---

## Storage & cost

| Item | Detail |
|---|---|
| State file | `data/seen_jobs.json` — capped by 7-day TTL, auto-pruned each run |
| Actions minutes | **Free** on public repos (unlimited) |
| API calls | Greenhouse public API — no auth, no rate limits documented |
| Commit frequency | Only when new/updated jobs are found |

---

## Files

```
greenhouse-job-tracker/
├── .github/workflows/poll_jobs.yml   # scheduler + runner
├── data/seen_jobs.json               # rolling dedup state (7-day TTL)
├── output/jobs.md                    # your job feed
├── companies.txt                     # list of board slugs to poll
├── poller.py                         # main orchestrator
├── filters.py                        # location + title filtering
├── state.py                          # state CRUD + TTL pruning
├── requirements.txt
└── README.md
```

---

## Adding more companies

Find Greenhouse board tokens by googling `site:boards.greenhouse.io <company name>` or checking a company's careers page URL. If it's `boards.greenhouse.io/acme`, the token is `acme`.
