from datetime import timedelta

from conftest import FIXTURES, NOW
from paperbot import sources
from paperbot.papers import arxiv_id_from, dedupe, normalize_doi


def test_parse_arxiv():
    papers = sources.parse_arxiv((FIXTURES / "arxiv.xml").read_text())
    assert len(papers) == 2
    p = papers[0]
    assert p.title == "A Reduced-Order Electrochemical Model for Fast-Charging Lithium-Ion Batteries"
    assert p.arxiv_id == "2610.01234"
    assert p.url == "https://arxiv.org/abs/2610.01234"
    assert p.pdf_url == "http://arxiv.org/pdf/2610.01234v2"
    assert p.authors == ["Jane Kim", "Lee Park"]
    assert p.doi == "10.1149/1945-7111/abc123"
    assert p.venue == "arXiv"
    assert p.published.date().isoformat() == "2026-10-12"


def test_parse_openalex():
    import json

    papers = sources.parse_openalex(json.loads((FIXTURES / "openalex.json").read_text()))
    assert len(papers) == 2  # 제목 없는 항목은 제외
    jps, pre = papers
    assert jps.title == "Thermal model of pouch cells"
    assert jps.abstract == "We heat model generation."
    assert jps.venue == "Journal of Power Sources"
    assert jps.doi == "10.1016/j.jpowsour.2026.111111"
    assert jps.url == "https://doi.org/10.1016/j.jpowsour.2026.111111"
    assert pre.arxiv_id == "2610.05555" and pre.venue == "arXiv" and pre.doi == ""


def test_parse_rss_keyword_filter():
    papers = sources.parse_rss((FIXTURES / "nature_energy.rss").read_bytes(),
                               {"name": "Nature Energy", "require_keywords": ["battery", "batteries"]})
    assert [p.title for p in papers] == ["Anode-free sodium batteries with long cycle life"]
    assert papers[0].doi == "10.1038/s41560-026-00001-1"
    assert papers[0].abstract == "A new electrolyte enables anode-free cells."


def test_identifier_helpers():
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert arxiv_id_from("http://arxiv.org/abs/2401.12345v3") == "2401.12345"
    assert arxiv_id_from("10.48550/arXiv.2401.12345") == "2401.12345"
    assert arxiv_id_from("10.1016/j.jpowsour.2026.1234") == ""


def test_dedupe_merges_arxiv_and_openalex_copies():
    import json

    a = sources.parse_arxiv((FIXTURES / "arxiv.xml").read_text())
    o = sources.parse_openalex(json.loads((FIXTURES / "openalex.json").read_text()))
    merged = dedupe(a + o)
    assert len(merged) == 3
    pinn = next(p for p in merged if p.arxiv_id == "2610.05555")
    assert pinn.pdf_url


def test_dedupe_similar_title_prefers_journal(paper_factory):
    pre = paper_factory("Fast charging model for Li-ion cells", venue="arXiv", arxiv_id="2610.1", pdf_url="pdf")
    pub = paper_factory("Fast-charging model for Li-ion cells.", venue="Journal of Power Sources", doi="10.1/x")
    [m] = dedupe([pre, pub])
    assert m.venue == "Journal of Power Sources"
    assert m.arxiv_id == "2610.1" and m.doi == "10.1/x" and m.pdf_url == "pdf"


def test_collect_continues_after_failure(monkeypatch):
    def boom(cfg, session):
        raise RuntimeError("network down")

    monkeypatch.setattr(sources, "fetch_arxiv", boom)
    monkeypatch.setattr(sources, "fetch_rss", lambda cfg, s: sources.parse_rss(
        (FIXTURES / "nature_energy.rss").read_bytes(), cfg))
    got = sources.collect([{"type": "arxiv", "query": "x"}, {"type": "rss", "url": "u", "name": "NE"}],
                          14, NOW, session=object())
    assert len(got) == 2


def test_fetch_openalex_builds_filter(monkeypatch):
    calls = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    class Session:
        def get(self, url, params, timeout):
            calls.update(params)
            return Resp()

    monkeypatch.setenv("OPENALEX_API_KEY", "k")
    sources.fetch_openalex({"search": '"battery model"\n OR PyBaMM'}, Session(), 14, NOW)
    start = (NOW - timedelta(days=14)).date().isoformat()
    assert calls["search"] == '"battery model" OR PyBaMM'
    assert f"from_publication_date:{start}" in calls["filter"]
    assert calls["api_key"] == "k"
