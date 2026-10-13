"""텔레그램 메시지 구성, 발송, 명령 수신."""
from __future__ import annotations

import html
import logging

import requests

from .config import KST, Topic
from .papers import Paper

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 3900  # 텔레그램 한도 4096자보다 여유 있게
WEEKDAYS = "월화수목금토일"


class TelegramError(RuntimeError):
    pass


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _date_label(day: str) -> str:
    from datetime import date

    d = date.fromisoformat(day)
    return f"{d.isoformat()} ({WEEKDAYS[d.weekday()]})"


def paper_block(i: int, p: Paper) -> str:
    lines = [f'<b>{i}. <a href="{html.escape(p.url)}">{esc(p.title)}</a></b>']
    if p.title_ko:
        lines.append(f"<i>{esc(p.title_ko)}</i>")
    meta = []
    if p.author_line():
        meta.append(f"👥 {esc(p.author_line())}")
    meta.append(f"📚 {esc(p.venue or p.source)}")
    meta.append(f"🗓 {p.published.astimezone(KST).date().isoformat()}")
    lines.append(" · ".join(meta))
    if p.summary:
        lines.append(f"📝 {esc(p.summary)}")
    if p.why:
        lines.append(f"🔍 {esc(p.why)}")
    return "\n".join(lines)


def _buttons(items: list[tuple[int, Paper]]) -> dict | None:
    rows = []
    for i, p in items:
        row = [{"text": f"{i}. 논문 보기", "url": p.url}]
        if p.pdf_url and p.pdf_url != p.url:
            row.append({"text": f"{i}. PDF", "url": p.pdf_url})
        rows.append(row)
    return {"inline_keyboard": rows} if rows else None


def build_messages(topic: Topic, papers: list[Paper], digest: str, day: str,
                   note: str = "") -> list[tuple[str, dict | None]]:
    """주제 하나를 (본문, 버튼) 메시지 목록으로 만든다. 길면 여러 메시지로 나눈다."""
    header = [f"{topic.emoji} <b>{esc(topic.name)} — 오늘의 논문</b>", f"{_date_label(day)} · {len(papers)}편"]
    if note:
        header.append(f"<i>{esc(note)}</i>")
    if digest:
        header += ["", f"💡 <b>오늘의 한 줄 정리</b>\n{esc(digest)}"]
    if not papers:
        header += ["", "오늘은 새로 소개할 논문이 없습니다. 내일 다시 찾아볼게요."]
        return [("\n".join(header), None)]

    messages: list[tuple[str, dict | None]] = []
    current = "\n".join(header)
    items: list[tuple[int, Paper]] = []
    for i, p in enumerate(papers, 1):
        block = paper_block(i, p)
        if items and len(current) + len(block) + 2 > MAX_LEN:
            messages.append((current, _buttons(items)))
            current, items = "", []
        current = f"{current}\n\n{block}" if current else block
        items.append((i, p))
    messages.append((current, _buttons(items)))
    return messages


class Telegram:
    def __init__(self, token: str, session: requests.Session | None = None):
        if not token:
            raise TelegramError("텔레그램 봇 토큰이 없습니다(TELEGRAM_BOT_TOKEN).")
        self.token = token
        self.session = session or requests.Session()

    def call(self, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
        url = API.format(token=self.token, method=method)
        last_exc: Exception | None = None
        for _ in range(3):
            try:
                resp = self.session.post(url, json=payload or {}, timeout=timeout)
                data = resp.json()
                break
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
        else:
            raise TelegramError(f"텔레그램 {method} 호출 실패: {last_exc}")
        if not data.get("ok"):
            desc = data.get("description", "")
            if "chat not found" in desc.lower():
                log.error(
                    "텔레그램 'chat not found': 봇에게 시작(Start)을 눌렀는지, "
                    "TELEGRAM_CHAT_ID가 맞는지 확인하세요."
                )
            raise TelegramError(f"텔레그램 {method} 오류: {desc}")
        return data.get("result", {})

    def send(self, chat_id: str, text: str, buttons: dict | None = None) -> None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if buttons:
            payload["reply_markup"] = buttons
        self.call("sendMessage", payload)

    def send_all(self, chat_id: str, messages: list[tuple[str, dict | None]]) -> None:
        for text, buttons in messages:
            self.send(chat_id, text, buttons)

    def get_updates(self, offset: int) -> list[dict]:
        return self.call("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["message"]})
