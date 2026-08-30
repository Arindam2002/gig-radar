"""Match scoring: 0-100, fully local.

  up to 55: core-skill overlap, SATURATING at 4 concept hits - a focused
            AI-infra JD with 5 core concepts beats an enterprise JD listing
            18 commodity technologies (rev-1 ranked those backwards).
  up to 15: transferable-skill coverage (fractional).
  up to 30: title fit - weighted MAX over patterns, not a hit count.
  penalties: anti_skills (-8 each, capped -24), anti_titles (-25),
             blocklisted company -> 0.

Description-poor rows (stage-1 LinkedIn cards) get a title-driven provisional
score instead of being buried near zero; run.py flags them needs_detail and
enriches the best ones.
"""
import re
from functools import lru_cache


@lru_cache(maxsize=2048)
def _pattern(variant: str) -> re.Pattern:
    # flexible separators for "fine-tuning"/"fine tuning"/"fine_tuning",
    # boundaries that survive "c#", ".net", "node.js"
    esc = re.escape(variant.lower())
    esc = esc.replace(r"\ ", r"[\s\-_]+").replace(r"\-", r"[\s\-_]+")
    return re.compile(rf"(?<![a-z0-9]){esc}(?![a-z0-9#])")


def _concept_hits(concepts: dict[str, list[str]], text: str) -> list[str]:
    hits = []
    for concept, variants in concepts.items():
        if any(_pattern(str(v)).search(text) for v in variants):
            hits.append(concept)
    return hits


def score_job(title: str, description: str, skills_tags: list[str],
              company: str, profile: dict, cfg: dict) -> tuple[float, list[str]]:
    """Returns (score 0-100, matched core+transferable concept names)."""
    company_l = (company or "").lower()
    for blocked in cfg.get("companies", {}).get("blocklist", []):
        if blocked.lower() in company_l:
            return 0.0, []

    title_l = (title or "").lower()
    text = f"{title_l} {description or ''} {' '.join(skills_tags or [])}".lower()

    core = _concept_hits(profile.get("core_skills", {}), text)
    transferable = _concept_hits(profile.get("transferable_skills", {}), text)

    core_pts = min(1.0, len(core) / 4) * 55
    n_trans = len(profile.get("transferable_skills", {})) or 1
    trans_pts = (len(transferable) / n_trans) * 15

    title_pts = max(
        (w for pat, w in profile.get("title_weights", {}).items() if pat in title_l),
        default=0)

    penalty = 0.0
    anti_hits = [a for a in profile.get("anti_skills", []) if _pattern(str(a)).search(text)]
    penalty += min(24.0, 8.0 * len(anti_hits))
    if any(a in title_l for a in profile.get("anti_titles", [])):
        penalty += 25.0

    boost = 0.0
    for boosted in cfg.get("companies", {}).get("boost", []):
        if boosted.lower() in company_l:
            boost = 10.0
            break

    thin = len(description or "") < 100 and not skills_tags
    if thin:
        # provisional, title-driven: an "AI Infrastructure Engineer" card
        # without a JD yet should still surface (rev-1 buried these at 7/100)
        score = min(60.0, title_pts * 2.0) - penalty + boost
    else:
        score = core_pts + trans_pts + title_pts - penalty + boost

    return round(max(0.0, min(100.0, score)), 1), core + transferable
