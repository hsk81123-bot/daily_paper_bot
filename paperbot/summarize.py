"""Claude로 논문 선별과 한국어 요약. 키가 없거나 실패하면 None을 돌려준다."""
from __future__ import annotations

import json
import logging
import os

from .config import DEFAULT_MODEL, Topic
from .papers import Paper

log = logging.getLogger(__name__)

MAX_CANDIDATES = 30
ABSTRACT_CHARS = 1500
# 서버 측 거절 시 대체 모델로 이어 받는 기능(fallbacks)을 지원하는 모델
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

SYSTEM = """당신은 배터리 연구자를 위해 매일 아침 최신 논문을 골라 소개하는 큐레이터입니다.
규칙:
- 반드시 주어진 제목·저자·저널·초록만 근거로 쓰세요. 초록에 없는 수치, 결론, 방법을 지어내지 마세요.
- 초록이 비어 있으면 제목에서 알 수 있는 범위만 짧게 쓰고, "초록 미제공"이라고 밝히세요.
- 초록 문장을 그대로 옮기지 말고 한국어로 다시 쓰세요. 전문 용어(DFN, SEI, SOH 등)는 영어 약어를 그대로 써도 됩니다.
- 출력은 지정된 JSON 형식만 사용하세요."""

SCHEMA = {
    "type": "object",
    "properties": {
        "selected": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title_ko": {"type": "string"},
                    "summary": {"type": "string"},
                    "points": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                },
                "required": ["index", "title_ko", "summary", "points", "why"],
                "additionalProperties": False,
            },
        },
        "digest": {"type": "string"},
    },
    "required": ["selected", "digest"],
    "additionalProperties": False,
}


def _candidate_block(i: int, p: Paper) -> str:
    abstract = p.abstract[:ABSTRACT_CHARS] + ("…" if len(p.abstract) > ABSTRACT_CHARS else "")
    return (
        f"[{i}] {p.title}\n"
        f"저자: {p.author_line(5) or '정보 없음'}\n"
        f"게재: {p.venue or p.source} · {p.published.date().isoformat()}\n"
        f"초록: {abstract or '(초록 미제공)'}"
    )


def build_prompt(topic: Topic, candidates: list[Paper]) -> str:
    blocks = "\n\n".join(_candidate_block(i, p) for i, p in enumerate(candidates))
    venues = ", ".join(topic.priority_venues[:12])
    return f"""주제: {topic.name}
주제 범위: {topic.description}

아래 후보 논문 {len(candidates)}편 중에서 이 주제의 연구자가 오늘 꼭 봐야 할 논문 {topic.count}편을 고르세요.
선정 기준:
1. 주제 범위와의 관련성(관련 없는 논문은 고르지 마세요. 관련 논문이 {topic.count}편보다 적으면 적게 골라도 됩니다)
2. 새로움과 영향력(새 방법·새 발견·실용적 의미). 참고로 영향력 있는 저널 예: {venues}
3. 초록이 있는 논문을 우선하세요(초록이 있어야 정확히 요약할 수 있습니다)
4. 여러 편을 고를 때는 같은 저널이나 arXiv에서 {topic.max_per_venue}편을 넘지 않게 섞기

고른 논문마다:
- index: 후보 번호
- title_ko: 자연스러운 한국어 제목
- summary: 3~4문장 한국어 요약(연구 배경과 문제, 접근 방법, 주요 결과)
- points: 핵심 정리 3~4개. 각 항목은 "목적: …", "방법: …", "결과: …", "의의: …" 형식의 한 문장.
  초록에 없는 항목(예: 구체적 수치가 없는 결과)은 지어내지 말고 아는 만큼만 쓰거나 빼세요.
- why: 이 논문이 왜 볼 만한지 한 문장
그리고 digest: 오늘 고른 논문들의 흐름을 묶은 "오늘의 한 줄 정리" 한 문장(한 편만 골랐다면 그 논문의 핵심을 한 문장으로).

후보 목록:
{blocks}"""


def _create(client, model: str, **kwargs):
    if model in FALLBACK_MODELS:
        return client.beta.messages.create(
            model=model,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **kwargs,
        )
    return client.messages.create(model=model, **kwargs)


def curate(topic: Topic, candidates: list[Paper], client=None) -> tuple[list[Paper], str] | None:
    """Claude가 고르고 요약한 논문 목록과 한 줄 정리를 돌려준다. 실패하면 None."""
    if not candidates:
        return None
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic

        client = anthropic.Anthropic(max_retries=3)
    candidates = candidates[:MAX_CANDIDATES]
    model = os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
    try:
        resp = _create(
            client,
            model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(topic, candidates)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            log.warning("Claude가 요청을 거절했습니다(%s). 기본 방식으로 고릅니다.", topic.name)
            return None
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 — API 실패해도 발송은 계속
        log.error("Claude 요약 실패(%s): %s", topic.name, exc)
        return None

    picked: list[Paper] = []
    seen: set[int] = set()
    for item in data.get("selected", []):
        idx = item.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(candidates) or idx in seen:
            continue
        seen.add(idx)
        p = candidates[idx]
        p.title_ko = (item.get("title_ko") or "").strip()
        p.summary = (item.get("summary") or "").strip()
        p.points = [x.strip() for x in item.get("points") or [] if isinstance(x, str) and x.strip()][:5]
        p.why = (item.get("why") or "").strip()
        picked.append(p)
        if len(picked) == topic.count:
            break
    return picked, (data.get("digest") or "").strip()
