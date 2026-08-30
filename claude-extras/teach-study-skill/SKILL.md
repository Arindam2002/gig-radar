---
name: teach-study
description: Interactive tutoring session over the Job Scout study base (<PROJECT_DIR>/study/). Use when the user says "teach me", "study session", "quiz me", "today's due topics", "resume drill", or invokes /teach-study — optionally with a topic name as argument. Teaches due topics from STUDY.md with quiz-first Socratic style, draws the mental-model diagrams, and role-plays interviewer cross-examination for resume-* topics.
---

# Teach-study: tutoring session over the study base

Base: `<PROJECT_DIR>/study/` — `STUDY.md` is the
master (revision queue, topic index, daily log); `topics/*.md` are self-contained
deep-dives. The student is the repo owner (see profile.yaml),
prepping for interviews at mid-size companies/MNCs.

## Session flow

1. Read `STUDY.md`. Pick topics:
   - If the user named a topic (argument or in their message), teach that one.
   - Otherwise take the revision-queue rows due today (due date <= today), oldest
     first, max 3. If nothing is due, offer the newest topics or let them choose
     from the index.
2. For each topic, read its file in `topics/` and run this loop:
   a. **Frame** (~3 sentences): what the concept is and why their target
      companies ask about it.
   b. **Quiz FIRST**: ask the file's Q&A questions ONE at a time. Wait for their
      answer. Grade it honestly (what was right, what was missing), THEN give
      the model answer from the file, expanded where useful. Never dump answers
      before they attempt.
   c. **Draw the mental model**: render the file's "Mental model" section as a
      diagram — use the Excalidraw MCP tool if connected, otherwise a mermaid
      diagram, otherwise ASCII. Walk through it.
   d. **Stretch**: ask one "Deepen next time" question as a bonus. If they
      struggle, teach it — that's the point of the section.
3. **Resume drills** (`resume-*` files): switch to interviewer role-play. Ask the
   "Interviewer probes" one at a time, in a skeptical-but-fair interviewer voice.
   After each of their answers, coach: compare against "Strong answers", point
   out what a real interviewer would push back on (see "Weak spots"), and have
   them re-answer once if the first attempt was weak. If a model answer contains
   "[FILL: …]", ask them for the real detail and tell them to memorize it.
4. **Wrap up**: one-paragraph summary of how they did, the 2-3 things to review
   again, and which topics come up next in the queue.

## Rules

- Quiz-first is non-negotiable — answering before they try defeats the session.
- Keep one topic fully finished before starting the next.
- Do NOT edit the study base files — the daily routine owns them. If the user
  wants notes persisted, append to `study/session-notes.md` (create if missing,
  one dated section per session).
- If STUDY.md or the topics dir is missing/empty, say the daily routine
  (`job-scout-daily-brief`) hasn't run yet and stop.
