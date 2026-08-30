from pathlib import Path

from jobscout.sources.base import (classify_market, eligible, looks_english,
                                   mentions_relocation, parse_pylist, query_match)
from jobscout.sources.naukri import parse_search_markdown

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CFG = {"eligibility": {"allow": ["worldwide", "anywhere", "global", "india", "apac", "asia"]}}


def test_naukri_fixture_parses():
    jobs = parse_search_markdown((FIXTURES / "naukri_search.md").read_text())
    assert len(jobs) >= 15
    assert all(j.title and j.company and j.url for j in jobs)
    assert all(j.url.startswith("https://www.naukri.com/job-listings-") for j in jobs)
    assert all(j.skills for j in jobs)
    # the fixture's one disclosed salary parses
    disclosed = [j for j in jobs if j.salary_min]
    assert disclosed and disclosed[0].salary_currency == "INR"
    # consultant "Posted by X" prefix stripped
    assert not any(j.company.lower().startswith("posted by") for j in jobs)
    # epochs sortable
    assert all(isinstance(j.extra.get("posted_at_epoch"), int) for j in jobs)


def test_naukri_malformed_block_skipped():
    md = "## [Broken](https://www.naukri.com/job-listings-x\n\ngarbage\n\n## [t](no-url)"
    assert parse_search_markdown(md) == []


def test_eligibility_gate():
    assert eligible("", CFG)
    assert eligible("Worldwide", CFG)
    assert eligible("India, APAC", CFG)
    assert not eligible("United States", CFG)
    assert not eligible("['Brazil']", CFG)
    assert not eligible("USA, Canada", CFG)


def test_parse_pylist():
    assert parse_pylist("['United States']") == ["United States"]
    assert parse_pylist("None") == []
    assert parse_pylist(None) == []
    assert parse_pylist(["a"]) == ["a"]
    assert parse_pylist("[-1, 0]") == [-1, 0]
    assert parse_pylist("garbage[") == []


def test_classify_market():
    assert classify_market("", CFG) == "ok"
    assert classify_market("Worldwide", CFG) == "ok"
    assert classify_market("United States", CFG) == "abroad"
    assert classify_market("Germany", CFG) == "abroad"
    assert classify_market("USA, Canada", CFG) == "abroad"


def test_mentions_relocation():
    assert mentions_relocation("We offer visa sponsorship for the right candidate")
    assert mentions_relocation("Relocation assistance provided to Berlin")
    assert mentions_relocation("we are willing to sponsor work visas")
    # negations must NOT count
    assert not mentions_relocation("There is no sponsorship available at this time")
    assert not mentions_relocation("Unfortunately we cannot offer visa sponsorship")
    assert not mentions_relocation("standard backend role in our Pune office")
    assert not mentions_relocation("")


def test_query_match_title_only():
    queries = ["backend engineer", "llm engineer"]
    assert query_match("Senior Backend Developer", queries)
    assert query_match("LLM Inference Engineer", queries)
    assert query_match("Python Developer", queries)
    assert not query_match("Quantity Surveyor", queries)
    assert not query_match("Retail Store Associate", queries)
    assert not query_match("building cleaner", queries)


def test_looks_english():
    assert looks_english("Senior Backend Engineer")
    assert not looks_english("Empfangsmitarbeiter (m/w/d) für Morsbach gesucht!")
    assert not looks_english("Werkstudent IT Operations")
    assert looks_english("Working Student IT Operations (m/f/d)")
