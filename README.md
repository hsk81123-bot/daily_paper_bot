# 🔋 Daily Paper Bot — 배터리 데일리 논문 텔레그램 봇

매일 새벽 **5시(KST)** 에 배터리 분야 최신 논문을 골라 텔레그램으로 보냅니다.

| 주제 | 내용 | 하루 편수 |
|---|---|---|
| 🧮 배터리 모델링 | 전기화학 모델(DFN/P2D, SPM), 등가회로, 열화·수명 모델, 열 모델, SOC/SOH/RUL 추정, 파라미터 식별, physics-informed ML, 디지털 트윈, PyBaMM | 1 |
| 🔋 배터리 일반 | 리튬이온·리튬금속·전고체·나트륨이온, 양극/음극/전해질, 계면(SEI), 급속충전, 열폭주, 재활용 | 1 |

- **논문 출처**: [arXiv API](https://info.arxiv.org/help/api/) (프리프린트, API가 막히면 분야별 arXiv RSS로 대신 수집), [OpenAlex](https://openalex.org) (저널 논문 — Journal of Power Sources, JES, Joule, Nature Energy 등), Nature Energy RSS. 모두 API 키 없이 동작합니다.
- **저널/arXiv 교대**: 하루는 저널 논문, 다음 날은 arXiv 프리프린트를 보냅니다(날짜 기준으로 하루씩 교대). 그날 차례인 종류의 후보가 없으면 다른 종류로 대신합니다. 메시지 머리에 `📰 저널 논문` 또는 `📝 arXiv 프리프린트`로 표시됩니다. 끄려면 `config/topics.yaml`에서 `alternate_sources: false`로 바꿉니다.
- **선정**: `ANTHROPIC_API_KEY`가 있으면 Claude가 후보 중 주제에 맞고 중요한 논문을 고르고, 없으면 키워드·주요 저널·최신순으로 고릅니다. 초록이 있는 논문을 우선합니다. 편수를 늘리면(`count`) 한 저널이나 arXiv가 2편을 넘지 않게 섞습니다.
- **요약**: Claude가 한국어 제목, 3~4문장 한국어 요약, 핵심 정리(목적·방법·결과·의의), "왜 볼 만한가" 한 줄을 씁니다. **초록에 있는 내용만** 근거로 씁니다. 키가 없거나 API가 실패하면 한국어 요약 없이 보냅니다.
- **Abstract**: 영문 초록 원문을 접을 수 있는 인용 블록으로 붙입니다(길면 2,000자에서 자름). 출처가 초록을 주지 않으면 그렇다고 표시합니다.
- **중복 방지**: 같은 논문이 arXiv와 저널 양쪽에서 잡히면 하나로 합치고(DOI·arXiv ID·제목 유사도), 이미 보낸 논문은 다시 보내지 않습니다. 프리프린트가 나중에 저널에 실려도 제목으로 걸러냅니다. 모델링 주제에서 보낸 논문은 일반 주제에서 다시 보내지 않습니다.
- **기간**: 최근 3일 논문을 우선 쓰고(arXiv는 주말에 새 논문이 없음), 후보가 모자라면 14일까지 늘립니다.

## 메시지 예시

```
🧮 배터리 모델링 — 오늘의 논문
2026-10-15 (목)

Physics-informed learning of health-aware equivalent circuit parameter evolution …   ← 링크
리튬이온 배터리의 건강 상태를 반영한 등가회로 파라미터 변화의 물리 기반 학습
👥 Tianyi Zhang, Hang Yu, Yulong Yu 외 3명 · 📚 Journal of Energy Storage · 🗓 2026-10-10

📝 요약
리튬이온 배터리는 사용 조건에 따라 비선형적으로 열화되어 등가회로 모델 파라미터가 시간에 따라 변한다. …

📌 핵심 정리
• 목적: 열화에 따라 변하는 ECM 파라미터를 SOH와 함께 추정
• 방법: 물리 제약을 넣은 신경망으로 파라미터 변화 학습
• 결과: 여러 데이터셋에서 기존 방법 대비 추정 정확도 향상
• 의의: BMS의 모델 기반 추정을 수명 전체에서 유지할 수 있음

🔍 ECM 기반 BMS를 열화까지 고려해 갱신하려는 실무 수요에 바로 닿는 연구다.

📄 Abstract
┃ Lithium-ion batteries exhibit nonlinear degradation under complex operating
┃ conditions, causing equivalent circuit model (ECM) parameters to evolve … (탭하면 펼침)

[논문 보기] [PDF]
```

(위 내용은 형식을 보여 주는 예시입니다. 요약·핵심 정리는 `ANTHROPIC_API_KEY`가 있을 때만 나옵니다. 하루 편수는 `config/topics.yaml`의 `count`로 바꿀 수 있고, 2편 이상이면 번호와 "오늘의 한 줄 정리"가 붙습니다.)

## 봇 명령어

| 명령 | 동작 |
|---|---|
| `/today` | 오늘 보낸 논문 다시 보기 (`/today modeling`처럼 주제 지정 가능) |
| `/now <주제>` | 지금 바로 수집해서 보내기. 오늘 아직 안 보낸 논문으로 보냅니다. 예: `/now 모델링`, `/now general` |
| `/topics` | 주제 목록 |
| `/history` | 최근 7일 기록 |

명령은 1시간마다 처리됩니다(아래 [사용량](#github-actions-사용량) 참고). `TELEGRAM_CHAT_ID`(및 주제별 chat_id)에서 온 명령에만 응답합니다.

---

## 설치 가이드

### 1. 텔레그램 봇 만들기
1. 텔레그램에서 **@BotFather** → `/newbot` → 이름과 아이디를 정하면 토큰이 나옵니다. 토큰은 채팅이나 코드에 붙여 넣지 마세요.
   - 기존 Daily Jazz/뉴스 봇을 그대로 써도 됩니다. 그 경우 이 단계와 2~3단계는 건너뛰고 기존 토큰·chat_id를 씁니다.
2. 새 봇을 검색해 대화를 열고 **시작(Start)** 을 누릅니다. 이걸 안 하면 발송할 때 `chat not found` 오류가 납니다.

### 2. chat_id 확인
1. 봇에게 아무 메시지나 한 번 보냅니다.
2. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 를 엽니다.
3. `"chat":{"id":123456789,…}` 의 숫자가 chat_id입니다. 결과가 `[]`이면 봇에게 메시지를 한 번 더 보내고 새로고침하세요. @userinfobot으로 확인해도 됩니다.

> 명령 처리 워크플로가 이미 돌고 있으면 메시지가 읽음 처리되어 getUpdates에 안 보일 수 있습니다. 그래서 `TELEGRAM_CHAT_ID`가 비어 있을 때는 봇이 getUpdates를 호출하지 않습니다.

### 3. GitHub Secrets 등록
저장소 **Settings → Secrets and variables → Actions → New repository secret**:

| 이름 | 필수 | 설명 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | BotFather 토큰 |
| `TELEGRAM_CHAT_ID` | ✅ | 2단계에서 확인한 숫자 |
| `ANTHROPIC_API_KEY` | 선택 | 있으면 Claude가 고르고 한국어로 요약 |
| `OPENALEX_API_KEY` | 선택 | OpenAlex 무료 API 키(openalex.org에서 가입 후 발급). 없어도 동작하게 만들었지만, 넣으면 호출 한도가 넉넉하고 OpenAlex가 키를 요구하게 돼도 계속 동작합니다 |
| `CONTACT_EMAIL` | 선택 | OpenAlex 요청에 붙이는 연락처 |

모델을 바꾸려면 **Variables** 탭에 `CLAUDE_MODEL`을 추가합니다(기본 `claude-opus-5-5`).

### 4. 워크플로 쓰기 권한 켜기
**Settings → Actions → General → Workflow permissions → Read and write permissions** 를 선택하고 저장합니다. 발송 기록(`data/history.json`)을 커밋하는 데 필요합니다.

### 5. main에 머지
수동 실행 버튼(Run workflow)과 예약 실행은 **기본 브랜치(main)에 있는 워크플로에만** 생깁니다. 이 브랜치의 PR을 먼저 main에 머지하세요.

### 6. 첫 실행
**Actions → Daily papers → Run workflow**
- 처음에는 `dry_run`을 켜고 실행해 로그에서 어떤 논문이 뽑히는지 확인해 보세요.
- `source`로 날짜 교대와 상관없이 `journal` 또는 `arxiv`를 골라 보낼 수 있습니다(`auto`가 기본).
- 문제없으면 `dry_run` 없이 실행하면 텔레그램으로 옵니다. 같은 날 다시 받으려면 `force`를 켭니다.
- 실패하면 로그를 Claude에게 보여 주세요.

이후에는 매일 **04:50 KST**(cron `50 19 * * *` UTC)에 시작합니다. GitHub 예약 실행은 몇 분~수십 분 늦게 시작하는 경우가 많아 10분 일찍 잡아 두었습니다.

---

## 주제 추가·수정

`config/topics.yaml`만 고치면 됩니다. 예를 들어 "전고체 전지" 주제를 따로 받고 싶다면:

```yaml
  - id: solid_state
    name: 전고체 전지
    emoji: 🧱
    count: 3
    description: Solid-state batteries — sulfide/oxide/halide solid electrolytes, interfaces, processing.
    keywords: [solid-state, solid electrolyte, sulfide, garnet, argyrodite, halide]
    sources:
      - type: arxiv
        query: 'abs:"solid-state battery" OR abs:"solid electrolyte"'
      - type: openalex
        search: '"solid-state battery" OR "solid-state batteries" OR "sulfide solid electrolyte"'
```

- `sources` 종류: `arxiv`(`query` — [arXiv 검색 문법](https://info.arxiv.org/help/api/user-manual.html#query_details): `ti:` 제목, `abs:` 초록, `cat:` 분야, `AND`/`OR`), `openalex`(`search` — 따옴표 구문과 `OR`), `rss`(`url`, 선택 `require_keywords`).
- 주제별로 다른 대화방이나 봇으로 보내려면:
  ```yaml
    telegram:
      chat_id_env: TELEGRAM_CHAT_ID_MODELING   # 이 이름으로 Secret을 추가하고 워크플로 env에도 추가
      bot_token_env: TELEGRAM_BOT_TOKEN_MODELING
  ```
  지정한 환경변수가 비어 있으면 기본값(`TELEGRAM_CHAT_ID`, `TELEGRAM_BOT_TOKEN`)을 씁니다. 봇 명령은 기본 봇(`TELEGRAM_BOT_TOKEN`)으로만 받습니다.
- 저널 가중치는 `defaults.priority_venues`에서 바꿉니다.

**검색어 팁**: 첫 며칠 결과를 보고 조정하세요. 관련 없는 논문이 많으면 구문을 더 구체적으로(`"battery model"` 대신 `"electrochemical model"`), 너무 적으면 `OR`로 넓힙니다. Claude 키가 있으면 관련 없는 후보는 Claude가 걸러냅니다.

## 로컬에서 실행

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # 값 채우기
python -m paperbot.main --dry-run               # 발송·저장 없이 출력
python -m paperbot.main --topic modeling --force
python -m paperbot.main --poll                  # 봇 명령 처리
python -m pytest -q                             # 테스트 (네트워크 없이 동작)
```

### 서버 cron으로 돌리기 (GitHub Actions 대신)
서버 시간대가 KST가 아니면 `CRON_TZ`를 쓰거나 UTC로 환산하세요.

```cron
CRON_TZ=Asia/Seoul
0 5 * * *   cd /path/to/daily_paper_bot && .venv/bin/python -m paperbot.main >> bot.log 2>&1
*/10 * * * * cd /path/to/daily_paper_bot && .venv/bin/python -m paperbot.main --poll >> bot.log 2>&1
```

서버에서 돌릴 때는 GitHub의 `Daily papers`·`Bot commands` 워크플로를 꺼 두세요(Actions → 워크플로 → `…` → Disable). 둘 다 돌면 두 번 발송되거나 명령이 엇갈립니다.

## GitHub Actions 사용량

| 워크플로 | 주기 | 한 번 | 월 사용량(대략) |
|---|---|---|---|
| Daily papers | 하루 1번 | 1~2분 | ~60분 |
| Bot commands | 1시간마다 | ~1분 | ~720분 |
| Tests | push할 때 | ~1분 | 소량 |

- **공개 저장소**는 무료이고 제한이 없습니다.
- **비공개 저장소**는 무료 플랜 월 2,000분입니다. 명령 처리를 자주 할수록 많이 씁니다. 30분 간격(`*/30`)으로 바꾸면 약 1,440분이 됩니다. 명령을 안 쓰면 `Bot commands` 워크플로를 꺼도 됩니다.

## Claude API 비용 메모

- Claude 구독(Pro/Max)과 API는 결제가 따로입니다. [console.anthropic.com](https://console.anthropic.com)에서 키를 만들고 크레딧을 충전하세요.
- 주제마다 하루 1번 호출합니다(후보 최대 30편의 초록을 한 번에 보내 고르고 요약). 기본 모델 `claude-opus-5-5` 기준 주제당 하루 약 0.1달러 안팎, 두 주제면 한 달 몇 달러 수준입니다(초록 길이와 모델에 따라 달라짐).
- 비용을 줄이려면 Variables에 `CLAUDE_MODEL=claude-haiku-5-5`를 넣으세요.
- 키가 없어도 제목·저자·저널·링크로 발송은 됩니다.

## 구조

```
paperbot/
  main.py        CLI, 하루 실행 흐름, 봇 명령 처리
  sources.py     arXiv / OpenAlex / RSS 수집
  papers.py      논문 레코드, DOI·arXiv ID 정규화, 중복 합치기
  ranking.py     기간 필터, 키 없을 때 선정 규칙(키워드·저널·최신·출처 다양성)
  summarize.py   Claude 선별·한국어 요약 (구조화 출력)
  telegram.py    HTML 메시지 구성(길면 나눠 보냄), 발송, getUpdates
  history.py     data/history.json (보낸 논문, 날짜별 기록, 60일 지나면 정리)
config/topics.yaml
data/history.json
.github/workflows/  daily.yml · commands.yml · tests.yml
tests/           pytest (네트워크 호출은 모두 모킹)
```
