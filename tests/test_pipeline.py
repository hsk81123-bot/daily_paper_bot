import json
from types import SimpleNamespace

from conftest import NOW
from paperbot import main, ranking, sources, summarize
from paperbot.config import DEFAULT_CONFIG, load_topics
from paperbot.history import History
from paperbot.telegram import build_messages


# ------------------------------------------------------------ 설정
def test_real_config_loads():
    topics = load_topics(DEFAULT_CONFIG)
    assert [t.id for t in topics] == ["modeling", "general"]
    assert all(t.sources for t in topics)
    assert topics[0].chat_id_env == "TELEGRAM_CHAT_ID"
    assert "Journal of Power Sources" in topics[0].priority_venues


# ------------------------------------------------------------ 선정
def test_candidates_expand_when_few(topic, paper_factory):
    papers = [paper_factory("old model a", days_ago=10), paper_factory("new model b", days_ago=1)]
    assert len(ranking.candidates_for(topic, papers, NOW)) == 2
    topic.count = 1
    assert len(ranking.candidates_for(topic, papers, NOW)) == 1


def test_basic_select_prefers_keywords_and_mixes_venues(topic, paper_factory):
    papers = [paper_factory(f"arxiv model {i}", venue="arXiv") for i in range(5)]
    papers.append(paper_factory("unrelated cathode synthesis", venue="Joule"))
    papers.append(paper_factory("state of health model", venue="Journal of Power Sources"))
    picked = ranking.basic_select(papers, topic, NOW)
    assert picked[0].venue == "Journal of Power Sources"
    assert sum(p.venue == "arXiv" for p in picked) == 2


# ------------------------------------------------------------ Claude
class FakeClient:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def test_curate_uses_indices_and_skips_bad_ones(topic, paper_factory):
    cands = [paper_factory(f"paper {i}") for i in range(4)]
    client = FakeClient({"selected": [
        {"index": 2, "title_ko": "논문 2", "summary": "요약", "why": "이유"},
        {"index": 99, "title_ko": "x", "summary": "x", "why": "x"},
        {"index": 2, "title_ko": "dup", "summary": "x", "why": "x"},
        {"index": 0, "title_ko": "논문 0", "summary": "요약0", "why": "이유0"},
    ], "digest": "오늘의 흐름"})
    papers, digest = summarize.curate(topic, cands, client=client)
    assert [p.title for p in papers] == ["paper 2", "paper 0"]
    assert papers[0].title_ko == "논문 2" and digest == "오늘의 흐름"
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"


def test_curate_model_without_fallback(monkeypatch, topic, paper_factory):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    client = FakeClient({"selected": [], "digest": ""})
    summarize.curate(topic, [paper_factory("p")], client=client)
    assert "fallbacks" not in client.calls[0]


def test_curate_failure_returns_none(topic, paper_factory):
    assert summarize.curate(topic, [paper_factory("p")], client=FakeClient(error=RuntimeError("500"))) is None


def test_curate_without_key_returns_none(topic, paper_factory):
    assert summarize.curate(topic, [paper_factory("p")]) is None


# ------------------------------------------------------------ 메시지
def test_build_messages_escapes_and_buttons(topic, paper_factory):
    p = paper_factory("Li <3 & model", authors=["A", "B", "C", "D"], pdf_url="https://x/pdf",
                      summary="요약 <b>", title_ko="한국어 제목")
    [(text, buttons)] = build_messages(topic, [p], "정리", NOW.date().isoformat())
    assert "Li &lt;3 &amp; model" in text and "요약 &lt;b&gt;" in text
    assert "A, B, C 외 1명" in text and "2026-10-14 (수)" in text
    assert [b["text"] for b in buttons["inline_keyboard"][0]] == ["1. 논문 보기", "1. PDF"]


def test_build_messages_splits_long(topic, paper_factory):
    papers = [paper_factory(f"title {i}", summary="가" * 1500) for i in range(5)]
    msgs = build_messages(topic, papers, "", "2026-10-14")
    assert len(msgs) > 1
    assert all(len(t) <= 4096 for t, _ in msgs)
    assert sum(len(b["inline_keyboard"]) for _, b in msgs) == 5


def test_build_messages_empty(topic):
    [(text, buttons)] = build_messages(topic, [], "", "2026-10-14")
    assert "새로 소개할 논문이 없습니다" in text and buttons is None


