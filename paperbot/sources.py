"""논문 수집: arXiv API, OpenAlex API, 일반 RSS."""
from __future__ import annotations

import calendar
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .papers import Paper, arxiv_id_from, clean_text, normalize_doi

log = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org/works"
USER_AGENT = "daily-paper-bot/1.0 (+https://github.com/hsk81123-bot/daily_paper_bot)"
ARXIV_DELAY = 3.0  # arXiv 이용 규칙: 요청 사이 3초 간격

_last_arxiv_call = 0.0


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    return s


def _struct_to_dt(st) -> datetime | None:
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)


def _one_line(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


# ---------------------------------------------------------------- arXiv
def fetch_arxiv(cfg: dict, session: requests.Session) -> list[Paper]:
    global _last_arxiv_call
    wait = ARXIV_DELAY - (time.monotonic() - _last_arxiv_call)
    if wait > 0:
        time.sleep(wait)
    params = {
        "search_query": _one_line(cfg["query"]),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": int(cfg.get("max_results", 50)),
    }
    resp = session.get(ARXIV_API, params=params, timeout=30)
    _last_arxiv_call = time.monotonic()
    resp.raise_for_status()
    return parse_arxiv(resp.text)


def parse_arxiv(xml: str) -> list[Paper]:
    feed = feedparser.parse(xml)
    papers = []
    for e in feed.entries:
        published = _struct_to_dt(e.get("published_parsed"))
        if not published or not e.get("title"):
            continue
        aid = arxiv_id_from(e.get("id", ""))
        pdf = next((l.get("href") for l in e.get("links", []) if l.get("title") == "pdf"), "")
        papers.append(
            Paper(
                title=clean_text(e.title),
                url=f"https://arxiv.org/abs/{aid}" if aid else e.get("link", ""),
                published=published,
                source="arXiv",
                authors=[a.get("name", "") for a in e.get("authors", []) if a.get("name")],
                abstract=clean_text(e.get("summary")),
                venue="arXiv",
                doi=normalize_doi(e.get("arxiv_doi")),
                arxiv_id=aid,
                pdf_url=pdf or (f"https://arxiv.org/pdf/{aid}" if aid else ""),
                categories=[t.get("term", "") for t in e.get("tags", [])],
            )
        )
    return papers


# ---------------------------------------------------------------- OpenAlex
def fetch_openalex(cfg: dict, session: requests.Session, max_lookback_days: int, today: datetime) -> list[Paper]:
    start = (today - timedelta(days=max_lookback_days)).date().isoformat()
    end = today.date().isoformat()
    params = {
        "search": _one_line(cfg["search"]),
        "filter": f"from_publication_date:{start},to_publication_date:{end},type:article|preprint|review",
        "sort": "publication_date:desc",
        "per-page": min(int(cfg.get("max_results", 50)), 200),
        "select": "id,doi,title,publication_date,authorships,primary_location,"
        "best_oa_location,abstract_inverted_index,type,ids",
    }
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    if os.environ.get("CONTACT_EMAIL"):
        params["mailto"] = os.environ["CONTACT_EMAIL"]
    resp = session.get(OPENALEX_API, params=params, timeout=30)
    resp.raise_for_status()
    return parse_openalex(resp.json())


def rebuild_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    positions = [(pos, word) for word, poss in inverted.items() for pos in poss]
    return " ".join(word for _, word in sorted(positions))


def parse_openalex(data: dict) -> list[Paper]:
    papers = []
    for w in data.get("results", []):
        title = clean_text(w.get("title"))
        date = w.get("publication_date")
        if not title or not date:
            continue
        doi = normalize_doi(w.get("doi"))
        loc = w.get("primary_location") or {}
        source = loc.get("source") or {}
        venue = source.get("display_name") or ""
        aid = arxiv_id_from(doi) or arxiv_id_from((w.get("ids") or {}).get("arxiv"))
        if aid or "arxiv" in venue.lower():
            venue = "arXiv"
        oa = w.get("best_oa_location") or {}
        papers.append(
            Paper(
                title=title,
                url=f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or w.get("id", "")),
                published=datetime.fromisoformat(date).replace(tzinfo=timezone.utc),
                source="OpenAlex",
                authors=[
                    a["author"]["display_name"]
                    for a in w.get("authorships", [])
                    if (a.get("author") or {}).get("display_name")
                ],
                abstract=clean_text(rebuild_abstract(w.get("abstract_inverted_index"))),
                venue=venue,
                doi="" if aid else doi,
                arxiv_id=aid,
                pdf_url=loc.get("pdf_url") or oa.get("pdf_url") or "",
            )
        )
    return papers


# ---------------------------------------------------------------- RSS
def fetch_rss(cfg: dict, session: requests.Session) -> list[Paper]:
    resp = session.get(cfg["url"], timeout=30)
    resp.raise_for_status()
    return parse_rss(resp.content, cfg)


def parse_rss(content: bytes | str, cfg: dict) -> list[Paper]:
    feed = feedparser.parse(content)
    name = cfg.get("name") or feed.feed.get("title", "RSS")
    required = [k.lower() for k in cfg.get("require_keywords", [])]
    papers = []
    for e in feed.entries:
        title = clean_text(e.get("title"))
        summary = clean_text(e.get("summary") or e.get("description"))
        if not title:
            continue
        if required and not any(k in f"{title} {summary}".lower() for k in required):
            continue
        published = _struct_to_dt(e.get("published_parsed") or e.get("updated_parsed"))
        if not published:
            continue
        link = e.get("link", "")
        doi = normalize_doi(e.get("prism_doi") or e.get("dc_identifier", "").removeprefix("doi:"))
        authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
        papers.append(
            Paper(
                title=title,
                url=link or (f"https://doi.org/{doi}" if doi else ""),
                published=published,
                source=name,
                authors=authors,
                abstract=summary,
                venue=cfg.get("venue", name),
                doi=doi,
                arxiv_id=arxiv_id_from(link),
            )
        )
    return papers


# ---------------------------------------------------------------- 공통
def collect(sources: list[dict], max_lookback_days: int, today: datetime,
            session: requests.Session | None = None) -> list[Paper]:
    """주제의 모든 소스를 수집한다. 한 소스가 실패해도 나머지는 계속한다."""
    session = session or make_session()
    papers: list[Paper] = []
    for cfg in sources:
        kind = cfg.get("type")
        label = cfg.get("name") or kind
        try:
            if kind == "arxiv":
                got = fetch_arxiv(cfg, session)
            elif kind == "openalex":
                got = fetch_openalex(cfg, session, max_lookback_days, today)
            elif kind == "rss":
                got = fetch_rss(cfg, session)
            else:
                log.warning("알 수 없는 소스 종류: %s", kind)
                continue
            log.info("  %s: %d편 수집", label, len(got))
            papers.extend(got)
        except Exception as exc:  # noqa: BLE001 — 한 소스 실패가 전체를 멈추지 않게
            log.error("  %s 수집 실패: %s", label, exc)
    return papers
