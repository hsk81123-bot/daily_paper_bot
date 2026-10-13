"""설정 파일(config/topics.yaml)과 환경변수 로딩."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "topics.yaml"
DEFAULT_HISTORY = ROOT / "data" / "history.json"

KST = timezone(timedelta(hours=9))
DEFAULT_MODEL = "claude-opus-5-5"


def now_kst() -> datetime:
    return datetime.now(KST)


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """의존성 없이 .env를 읽는다. 이미 설정된 환경변수는 덮어쓰지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Topic:
    id: str
    name: str
    emoji: str
    description: str
    sources: list[dict]
    keywords: list[str] = field(default_factory=list)
    count: int = 5
    lookback_days: int = 3
    max_lookback_days: int = 14
    max_per_venue: int = 2
    priority_venues: list[str] = field(default_factory=list)
    alternate_sources: bool = False   # 날짜마다 저널/arXiv 번갈아 보내기
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"

    @property
    def bot_token(self) -> str:
        return os.environ.get(self.bot_token_env) or os.environ.get("TELEGRAM_BOT_TOKEN", "")

    @property
    def chat_id(self) -> str:
        return os.environ.get(self.chat_id_env) or os.environ.get("TELEGRAM_CHAT_ID", "")

    def matches(self, name: str) -> bool:
        name = name.strip().lower()
        return name in (self.id.lower(), self.name.lower(), self.name.replace(" ", "").lower())


def load_topics(path: Path = DEFAULT_CONFIG) -> list[Topic]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {}) or {}
    tg_defaults = defaults.get("telegram", {}) or {}
    topics = []
    for raw in data.get("topics", []) or []:
        tg = {**tg_defaults, **(raw.get("telegram") or {})}
        topics.append(
            Topic(
                id=str(raw["id"]),
                name=raw.get("name", raw["id"]),
                emoji=raw.get("emoji", "📄"),
                description=(raw.get("description") or "").strip(),
                sources=raw.get("sources") or [],
                keywords=raw.get("keywords") or [],
                count=int(raw.get("count", defaults.get("count", 5))),
                lookback_days=int(raw.get("lookback_days", defaults.get("lookback_days", 3))),
                max_lookback_days=int(raw.get("max_lookback_days", defaults.get("max_lookback_days", 14))),
                max_per_venue=int(raw.get("max_per_venue", defaults.get("max_per_venue", 2))),
                priority_venues=raw.get("priority_venues", defaults.get("priority_venues", [])) or [],
                alternate_sources=bool(raw.get("alternate_sources", defaults.get("alternate_sources", False))),
                bot_token_env=tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"),
                chat_id_env=tg.get("chat_id_env", "TELEGRAM_CHAT_ID"),
            )
        )
    if not topics:
        raise ValueError(f"{path}에 주제가 없습니다.")
    return topics


def find_topic(topics: list[Topic], name: str) -> Topic | None:
    return next((t for t in topics if t.matches(name)), None)