# ------------------------------------------------------------ 기록
def test_history_roundtrip_and_prune(tmp_path, paper_factory):
    h = History(tmp_path / "h.json")
    p = paper_factory("A model", arxiv_id="2610.1", doi="10.1/a")
    h.record("2026-08-01", "modeling", [p], "")
    h.record("2026-10-14", "modeling", [paper_factory("B")], "d")
    h.save()
    h2 = History(tmp_path / "h.json")
    assert h2.was_sent(paper_factory("a model!"))  # 제목만 같아도 이미 보낸 것으로 봄
    assert h2.sent_today("2026-10-14", "modeling")
    h2.prune(NOW.date())
    assert "2026-08-01" not in h2.days and not h2.was_sent(p)


# ------------------------------------------------------------ 실행 흐름
class FakeTelegram:
    def __init__(self, fail=False):
        self.sent, self.fail, self.updates_called = [], fail, False

    def send(self, chat_id, text, buttons=None):
        from paperbot.telegram import TelegramError

        if self.fail:
            raise TelegramError("chat not found")
        self.sent.append((chat_id, text))

    def send_all(self, chat_id, messages):
        for text, buttons in messages:
            self.send(chat_id, text, buttons)

    def get_updates(self, offset):
        self.updates_called = True
        return self.updates


def _patch_collect(monkeypatch, papers):
    monkeypatch.setattr(sources, "collect", lambda *a, **k: list(papers))


def test_run_topic_sends_and_records(monkeypatch, tmp_path, topic, paper_factory):
    papers = [paper_factory(f"model {i}") for i in range(5)]
    _patch_collect(monkeypatch, papers)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    h = History(tmp_path / "h.json")
    tg = FakeTelegram()
    assert main.run_topic(topic, h, NOW, telegram=tg)
    assert tg.sent and tg.sent[0][0] == "42"
    assert len(h.day_entry("2026-10-14", "modeling")["papers"]) == 3
    # 같은 날 다시 실행하면 건너뜀
    tg.sent.clear()
    assert main.run_topic(topic, h, NOW, telegram=tg) and not tg.sent
    # --force면 아직 안 보낸 논문으로 다시 보냄
    assert main.run_topic(topic, h, NOW, telegram=tg, force=True)
    assert len(h.day_entry("2026-10-14", "modeling")["papers"]) == 5


def test_run_topic_send_failure_not_recorded(monkeypatch, tmp_path, topic, paper_factory):
    _patch_collect(monkeypatch, [paper_factory("model")])
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    h = History(tmp_path / "h.json")
    assert not main.run_topic(topic, h, NOW, telegram=FakeTelegram(fail=True))
    assert not h.sent_today("2026-10-14", "modeling")


def test_dry_run_prints_without_saving(monkeypatch, tmp_path, capsys, paper_factory):
    _patch_collect(monkeypatch, [paper_factory("battery model x")])
    hist = tmp_path / "h.json"
    assert main.main(["--dry-run", "--history", str(hist)]) == 0
    out = capsys.readouterr().out
    assert "배터리 모델링" in out and "battery model x" in out
    # 두 번째 주제에서는 같은 논문을 다시 고르지 않음
    assert out.count("battery model x") == 1
    assert not hist.exists()


def test_poll_without_chat_id_does_not_call_get_updates(monkeypatch, tmp_path, topic):
    called = []
    monkeypatch.setattr(main, "Telegram", lambda token: called.append(token))
    main.poll_commands([topic], History(tmp_path / "h.json"))
    assert not called


def test_poll_ignores_unknown_chat_and_handles_allowed(monkeypatch, tmp_path, topic):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    tg = FakeTelegram()
    tg.updates = [
        {"update_id": 10, "message": {"chat": {"id": 999}, "text": "/now modeling"}},
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/topics@paper_bot"}},
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "hello"}},
    ]
    monkeypatch.setattr(main, "Telegram", lambda token: tg)
    h = History(tmp_path / "h.json")
    main.poll_commands([topic], h)
    assert [c for c, _ in tg.sent] == ["42"]
    assert "주제 목록" in tg.sent[0][1]
    assert History(tmp_path / "h.json").telegram_offset == 13


def test_now_command_partial_name(monkeypatch, tmp_path, topic, paper_factory):
    _patch_collect(monkeypatch, [paper_factory("model z")])
    tg = FakeTelegram()
    h = History(tmp_path / "h.json")
    main.handle_command("/now 모델링", "42", [topic], h, tg)
    assert any("model z" in t for _, t in tg.sent)
    main.handle_command("/today", "42", [topic], h, tg)
    assert "(다시 보기)" in tg.sent[-1][1]
