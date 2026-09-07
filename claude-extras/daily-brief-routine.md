# Daily brief routine - scheduled agent prompt

Fill in the three placeholders, then paste the whole prompt into Claude Code
(or another capable agent) and ask for a **daily scheduled task** (e.g. 11:00).

Placeholders:
- `<PROJECT_DIR>` - absolute path to your Job Scout clone
- `<RESUME_PDF>` - absolute path to the PDF you keep current (updating this one
  file is the only maintenance; the routine treats it as ground truth)
- `<ABOUT_YOU>` - one line: years of experience, target tracks, salary target,
  relocation stance

---

```text
You are generating the daily job-hunt brief AND maintaining the cumulative
study base for a candidate. <ABOUT_YOU>. Everything lives in <PROJECT_DIR>
(SQLite DB jobscout.db, WAL mode; venv at .venv - adjust commands if using
Docker: `docker compose exec jobscout python run.py`).

## Part 1 - Data & picks

1. **Read the resume - ground truth.** Read <RESUME_PDF> fully. Base all fit
   judgments and "why you" hooks on the resume's actual content, NOT on any
   cached profile. (profile.yaml is only the deterministic pipeline's config;
   if they disagree, trust the resume.)

2. **Refresh data**: run `.venv/bin/python run.py` from the project directory
   (timeout ~10 min). Individual source failures are fine; if the whole run
   fails, continue with existing data and note it.

3. **Gather candidates**: query jobscout.db for jobs with status='new', posted
   within the last 4 days (posted_at_epoch), score >= 35 (score is a LOOSE
   pre-filter - your judgment supersedes it). Join company tier via the
   companies table (match with jobscout.db.norm_company). Exclude staffing-tier
   companies from picks; prefer enterprise/growth tier; unknown is acceptable.

4. **Judge, don't count**: read the descriptions of the ~30 strongest
   candidates and pick about 10: skill fit against the resume, seniority fit,
   company quality, salary signal. Balance across the candidate's target
   tracks, plus 1-2 "abroad" roles (relocation-flagged first). Each pick gets
   one "why you" line - a concrete hook connecting the JD to the resume.

5. **Follow-ups due**: status='applied' older than 7 days, status='outreach'
   older than 4 days (status_updated_at).

6. **One cold-mail prospect**: from companies (status='prospect',
   suitability='good'), one line on why it fits the resume.

## Part 2 - Study base (cumulative, spaced repetition, PACED BY THE USER)

The study base lives at study/STUDY.md (master: revision queue, topic index,
daily log) and study/topics/<kebab-slug>.md (one deep-dive file per topic).
Read STUDY.md first.

7. **Read the user's completion state**: query the study_progress table in
   jobscout.db (columns: slug, completed_at). A topic counts as STUDIED only
   if completed_at is newer than the topic file's mtime. Respect their pace:
   never deepen an un-studied topic (keep it due, roll +2 days, mark it
   "waiting on you"); if 5+ topics are un-studied, create at most ONE new
   topic today instead of two. NEVER write to study_progress yourself - only
   the user ticks topics off, on the dashboard's Study page.

8. **Choose today's topics** (2-5 total, respecting rule 7): 1-2 STUDIED
   topics from the revision queue due today
   (spaced repetition: 1, 3, 7, then 21 days after last touched), 2 NEW
   technical topics derived from today's pick JDs (balanced over time across
   DSA patterns, system design, the candidate's backend stack, and their
   specialty; GENERIC mid-size-company/MNC interview style, never
   early-startup-specific), and 1 RESUME-DRILL topic (step 9).

9. **Write/extend technical topic files** in study/topics/:
   - **Every topic file starts with a YAML frontmatter block, before the
     `# ` heading.** It is the only metadata the tooling has, so it is not
     optional and it is never dropped, reordered or reformatted:
     ```
     ---
     track: llm-infra          # dsa | backend | system-design | llm-infra | resume
     tags: [rag, reranking]    # 2-5 lowercase kebab-case tags
     related: [llm-evaluation-and-guardrails, sql-indexing-query-performance]
     created: 2026-01-31       # ISO date, set once and never touched again
     updated: 2026-01-31       # ISO date, bumped on every revision
     ---
     ```
     No `title` key - the `# ` heading is the title. `related` holds slugs of
     topics that ALREADY have files: at least 2 for a technical topic, and
     the reason the graph is worth drawing. Resume drills need none.
   - NEW topic → create <slug>.md: frontmatter / `# <Topic>` / `## Concept` (first
     principles, ~200 words) / `## Why interviewers ask this` / `## Q&A` (3-4
     questions, each with a COMPLETE teaching answer - reasoning and
     trade-offs, not bullet fragments) / `## Mental model` (describe the
     diagram to draw, renderable by a teaching session) / `## Deepen next time`
     (2-3 harder unanswered follow-ups).
   - REVISION topic (studied ones only) → open its file, ANSWER the "Deepen
     next time" questions, add 1-2 harder Q&As, refresh "Deepen next time".
     Never duplicate; deepen. Keep the frontmatter block, set `updated` to
     today, and leave `created` alone.
   - **Every technical topic carries a flashcard deck** in a sidecar file at
     study/cards/<slug>.md, never inside the topic file. A NEW topic gets a
     deck of 6 to 10 cards written the same day; a REVISION adds 1 or 2 cards
     for the new material ONLY and never rewrites or reorders the cards that
     are already there (the reader shuffles them, the user is learning them).
     One fact per card, answers under 40 words, questions phrased the way an
     interviewer would ask them out loud. The shape is exact, because a
     parser reads it:
     ```
     - Q: What does a token bucket bound?
       A: Burst (capacity b) and sustained rate (r/sec); both are the SLO knobs.
     ```
     A "- Q:" line opens a card, the indented "A:" line answers it, and an
     answer may run on over further indented lines. Resume drills get no deck.
   - **Link as you write.** When a deepened answer leans on another topic,
     do BOTH: add that slug to `related` (if it is not already there), and
     put an inline `[text](<slug>.md)` link in the answer text itself where
     the reader would want it. The link and the list are the same graph seen
     twice - the list is the deliberate map, the inline link is what makes
     the sentence useful. Never link a slug with no file.
   - **DSA topics: every named problem carries a practice link.** Use
     https://neetcode.io/problems/<slug> for problems in the NeetCode roadmap,
     otherwise the leetcode.com URL. Add a `## Practice` section listing the
     pattern's problems with links, easy to hard.

