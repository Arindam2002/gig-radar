# Job Scout 🎯

**Local-first job-hunt automation.** One container pulls fresh listings from
free sources, scores them against *your* profile, finds public HR contacts,
drafts your outreach, tracks every application — and (optionally) an AI agent
turns it all into a bounded daily brief plus a spaced-repetition interview-prep
curriculum built from the jobs you're actually applying to.

All data stays on your machine. All API keys are yours. Nothing is ever sent
or submitted on your behalf.

```bash
git clone https://github.com/<you>/job-scout && cd job-scout
cp .env.example .env          # add your keys (all optional)
docker compose up --build     # → http://localhost:8501
```

First run seeds editable `data/config.yaml` (your searches, salary bands,
filters) and `data/profile.yaml` (your skills — **edit this one**, it drives
the match scoring). Everything you generate lives in `./data/`.

<details>
<summary><b>Running without Docker</b></summary>

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-runtime.txt
cp config.example.yaml config.yaml && cp profile.example.yaml profile.yaml
cp .env.example .env                      # add keys
.venv/bin/python run.py                   # fetch + score
.venv/bin/python -m streamlit run dashboard.py
```
The Naukri source and contact search additionally need Node.js (for the
Firecrawl CLI) and a `FIRECRAWL_API_KEY`.
</details>

---

## How it works — the architecture

```
                 ┌────────────────────────  SOURCES (all free)  ─────────────────────────┐
                 │ LinkedIn guest pages (two-stage: search cards → full JDs, polite/low  │
                 │ volume) · Naukri via Firecrawl JS rendering · Himalayas · Remotive ·  │
                 │ RemoteOK · Arbeitnow · [Adzuna / JSearch with keys]                   │
                 └──────────────────────────────────┬─────────────────────────────────────┘
                                                    ▼
   run.py  ───────────────────────────────  THE PIPELINE  ──────────────────────────────
   1. normalize   salary text → comparable numbers (₹ LPA / $ annual), timestamps → epochs
   2. dedupe      identity = company+title+city hash + URL hash; re-fetches MERGE, never
                  duplicate; a dismissed job that's re-posted fresh resurfaces
   3. gate        India-ineligible remote roles tagged "abroad" (relocation 🚚 flagged
                  via regex + LLM); staffing/junk auto-archived so the pool stays honest
   4. score       0-100, fully local: core-skill overlap (saturating — depth beats
                  breadth) + title fit (weighted max) − anti-signals. Salary is a
                  FILTER, never a score input
   5. enrich      Gemini structured extraction (JSON mode) fills what regex can't read:
                  years required, salary, seniority, eligibility — once per JD, ever,
                  budget-capped and rate-limit-aware
   6. contacts    hiring emails regex-extracted from JD text; company tiers classified
                  (enterprise/growth/startup/staffing)
                                                    ▼
                                        SQLite (jobscout.db, WAL)
                          jobs · companies · contacts · status_events (full history)
                                                    ▼
   dashboard.py  ─────────────────────────  THE DASHBOARD  ─────────────────────────────
   📋 Today          the daily brief with one-click apply/shortlist/dismiss per pick
   🔥 Fresh matches  scored cards, filters (market/salary/experience/tier/source)
   ✉️ Outreach       per-job contacts (JD-extracted, Firecrawl HR search, Hunter
                     patterns), drafted emails (Gemini or template — YOU send them),
                     plus speculative company prospects
   📊 Tracker        needs-action follow-up queue, pipeline stats, activity calendar
   📚 Study          the spaced-repetition study base (see below)
```

**Design principles:** archive, never delete (deleted rows would re-enter as
"new" on the next fetch); every status change is recorded in `status_events`
(powers the calendar and any future analytics); scraping is polite (delays,
budgets, back-off on any non-200) and login-free; emails are drafted, **never
sent** — that keeps outreach personal and you out of spam trouble.

## The agentic layer (optional, where it gets fun)

The deterministic app is complete on its own. But if you use
[Claude Code](https://claude.com/claude-code) (or any capable coding agent),
[`claude-extras/`](claude-extras/) upgrades it into a system that runs your
whole job-hunt morning for you:

```
your resume (ONE pdf you keep current — the only maintenance)
      │
      ▼   daily scheduled agent (11:00)
┌─────────────────────────────────────────────────────────────────┐
│ 1. runs the pipeline refresh                                    │
│ 2. reads your RESUME as ground truth (not the cached profile)   │
│ 3. reads the ~30 strongest fresh JDs and picks ~10 with real    │
│    judgment — each with a "why you" hook tying JD ⇆ resume      │
│ 4. writes briefs/TODAY.md → the dashboard's Today page, where   │
│    every pick has apply/shortlist/dismiss buttons inline        │
│ 5. maintains study/ — a cumulative interview-prep knowledge     │
│    base with spaced repetition (1/3/7/21 days):                 │
│      · topics derived from the JDs you're applying to           │
│      · resume cross-examination drills ("you claim X — prove    │
│        it") with model answers and weak-spot coaching           │
│ 6. flags drift between your resume and the scoring profile      │
└─────────────────────────────────────────────────────────────────┘
      │
      ▼
/teach-study — an interactive tutor skill: quiz-first teaching over the study
base, mental-model diagrams, and interviewer role-play on the resume drills
```

The daily loop this produces: **open the dashboard → 30-minute apply sprint
through pre-judged picks → done; the rest of the day is yours for studying**,
with the studying itself curated from your actual pipeline. Setup takes five
minutes: a copy-paste prompt for the routine and a one-file skill install —
see [claude-extras/README.md](claude-extras/README.md).

## Configuration

| File | What it controls |
|---|---|
| `data/config.yaml` | search queries, freshness, salary floors, market/eligibility rules, source toggles, LLM models & budgets |
| `data/profile.yaml` | **your** skills (core vs transferable vs anti), title weights, outreach highlights — this is what scoring means |
| `.env` | API keys (see `.env.example`; every key is optional) |

What each optional key unlocks: **Gemini** (free at aistudio.google.com) → JD
fact-extraction, personalized drafts, company-tier classification. **Firecrawl**
→ the Naukri source, HR-profile search, careers-page scraping. **Hunter** →
email-pattern lookup. **Adzuna/JSearch** → extra sources. Free-tier quotas are
respected by design: per-run budgets, pacing, one-extraction-per-JD-ever, and
429s parsed and backed off.

## Commands

```bash
python run.py                   # fetch → score → store
python run.py --source linkedin # one source
python run.py --contract-check  # are the endpoints still shaped as expected?
python run.py --prospects       # discover cold-mail target companies
python brief.py                 # deterministic daily brief (no agent needed)
```

## Tests

```bash
pip install -r requirements.txt          # dev deps (pytest, playwright)
pytest tests/unit                        # offline logic tests (fixtures)
pytest tests/e2e                         # Playwright against a seeded dashboard
```

## Disclaimers

- **Scraping**: sources are public pages and official APIs, fetched at low
  volume without logins. LinkedIn's guest pages are a terms-of-service gray
  area — this tool is deliberately gentle (small budgets, delays, back-off),
  but use your own judgment and keep volumes personal-scale.
- **Your data, your keys**: nothing leaves your machine except the fetches you
  configure and calls to APIs you supplied keys for.
- This tool never submits applications or sends email. The human does that.

## License

[MIT](LICENSE)
