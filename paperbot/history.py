"""발송 기록(data/history.json)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .papers import Paper

RETENTION_DAYS = 60


class History:
    def __init__(self, path: Path):
        self.path = Path(path)
        data = {}
        if self.path.exists() and self.path.read_text(encoding="utf-8").strip():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        self.sent: dict[str, dict] = data.get("sent", {})          # 키 -> {date, topic}
        self.days: dict[str, dict] = data.get("days", {})          # 날짜 -> 주제 -> {digest, papers}
        self.telegram_offset: int = int(data.get("telegram_offset", 0))

    # ------------------------------------------------------------ 조회
    def was_sent(self, paper: Paper) -> bool:
        return any(k in self.sent for k in paper.keys)

    def sent_today(self, day: str, topic_id: str) -> bool:
        return topic_id in self.days.get(day, {})

    def day_entry(self, day: str, topic_id: str) -> dict | None:
        return self.days.get(day, {}).get(topic_id)

    def recent_days(self, n: int = 7) -> list[str]:
        return sorted(self.days, reverse=True)[:n]

    # ------------------------------------------------------------ 기록
    def record(self, day: str, topic_id: str, papers: list[Paper], digest: str) -> None:
        for p in papers:
            for k in p.keys:
                self.sent[k] = {"date": day, "topic": topic_id}
        entry = self.days.setdefault(day, {}).setdefault(topic_id, {"digest": "", "papers": []})
        entry["papers"].extend(p.to_dict() for p in papers)  # 같은 날 /now로 더 받으면 이어 붙임
        if digest:
            entry["digest"] = digest

    def prune(self, today: date, keep_days: int = RETENTION_DAYS) -> None:
        cutoff = (today - timedelta(days=keep_days)).isoformat()
        self.sent = {k: v for k, v in self.sent.items() if v.get("date", "") >= cutoff}
        self.days = {d: v for d, v in self.days.items() if d >= cutoff}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sent": dict(sorted(self.sent.items())),
            "days": dict(sorted(self.days.items())),
            "telegram_offset": self.telegram_offset,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
