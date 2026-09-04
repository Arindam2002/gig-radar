# gig-radar

I vibe coded my entire job hunt, and honestly, it worked.

gig-radar is a little radar that runs on your machine and quietly sweeps job
boards so you don't have to. It scrapes fresh listings from free sources,
scores them against your actual profile, digs up public HR contacts, drafts
your cold emails, and tracks every application so nothing rots in a
spreadsheet. Hook it up to an AI agent and it even writes you a daily
"apply to these 10, here's why you'd get each one" brief, then builds an
interview prep curriculum out of the exact jobs you're applying to.

Everything runs locally. Your data stays in a folder on your machine. Your API
keys are yours. It never sends or submits anything on your behalf, you stay
the human who hits send.

## Get it running

```bash
git clone https://github.com/<you>/gig-radar && cd gig-radar
cp .env.example .env          # add your keys (all optional)
docker compose up --build     # then open http://localhost:8501
```

First run drops two editable files into `./data/`: `config.yaml` (what to
search, salary floors, filters) and `profile.yaml` (your skills, this is what
"match score" means, so actually fill it in). Everything the radar picks up
lives in `./data/` too.

<details>
<summary>Running without Docker</summary>

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-runtime.txt
cp config.example.yaml config.yaml && cp profile.example.yaml profile.yaml
cp .env.example .env
.venv/bin/python run.py
.venv/bin/python -m streamlit run dashboard.py
```

The Naukri source and contact search also need Node.js (for the Firecrawl CLI)
plus a `FIRECRAWL_API_KEY`.
</details>

## How it works

```
                        SOURCES (all free)
   LinkedIn guest pages (two stage: search cards, then full JDs,
   polite and low volume) . Naukri via Firecrawl JS rendering .
   Himalayas . Remotive . RemoteOK . Arbeitnow . [Adzuna / JSearch]
                              |
                              v
  run.py ------------------ THE PIPELINE ------------------------
  1. normalize   salary text to comparable numbers, timestamps to epochs
  2. dedupe      identity = company+title+city hash plus URL hash;
                 re-fetches merge instead of duplicating, and a dismissed
                 job that gets re-posted fresh comes back
  3. gate        remote roles you can't take get tagged "abroad" instead
                 of hidden (relocation mentions get flagged); junk and
                 stale listings auto-archive so the pool stays honest
  4. score       0 to 100, fully local: core skill overlap (saturating,
                 so depth beats keyword soup) + title fit - anti signals.
                 salary filters, it never scores
  5. enrich      Gemini reads each JD once, ever, and extracts years
                 required, salary, seniority, eligibility (budget capped,
                 rate limit aware)
  6. contacts    hiring emails pulled from JD text; companies classified
                 into enterprise / growth / startup / staffing tiers
                              |
                              v
                 SQLite (one file, WAL mode)
      jobs . companies . contacts . full status history
                              |
                              v
  dashboard.py ------------ THE DASHBOARD -----------------------
  Today          the daily brief, with one-click apply/shortlist/dismiss
  Fresh matches  scored cards with filters (market, salary, experience,
                 company tier, source)
  Outreach       contacts per job, drafted emails you copy and send
                 yourself, plus speculative "any openings?" prospects
  Tracker        follow-up queue, pipeline stats, activity calendar
  Study          the interview prep base (see below): each topic opens
                 on its own page where you can highlight text, leave
                 notes on it, and mark the topic studied
```

Design choices worth knowing: nothing gets deleted, only archived (deleted
rows would just sneak back in as "new" on the next fetch). Every status change
is recorded forever, which powers the calendar. Scraping is deliberately
gentle: small budgets, delays, instant back-off, no logins. And emails are
drafted, never sent, because outreach only works when it's actually from you.

## The agent layer (optional, where it gets fun)

The app above is complete on its own. But if you use Claude Code or any coding
agent that can run commands on a schedule, the [claude-extras](claude-extras/)
folder turns gig-radar into a system that runs your whole morning:

```
your resume (one pdf you keep current, that's the only maintenance)
     |
     v    daily scheduled agent
  1. refreshes the data
  2. reads your RESUME as ground truth, not some cached config
  3. reads the ~30 strongest fresh JDs and picks ~10 with actual
     judgment, each with a "why you" line tying the JD to your resume
  4. writes the brief that shows up on the Today page with
     apply buttons already attached
  5. maintains a spaced repetition study base (1/3/7/21 days):
     interview topics derived from the JDs you're applying to, plus
     cross-examination drills on your own resume claims ("you say you
     cut costs 40%, prove it") with model answers and weak spot notes
     |
     v
  /teach-study, a tutor skill: quiz-first teaching over the study
  base, diagrams, and interviewer role-play on the resume drills
```

The daily loop this creates: open the dashboard, do a 30 minute apply sprint
through pre-judged picks, done. The rest of the day is yours for studying, and
the studying itself is curated from your actual pipeline. Setup is a
copy-paste prompt and a one-file skill install, see
[claude-extras/README.md](claude-extras/README.md).

## Config

| File | What it controls |
|---|---|
| `data/config.yaml` | search queries, freshness, salary floors, eligibility rules, source toggles, LLM budgets |
| `data/profile.yaml` | your skills (core vs transferable vs "never again"), title weights, outreach highlights |
| `.env` | API keys, see `.env.example`, every single one is optional |

What each key unlocks: Gemini (free at aistudio.google.com) gets you JD fact
extraction, personalized drafts, and company tier classification. Firecrawl
gets you the Naukri source and HR contact search. Hunter gets you email
pattern lookup. Adzuna and JSearch add extra sources. Free tier quotas are
respected by design: per-run budgets, pacing, one extraction per JD ever, and
429s parsed and backed off properly.

## Commands

```bash
python run.py                   # fetch, score, store
python run.py --source linkedin # just one source
python run.py --contract-check  # are the endpoints still shaped right?
python run.py --prospects       # find cold-mail target companies
python brief.py                 # deterministic daily brief, no agent needed
```

## Tests

```bash
pip install -r requirements.txt   # dev deps (pytest, playwright)
pytest tests/unit                 # offline logic tests against fixtures
pytest tests/e2e                  # playwright against a seeded dashboard
```

## The fine print

Sources are public pages and official APIs, fetched slowly and without logins.
LinkedIn's logged-out pages are a terms-of-service gray area; this tool keeps
volumes personal-scale on purpose, but use your own judgment. Nothing leaves
your machine except the fetches you configured and calls to APIs you gave keys
for. And once more for the record: it never applies or emails for you. That
part is your job. It just makes it a 30 minute job.

## License

[MIT](LICENSE)
