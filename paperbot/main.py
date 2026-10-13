"""데일리 페이퍼 봇 진입점.

    python -m paperbot.main                 # 모든 주제 수집·발송
    python -m paperbot.main --dry-run       # 발송·저장 없이 출력만
    python -m paperbot.main --topic modeling --force
    python -m paperbot.main --poll          # 텔레그램 명령 처리
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from . import ranking, sources, summarize
from .config import DEFAULT_CONFIG, DEFAULT_HISTORY, Topic, find_topic, load_dotenv, load_topics, now_kst
from .history import History
from .papers import Paper, dedupe
from .telegram import Telegram, TelegramError, build_messages, esc

log = logging.getLogger("paperbot")


# ------------------------------------------------------------------ 수집·선정
def pick_papers(topic: Topic, history: History, now: datetime, session=None, client=None) -> tuple[list[Paper], str, str]:
    """(선정 논문, 한 줄 정리, 안내 문구)를 돌려준다."""
    log.info("[%s] 수집 시작", topic.name)
    raw = sources.collect(topic.sources, topic.max_lookback_days, now, session=session)
    fresh = [p for p in dedupe(raw) if not history.was_sent(p)]
    pool = ranking.candidates_for(topic, fresh, now)
    log.info("[%s] 수집 %d편 → 중복·기발송 제외 %d편 → 기간 내 후보 %d편", topic.name, len(raw), len(fresh), len(pool))
    if not pool:
        return [], "", ""

    ranked = ranking.rank(pool, topic, now)
    curated = summarize.curate(topic, ranked[: summarize.MAX_CANDIDATES], client=client)
    if curated is not None:
        papers, digest = curated
        return papers, digest, ""
    note = "" if not os.environ.get("ANTHROPIC_API_KEY") else "요약 생성에 실패해 제목·링크 위주로 보냅니다."
    return ranking.basic_select(pool, topic, now), "", note


def run_topic(topic: Topic, history: History, now: datetime, *, dry_run: bool = False, force: bool = False,
              chat_id: str | None = None, session=None, client=None, telegram: Telegram | None = None) -> bool:
    day = now.date().isoformat()
    if history.sent_today(day, topic.id) and not force:
        log.info("[%s] 오늘(%s) 이미 보냈습니다. 다시 보내려면 --force", topic.name, day)
        return True

    papers, digest, note = pick_papers(topic, history, now, session=session, client=client)
    messages = build_messages(topic, papers, digest, day, note=note)

    if dry_run:
        for text, buttons in messages:
            print(text)
            if buttons:
                print("[버튼]", ", ".join(b["text"] for row in buttons["inline_keyboard"] for b in row))
            print("-" * 60)
        history.record(day, topic.id, papers, digest)  # 같은 실행 안에서 주제 간 중복 방지(저장은 안 함)
        return True

    target = chat_id or topic.chat_id
    if not target:
        log.error("[%s] chat_id가 없습니다(%s). 발송을 건너뜁니다.", topic.name, topic.chat_id_env)
        return False
    try:
        tg = telegram or Telegram(topic.bot_token)
        tg.send_all(target, messages)
    except TelegramError as exc:
        log.error("[%s] 발송 실패: %s", topic.name, exc)
        return False
    history.record(day, topic.id, papers, digest)
    log.info("[%s] %d편 발송 완료", topic.name, len(papers))
    return True


def run_daily(topics: list[Topic], history: History, *, dry_run: bool, force: bool) -> bool:
    now = now_kst()
    session = sources.make_session()
    ok = True
    for topic in topics:
        try:
            ok &= run_topic(topic, history, now, dry_run=dry_run, force=force, session=session)
        except Exception:  # noqa: BLE001 — 한 주제가 실패해도 다음 주제는 계속
            log.exception("[%s] 처리 중 오류", topic.name)
            ok = False
    if not dry_run:
        history.prune(now.date())
        history.save()
    return ok


# ------------------------------------------------------------------ 봇 명령
HELP = (
    "📚 <b>데일리 페이퍼 봇</b>\n"
    "/today — 오늘 보낸 논문 다시 보기\n"
    "/now &lt;주제&gt; — 지금 바로 수집해서 보내기 (예: /now 모델링)\n"
    "/topics — 주제 목록\n"
    "/history — 최근 7일 기록"
)


def _render_saved(topic: Topic, entry: dict, day: str):
    papers = [Paper.from_dict(d) for d in entry.get("papers", [])]
    return build_messages(topic, papers, entry.get("digest", ""), day, note="(다시 보기)")


def handle_command(text: str, chat_id: str, topics: list[Topic], history: History, tg: Telegram) -> None:
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    now = now_kst()
    day = now.date().isoformat()

    if cmd in ("/start", "/help"):
        tg.send(chat_id, HELP)
    elif cmd == "/topics":
        lines = ["📚 <b>주제 목록</b>"]
        for t in topics:
            lines.append(f"{t.emoji} {esc(t.name)} (<code>{esc(t.id)}</code>) — 하루 {t.count}편")
        tg.send(chat_id, "\n".join(lines))
    elif cmd == "/today":
        chosen = [find_topic(topics, arg)] if arg else topics
        sent_any = False
        for t in filter(None, chosen):
            entry = history.day_entry(day, t.id)
            if entry:
                tg.send_all(chat_id, _render_saved(t, entry, day))
                sent_any = True
        if not sent_any:
            tg.send(chat_id, "오늘은 아직 보낸 논문이 없습니다. /now &lt;주제&gt; 로 바로 받아볼 수 있어요.")
    elif cmd == "/now":
        topic = find_topic(topics, arg) if arg else None
        if topic is None and arg:
            # "모델링"처럼 이름 일부만 써도 찾기
            topic = next((t for t in topics if arg.lower() in t.name.lower() or arg.lower() in t.id.lower()), None)
        if topic is None:
            names = ", ".join(f"{t.name}({t.id})" for t in topics)
            tg.send(chat_id, f"주제를 지정해 주세요: /now &lt;주제&gt;\n가능한 주제: {esc(names)}")
            return
        tg.send(chat_id, f"{topic.emoji} {esc(topic.name)} 논문을 찾는 중입니다…")
        run_topic(topic, history, now, force=True, chat_id=chat_id, telegram=tg)
    elif cmd == "/history":
        days = history.recent_days(7)
        if not days:
            tg.send(chat_id, "아직 기록이 없습니다.")
            return
        lines = ["🗂 <b>최근 7일 기록</b>"]
        names = {t.id: f"{t.emoji} {t.name}" for t in topics}
        for d in days:
            lines.append(f"\n<b>{d}</b>")
            for tid, entry in history.days[d].items():
                lines.append(f"{esc(names.get(tid, tid))}")
                for p in entry.get("papers", []):
                    title = p.get("title_ko") or p.get("title", "")
                    lines.append(f'• <a href="{esc(p.get("url", ""))}">{esc(title[:90])}</a>')
        text = "\n".join(lines)
        tg.send(chat_id, text[:3900])
    else:
        tg.send(chat_id, "알 수 없는 명령입니다.\n\n" + HELP)


def poll_commands(topics: list[Topic], history: History) -> None:
    default_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not default_chat:
        # getUpdates를 부르면 메시지가 읽음 처리되어 사용자가 브라우저로 chat_id를 확인할 수 없게 된다
        log.warning("TELEGRAM_CHAT_ID가 비어 있어 명령 처리를 건너뜁니다(getUpdates 호출 안 함).")
        return
    allowed = {default_chat} | {t.chat_id for t in topics if t.chat_id}
    tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    updates = tg.get_updates(history.telegram_offset)
    log.info("새 업데이트 %d건", len(updates))
    for upd in updates:
        history.telegram_offset = max(history.telegram_offset, upd["update_id"] + 1)
        msg = upd.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text") or ""
        if not text.startswith("/"):
            continue
        if chat_id not in allowed:
            log.warning("허용되지 않은 chat_id(%s)의 명령을 무시합니다.", chat_id)
            continue
        log.info("명령 처리: %s", text.split()[0])
        try:
            handle_command(text, chat_id, topics, history, tg)
        except Exception:  # noqa: BLE001
            log.exception("명령 처리 실패: %s", text)
    history.prune(now_kst().date())
    history.save()


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="배터리 데일리 페이퍼 텔레그램 봇")
    parser.add_argument("--dry-run", action="store_true", help="발송·저장 없이 메시지를 출력")
    parser.add_argument("--topic", help="이 주제만 실행 (id 또는 이름)")
    parser.add_argument("--force", action="store_true", help="오늘 이미 보냈어도 다시 수집·발송")
    parser.add_argument("--poll", action="store_true", help="텔레그램 봇 명령 처리")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--history", default=str(DEFAULT_HISTORY))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    topics = load_topics(Path(args.config))
    history = History(Path(args.history))

    if args.poll:
        poll_commands(topics, history)
        return 0

    if args.topic:
        topic = find_topic(topics, args.topic)
        if not topic:
            log.error("주제를 찾을 수 없습니다: %s (가능: %s)", args.topic, ", ".join(t.id for t in topics))
            return 2
        topics = [topic]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY가 없어 Claude 요약 없이 제목·링크 위주로 보냅니다.")
    ok = run_daily(topics, history, dry_run=args.dry_run, force=args.force)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
