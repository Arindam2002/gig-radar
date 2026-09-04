# Claude extras - the intelligent layer

The Docker/local app is fully functional on its own (deterministic pipeline,
dashboard, `brief.py`). This folder adds the **agentic layer** for people who
use [Claude Code](https://claude.com/claude-code) (or any coding agent that can
run shell commands and edit files on a schedule):

| Extra | What it does |
|---|---|
| [daily-brief-routine.md](daily-brief-routine.md) | A scheduled task prompt: every morning an agent refreshes the data, reads your **resume as ground truth**, judges the fresh jobs (not just keyword scores), writes a bounded apply-sprint brief with a "why you" hook per pick, and maintains a spaced-repetition study base with interview prep derived from the JDs you're actually applying to. |
| [tutor-skill/SKILL.md](tutor-skill/SKILL.md) | A `/tutor` skill: an interactive tutor over the study base - quiz-first Socratic teaching, diagram drawing, and interviewer role-play for the resume cross-examination drills. |

## Setup (Claude Code)

**Daily routine:** open Claude Code in the project directory, paste the prompt
from `daily-brief-routine.md` (fill in the placeholders first), and say:
*"Create a daily scheduled task at 11am with this prompt."* Claude will register
it; it runs whenever the app is open (or on next launch).

**Tutor skill:** copy the folder into your skills directory:

```bash
mkdir -p ~/.claude/skills/tutor
cp claude-extras/tutor-skill/SKILL.md ~/.claude/skills/tutor/SKILL.md
```

Edit the `Base:` path inside it to your clone's location. Then `/tutor`
works in any Claude Code session.

## Not using Claude?

The prompt in `daily-brief-routine.md` is agent-agnostic - any agent that can
read files, run `python run.py`, query SQLite, and write markdown can execute
it. Point your agent of choice at it on whatever scheduler you like.
