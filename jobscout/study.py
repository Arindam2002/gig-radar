"""Study planner: turn the jobs DB into a curriculum.

Constraints (user decisions 2026-08-25):
- Demand statistics come from GOOD companies only - enterprise/growth tier
  (unknown allowed, staffing/startup excluded) - so prep tracks what mid-size
  companies and MNCs actually ask, not early-startup stacks.
- Two equal tracks: backend/.NET/system-design AND AI-infra. Prep is
  GENERIC (transferable interview skills), never tied to specific companies.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone

from .score import _pattern

# concepts the market asks for beyond his current profile - tracked so gaps
# in widely-demanded skills surface even when he has zero overlap today
MARKET_CONCEPTS = {
    "system design": ["system design", "distributed systems", "scalability",
                      "high availability", "low latency"],
    "aws": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "grpc": ["grpc"],
    "graphql": ["graphql"],
    "terraform": ["terraform", "infrastructure as code", "iac"],
    "caching": ["caching", "memcached", "cache"],
    "message queues": ["rabbitmq", "message queue", "sqs", "pub/sub", "pubsub"],
    "testing": ["unit testing", "integration testing", "tdd", "xunit", "pytest"],
    "concurrency": ["concurrency", "multithreading", "async", "parallelism"],
    "spring/java": ["spring boot", "java"],
    "golang": ["golang", "go lang"],
    "react": ["react", "next.js"],
    "elasticsearch": ["elasticsearch", "opensearch"],
    "spark": ["spark", "databricks"],
    "airflow": ["airflow"],
    "security": ["owasp", "security best practices", "encryption"],
}

AI_TRACK = {"llm", "inference", "gpu", "pytorch", "fine-tuning", "quantization",
            "rag", "agents"}
DOTNET_TRACK = {"c#", ".net", "entity framework", "azure", "cqrs"}

GOOD_TIERS = ("enterprise", "growth", "unknown", "")


def _tier_map(conn) -> dict:
    from .db import norm_company
    return {norm_company(r["name"]): (r["tier"] or "unknown")
            for r in conn.execute("SELECT name, tier FROM companies")}


def good_company_jobs(conn, min_score: float = 50) -> list:
    """High-scoring JDs at decent companies (enterprise/growth/unclassified)."""
    from .db import norm_company
    tiers = _tier_map(conn)
    rows = conn.execute(
        """SELECT title, company, description, skills, matched_skills, score
           FROM jobs WHERE score >= ? AND length(description) >= 200
           AND status NOT IN ('dismissed','archived')""", (min_score,)).fetchall()
    return [r for r in rows
            if tiers.get(norm_company(r["company"]), "unknown") in GOOD_TIERS]


def skill_demand(conn, profile: dict, min_score: float = 50) -> dict:
    """concept -> {'count', 'pct', 'have'} over good-company high-score JDs.

    'have': 'core' | 'transferable' | 'missing' (relative to profile).
    """
    jobs = good_company_jobs(conn, min_score)
    if not jobs:
        return {}
    concepts: dict[str, tuple[list, str]] = {}
    for name, variants in profile.get("core_skills", {}).items():
        concepts[name] = ([str(v) for v in variants], "core")
    for name, variants in profile.get("transferable_skills", {}).items():
        concepts[name] = ([str(v) for v in variants], "transferable")
    for name, variants in MARKET_CONCEPTS.items():
        if name not in concepts:
            concepts[name] = (variants, "missing")

    counts: Counter = Counter()
    for r in jobs:
        text = f"{r['title']} {r['description']} {r['skills'] or ''}".lower()
        for name, (variants, _) in concepts.items():
            if any(_pattern(v).search(text) for v in variants):
                counts[name] += 1
    n = len(jobs)
    return {name: {"count": c, "pct": round(100 * c / n, 1),
                   "have": concepts[name][1]}
            for name, c in counts.most_common() if c >= max(2, n * 0.05)}


def job_track(matched_skills: list[str]) -> str:
    m = set(matched_skills or [])
    ai = len(m & AI_TRACK)
    dn = len(m & DOTNET_TRACK)
    if ai and ai >= dn:
        return "ai"
    if dn:
        return "dotnet"
    return "backend"


def gap_report_md(conn, profile: dict, min_score: float = 50) -> str:
    demand = skill_demand(conn, profile, min_score)
    n_jobs = len(good_company_jobs(conn, min_score))
    if not demand:
        return "_Not enough good-company JDs collected yet - run a few refreshes first._"
    lines = [
        f"_Based on {n_jobs} high-scoring JDs at enterprise/growth companies "
        f"(staffing & early-startup JDs excluded)._",
        "",
        "| Skill | Demanded by | You | Priority |",
        "|---|---|---|---|",
    ]
    for name, d in list(demand.items())[:20]:
        if d["have"] == "missing" and d["pct"] >= 15:
            prio = "🔴 **learn**"
        elif d["have"] == "transferable" and d["pct"] >= 30:
            prio = "🟡 deepen"
        elif d["have"] == "core":
            prio = "🟢 strong - keep sharp"
        else:
            prio = "-"
        lines.append(f"| {name} | {d['pct']}% of JDs | {d['have']} | {prio} |")
    gaps = [n for n, d in demand.items() if d["have"] == "missing" and d["pct"] >= 15]
    if gaps:
        lines += ["", f"**This week's study focus:** {', '.join(gaps[:3])} - "
                  "highest demand at your target companies with no profile coverage."]
    return "\n".join(lines)


PREP_PROMPT = """You are a senior interview coach preparing a candidate for backend
and AI-infrastructure roles at MID-SIZE PRODUCT COMPANIES and MNCs (think Razorpay,
Atlassian, Microsoft, NVIDIA tier) - NOT early-stage startups. 2 years experience,
targeting SDE-2 level. Their stack: C#/.NET AND Python; differentiator: self-hosted
LLM inference infrastructure.

Today's demanded skills at their target companies (from real job descriptions):
{demand_summary}

Generate today's prep drop ({date}). Keep it GENERIC and transferable - classic
interview material of the kind large/mid-size companies actually ask. No company
names. Format as markdown:

## 🧮 DSA (2 problems)
State each problem (LeetCode-medium level, name the pattern). No solutions.
For each problem add a practice link: https://neetcode.io/problems/<slug> when
it is in the NeetCode roadmap, otherwise the leetcode.com problem URL.

## 🏗 System design (1 prompt)
One design prompt typical of SDE-2 interviews, with 3 probing follow-up questions.

## ⚙️ Backend rapid-fire (4 questions)
Mix of C#/.NET internals, SQL, concurrency, API design.

## 🤖 AI-infra differentiator (2 questions)
LLM serving / GPU / inference optimization questions an interviewer would ask
someone claiming production LLM experience.

## 🗣 Behavioral (1)
One STAR-format prompt tied to migration/reliability/scale stories.

Vary topics day to day (today's seed: {seed}). Total under 450 words."""


def daily_prep_md(conn, profile: dict, api_key: str, model: str) -> str:
    from .llm import generate
    demand = skill_demand(conn, profile)
    top = [f"{n} ({d['pct']}%)" for n, d in list(demand.items())[:12]]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = PREP_PROMPT.format(demand_summary=", ".join(top) or "n/a",
                                date=today, seed=today)
    return generate(prompt, api_key, model=model, timeout=90)
