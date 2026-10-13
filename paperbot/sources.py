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
ARXIV_RSS = "https://rss.arxiv.org/rss"
OPENALEX_ARXIV_SOURCE = "S4306400194"  # OpenAlex의 arXiv (Cornell University) 소스 ID
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
def fetch_arxiv(cfg: dict, session: requests.Session, max_lookback_days: int = 14,
                today: datetime | None = None) -> list[Paper]:
    """arXiv 논문을 여러 경로로 시도해 처음 결과가 나온 것을 쓴다.

    GitHub Actions에서는 arXiv 검색 API가 429/시간 초과로 막히고 RSS도 빈 피드를 주는 일이 잦아서,
    마지막에는 OpenAlex가 색인한 arXiv 논문(OPENALEX_ARXIV_SOURCE)으로 대신한다.
    """
    today = today or datetime.now(timezone.utc)
    routes = [("API", lambda: fetch_arxiv_api(cfg))]
    if cfg.get("rss_categories"):
        routes.append(("RSS", lambda: fetch_arxiv_rss(cfg, session)))
    if cfg.get("openalex_search"):
        routes.append(("OpenAlex", lambda: fetch_openalex(
            {"search": cfg["openalex_search"], "filter": f"primary_location.source.id:{OPENALEX_ARXIV_SOURCE}",
             "max_results": cfg.get("max_results", 50)},
            session, max_lookback_days, today)))
    last_exc: Exception | None = None
    for name, fetch in routes:
        try:
            got = fetch()
        except requests.RequestException as exc:
            log.warning("  arXiv %s 실패: %s", name, str(exc)[:160])
            last_exc = exc
            continue
        if got:
            log.info("  arXiv %s 경로로 수집", name)
            return got
        log.info("  arXiv %s: 0편", name)
    if last_exc and len(routes) == 1:
        raise last_exc
    return []


def fetch_arxiv_api(cfg: dict, attempts: int = 1) -> list[Paper]:
    # 공용 세션의 빠른 재시도는 arXiv 429를 악화시키므로 따로 천천히 재시도한다
    global _last_arxiv_call
    params = {
        "search_query": _one_line(cfg["query"]),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": int(cfg.get("max_results", 50)),
    }
    for attempt in range(attempts):
        wait = ARXIV_DELAY - (time.monotonic() - _last_arxiv_call)
        if wait > 0:
            time.sleep(wait)
        resp = requests.get(ARXIV_API, params=params, timeout=30, headers={"User-Agent": USER_AGENT})
        _last_arxiv_call = time.monotonic()
        if resp.status_code in (429, 503) and attempt + 1 < attempts:
            time.sleep(min(int(resp.headers.get("Retry-After", "10") or 10), 30))
            continue
        resp.raise_for_status()
        return parse_arxiv(resp.text)
    return []


def fetch_arxiv_rss(cfg: dict, session: requests.Session) -> list[Paper]:
    cats = "+".join(cfg["rss_categories"])
    resp = session.get(f"{ARXIV_RSS}/{cats}", timeout=30)
    resp.raise_for_status()
    return parse_arxiv_rss(resp.content, cfg.get("rss_require", []))


def _matches_groups(text: str, groups: list[list[str]]) -> bool:
    """그룹끼리는 AND, 그룹 안의 단어끼리는 OR."""
    text = text.lower()
    return all(any(w.lower() in text for w in group) for group in groups)


def parse_arxiv_rss(content: bytes | str, require: list[list[str]]) -> list[Paper]:
    feed = feedparser.parse(content)
    papers = []
    for e in feed.entries:
        desc = e.get("summary") or e.get("description") or ""
        if "announce type: replace" in desc.lower():  # 기존 논문의 새 버전은 제외
            continue
        title = clean_text(e.get("title"))
        abstract = clean_text(desc.split("Abstract:", 1)[1] if "Abstract:" in desc else desc)
        if not title or (require and not _matches_groups(f"{title} {abstract}", require)):
            continue
        aid = arxiv_id_from(e.get("link") or e.get("id"))
        published = _struct_to_dt(e.get("published_parsed") or e.get("updated_parsed"))
        if not aid or not published:
            continue
        creators = e.get("author") or e.get("dc_creator") or ""
        papers.append(
            Paper(
                title=title,
                url=f"https://arxiv.org/abs/{aid}",
                published=published,
                source="arXiv",
                authors=[a.strip() for a in creators.split(",") if a.strip()],
                abstract=abstract,
                venue="arXiv",
                arxiv_id=aid,
                pdf_url=f"https://arxiv.org/pdf/{aid}",
                categories=[t.get("term", "") for t in e.get("tags", [])],
            )
        )
    return papers


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
        "filter": f"from_publication_date:{start},to_publication_date:{end},type:article|preprint|review"
        + (f",{cfg['filter']}" if cfg.get("filter") else ""),
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
                url=(f"https://arxiv.org/abs/{aid}" if aid
                     else f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or w.get("id", ""))),
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
                pdf_url=(f"https://arxiv.org/pdf/{aid}" if aid else loc.get("pdf_url") or oa.get("pdf_url") or ""),
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
                got = fetch_arxiv(cfg, session, max_lookback_days, today)
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