10. **Resume drill** - files named study/topics/resume-<slug>.md, one per major
   resume claim. Rotate: each day cover the next uncovered claim, or deepen one
   due for revision. Same frontmatter block as rule 9 (`track: resume`;
   `related` may stay empty). Structure: `# Resume drill: <Claim>` / `## The claim`
   (quote the resume) / `## Interviewer probes` (5-6 cross-examination
   questions: how measured, alternatives considered, what breaks at 10x, YOUR
   part vs the team's) / `## Strong answers` (complete model answers in the
   candidate's voice, grounded ONLY in what the resume supports - mark gaps
   with "[FILL: …]") / `## Weak spots` / `## Deepen next time`.

11. **Update study/STUDY.md**: revision-queue table (due dates 1d/3d/7d/21d
    after today for topics touched today), topic-index table (track column),
    and PREPEND one daily-log entry.

## Part 3 - The brief AND the picks manifest

12. **Write the brief** to BOTH briefs/TODAY.md and briefs/YYYY-MM-DD.md.
    Start with "# 📋 Daily brief - <Weekday, DD Mon YYYY>". Sections: Apply
    sprint (why-you lines, job URLs as links) / Follow-ups due / One cold-mail
    prospect / 📚 Today's study (SHORT: topics with one-line hooks and
    study/topics/<slug>.md links) / optional "⚠️ Profile sync suggested" note
    if the resume and profile.yaml skills have clearly diverged (do NOT edit
    profile.yaml yourself). Keep the brief bounded - it is a 30-minute morning
    ritual, not homework. Prose format and layout are yours to choose freely.

13. **MANDATORY - write the picks manifest.** The dashboard attaches
    apply/shortlist/dismiss buttons from this file, NOT from your prose, so
    this is a hard contract: write briefs/TODAY.picks.json AND
    briefs/YYYY-MM-DD.picks.json - a JSON array with exactly one object per
    apply-sprint pick: {"url": "<exact listing URL linked in the brief>",
    "title": "<job title as stored in the jobs table>", "company": "<company
    as stored in the jobs table>"}. Copy title/company from the DB rows, not
    from your own rephrasing. A brief without its manifest is incomplete.

## Hard constraints
- NEVER send emails or submit applications; never change job statuses; never
  modify config.yaml, profile.yaml, code, or the resume.
- Run run.py at most once. ~10 picks max. 2-5 study topics per day per rule 7.
- Study base edits are append/deepen only.
- If the DB is locked briefly, retry - the dashboard may be open (WAL).

Success = TODAY.md freshly written AND TODAY.picks.json lists every pick; topic files created/deepened with
complete-answer Q&As incl. one resume drill; STUDY.md queue, index, and daily
log updated consistently.
```
