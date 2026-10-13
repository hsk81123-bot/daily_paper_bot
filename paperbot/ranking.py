"""후보 필터링과 키 없이 고르는 기본 선정 규칙."""
from __future__ import annotations

from datetime import datetime, timedelta

from .config import Topic
from .papers import Paper


def within(papers: list[Paper], days: int, today: datetime) -> list[Paper]:
    # 날짜만 있는 소스(OpenAlex)도 있으므로 하루 여유를 둔다
    cutoff = today - timedelta(days=days + 1)
    return [p for p in papers if p.published >= cutoff]


def candidates_for(topic: Topic, papers: list[Paper], today: datetime) -> list[Paper]:
    """기본 기간 안에 후보가 부족하면 최대 기간까지 늘린다."""
    recent = within(papers, topic.lookback_days, today)
    if len(recent) >= topic.count:
        return recent
    return within(papers, topic.max_lookback_days, today)


def _is_priority(venue: str, priority: list[str]) -> bool:
    v = venue.lower()
    return any(p.lower() == v for p in priority)


def score(paper: Paper, topic: Topic, today: datetime) -> float:
    title = paper.title.lower()
    abstract = paper.abstract.lower()
    s = 0.0
    for kw in topic.keywords:
        k = kw.lower()
        if k in title:
            s += 2
        elif k in abstract:
            s += 0.5
    if _is_priority(paper.venue, topic.priority_venues):
        s += 3
    if paper.abstract:
        s += 1
    age_days = max((today - paper.published).total_seconds() / 86400, 0)
    s -= 0.3 * age_days
    return s


def diversify(ranked: list[Paper], count: int, max_per_venue: int) -> list[Paper]:
    """출처당 최대 편수를 지키며 순서대로 고른다. 모자라면 제한을 풀고 채운다."""
    picked: list[Paper] = []
    per_venue: dict[str, int] = {}
    for p in ranked:
        venue = p.venue or p.source
        if per_venue.get(venue, 0) >= max_per_venue:
            continue
        picked.append(p)
        per_venue[venue] = per_venue.get(venue, 0) + 1
        if len(picked) == count:
            return picked
    for p in ranked:
        if len(picked) == count:
            break
        if p not in picked:
            picked.append(p)
    return picked


def rank(papers: list[Paper], topic: Topic, today: datetime) -> list[Paper]:
    return sorted(papers, key=lambda p: (score(p, topic, today), p.published), reverse=True)


def basic_select(papers: list[Paper], topic: Topic, today: datetime) -> list[Paper]:
    return diversify(rank(papers, topic, today), topic.count, topic.max_per_venue)
