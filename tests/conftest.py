import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperbot.config import KST, Topic  # noqa: E402
from paperbot.papers import Paper  # noqa: E402

NOW = datetime(2026, 10, 14, 7, 0, tzinfo=KST)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CLAUDE_MODEL",
                "OPENALEX_API_KEY", "CONTACT_EMAIL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def topic():
    return Topic(
        id="modeling",
        name="배터리 모델링",
        emoji="🧮",
        description="battery modeling",
        sources=[{"type": "arxiv", "query": "abs:battery"}],
        keywords=["model", "state of health"],
        count=3,
        priority_venues=["Journal of Power Sources"],
    )


def make_paper(title, days_ago=1, venue="arXiv", **kw):
    return Paper(
        title=title,
        url=kw.pop("url", f"https://example.org/{abs(hash(title))}"),
        published=NOW - timedelta(days=days_ago),
        source=kw.pop("source", venue),
        venue=venue,
        **kw,
    )


@pytest.fixture
def paper_factory():
    return make_paper


FIXTURES = Path(__file__).parent / "fixtures"
