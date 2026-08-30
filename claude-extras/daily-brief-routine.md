# Daily brief routine — scheduled agent prompt

Fill in the three placeholders, then paste the whole prompt into Claude Code
(or another capable agent) and ask for a **daily scheduled task** (e.g. 11:00).

Placeholders:
- `<PROJECT_DIR>` — absolute path to your Job Scout clone
- `<RESUME_PDF>` — absolute path to the PDF you keep current (updating this one
  file is the only maintenance; the routine treats it as ground truth)
- `<ABOUT_YOU>` — one line: years of experience, target tracks, salary target,
  relocation stance

---

```text
You are generating the daily job-hunt brief AND maintaining the cumulative
study base for a candidate. <ABOUT_YOU>. Everything lives in <PROJECT_DIR>
(SQLite DB jobscout.db, WAL mode; venv at .venv — adjust commands if using
Docker: `docker compose exec jobscout python run.py`).

## Part 1 — Data & picks

1. **Read the resume — ground truth.** Read <RESUME_PDF> fully. Base all fit
   judgments and "why you" hooks on the resume's actual content, NOT on any
   cached profile. (profile.yaml is only the deterministic pipeline's config;
   if they disagree, trust the resume.)

2. **Refresh data**: run `.venv/bin/python run.py` from the project directory
   (timeout ~10 min). Individual source failures are fine; if the whole run
   fails, continue with existing data and note it.

3. **Gather candidates**: query jobscout.db for jobs with status='new', posted
   within the last 4 days (posted_at_epoch), score >= 35 (score is a LOOSE
   pre-filter — your judgment supersedes it). Join company tier via the
   companies table (match with jobscout.db.norm_company). Exclude staffing-tier
   companies from picks; prefer enterprise/growth tier; unknown is acceptable.

4. **Judge, don't count**: read the descriptions of the ~30 strongest
   candidates and pick about 10: skill fit against the resume, seniority fit,
   company quality, salary signal. Balance across the candidate's target
   tracks, plus 1-2 "abroad" roles (relocation-flagged first). Each pick gets
   one "why you" line — a concrete hook connecting the JD to the resume.

5. **Follow-ups due**: status='applied' older than 7 days, status='outreach'
   older than 4 days (status_updated_at).

6. **One cold-mail prospect**: from companies (status='prospect',
   suitability='good'), one line on why it fits the resume.

## Part 2 — Study base (cumulative, spaced repetition)

The study base lives at study/STUDY.md (master: revision queue, topic index,
daily log) and study/topics/<kebab-slug>.md (one deep-dive file per topic).
Read STUDY.md first.

7. **Choose today's topics** (4-5 total): 1-2 from the revision queue due today
   (spaced repetition: 1, 3, 7, then 21 days after last touched), 2 NEW
   technical topics derived from today's pick JDs (balanced over time across
   DSA patterns, system design, the candidate's backend stack, and their
   specialty; GENERIC mid-size-company/MNC interview style, never
   early-startup-specific), and 1 RESUME-DRILL topic (step 9).

8. **Write/extend technical topic files** in study/topics/:
   - NEW topic → create <slug>.md: `# <Topic>` / `## Concept` (first
     principles, ~200 words) / `## Why interviewers ask this` / `## Q&A` (3-4
     questions, each with a COMPLETE teaching answer — reasoning and
     trade-offs, not bullet fragments) / `## Mental model` (describe the
     diagram to draw, renderable by a teaching session) / `## Deepen next time`
     (2-3 harder unanswered follow-ups).
   - REVISION topic → open its file, ANSWER the "Deepen next time" questions,
     add 1-2 harder Q&As, refresh "Deepen next time". Never duplicate; deepen.

9. **Resume drill** — files named study/topics/resume-<slug>.md, one per major
   resume claim. Rotate: each day cover the next uncovered claim, or deepen one
   due for revision. Structure: `# Resume drill: <Claim>` / `## The claim`
   (quote the resume) / `## Interviewer probes` (5-6 cross-examination
   questions: how measured, alternatives considered, what breaks at 10x, YOUR
   part vs the team's) / `## Strong answers` (complete model answers in the
   candidate's voice, grounded ONLY in what the resume supports — mark gaps
   with "[FILL: …]") / `## Weak spots` / `## Deepen next time`.

10. **Update study/STUDY.md**: revision-queue table (due dates 1d/3d/7d/21d
    after today for topics touched today), topic-index table (track column),
    and PREPEND one daily-log entry.

## Part 3 — The brief

11. **Write the brief** to BOTH briefs/TODAY.md and briefs/YYYY-MM-DD.md.
    Start with "# 📋 Daily brief — <Weekday, DD Mon YYYY>". Sections: Apply
    sprint (why-you lines, job URLs as links) / Follow-ups due / One cold-mail
    prospect / 📚 Today's study (SHORT: topics with one-line hooks and
    study/topics/<slug>.md links) / optional "⚠️ Profile sync suggested" note
    if the resume and profile.yaml skills have clearly diverged (do NOT edit
    profile.yaml yourself). Keep the brief bounded — it is a 30-minute morning
    ritual, not homework. The dashboard's Today page renders TODAY.md and
    attaches apply/shortlist/dismiss buttons to every pick automatically.

## Hard constraints
- NEVER send emails or submit applications; never change job statuses; never
  modify config.yaml, profile.yaml, code, or the resume.
- Run run.py at most once. ~10 picks max. 4-5 study topics max per day.
- Study base edits are append/deepen only.
- If the DB is locked briefly, retry — the dashboard may be open (WAL).

Success = TODAY.md freshly written; topic files created/deepened with
complete-answer Q&As incl. one resume drill; STUDY.md queue, index, and daily
log updated consistently.
```
