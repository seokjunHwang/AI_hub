# meeting_minutes

회의 녹음 → 회의록 → 구글 드라이브. 로컬 STT + Claude 구조화 추출.

```
회의 녹음 ──▶ faster-whisper ──▶ 녹취록 ──▶ Claude ──▶ 구조 데이터 ──┬─▶ .md   (Notion/Slack)
            (로컬, 외부전송 X)   (타임스탬프)  (structured   (Minutes)  ├─▶ .html (뷰어)
                                                output)                └─▶ .json (후속 자동화 입력)
                                                                            │
                                                                     Google Drive 업로드
```

## 사용법 — 셋 중 하나

| | 방법 | 언제 |
|---|---|---|
| **A** | [`회의록_GUI.bat`](회의록_GUI.bat) **더블클릭** | 평소. 가장 쉽다 |
| **B** | `jupyter lab` → [`run.ipynb`](run.ipynb) | 중간 결과를 셀 단위로 들여다볼 때 |
| **C** | `python -m src.pipeline` | 스케줄러·배치 |

### A. GUI (권장)

1. **`회의록_GUI.bat` 더블클릭**
2. **입력** — `녹취록(.txt)` 또는 `오디오`(STT 부터) 를 고르고 «찾기…» 로 파일 선택
   (`.env` 에 적어 둔 값이 이미 채워져 있다)
3. **모드** — `사람검수` 또는 `오토` 중 하나
4. **`▶ 실행`**
5. 검수 모드면 추출이 끝나고 **멈춘다** → 아래 체크박스에서 뺄 항목을 해제 → **`✔ 확정 후 업로드`**

처음 한 번은 **`연결 확인`** 버튼을 먼저 눌러 보는 게 좋다. 입력·드라이브 토큰·노션
페이지를 실제로 찔러 보고 `O / X` 로 알려준다. 여기서 `X` 면 실행해도 같은 데서 막힌다.

### 무엇이 어디로 가는가

```
                     ┌─▶ data/minutes/<날짜>/<제목>/  (로컬 . 항상)
녹취록 ──▶ 추출 ──▶ 확정 ─┼─▶ 구글 드라이브 «내 드라이브 / 회의록 / <제목> (업로드시각)»
                     └─▶ 노션 «회의록 자동업로드» 아래 새 페이지
```

드라이브·노션은 GUI 체크박스로 끌 수 있다. 끄면 로컬에만 저장된다.

### 자주 바꾸는 값은 `.env` 한 곳에 있다

| 하고 싶은 것 | `.env` 에서 고칠 것 |
|---|---|
| 추출 품질을 올리고 싶다 | `CLI_EFFORT=max` (느림) / `high` (빠름) |
| STT 가 너무 느리다 | `WHISPER_MODEL=small` |
| 드라이브 폴더 이름 | `DRIVE_FOLDER_NAME` |
| 노션 올릴 위치 | `NOTION_TARGET` (페이지 URL) |
| 업로드를 아예 끄고 싶다 | `SEND=` · `NOTION_MODE=` (비우기) |
| GUI 가 쓸 파이썬 | `PYTHON=` |

`.env` 를 고친 뒤 GUI 는 **`.env` 다시 읽기**, 노트북은 **1번 셀 재실행**이면 반영된다.

## 설계 포인트

**1. 요약이 아니라 구조 추출이다.** 출력은 산문이 아니라 [`src/schema.py`](src/schema.py)의 `Minutes` 스키마다. `decisions` / `action_items` / `open_questions` / `unclear_notes`로 쪼개진 데이터라서 다음 자동화(GitHub Issue 생성, 칸반)의 입력이 된다.

**2. 모든 결정·액션에 근거 인용(`quote`)이 붙는다.** 녹취록에 없는 문장은 인용할 수 없으니, 인용을 강제하면 없는 사실을 만들기 어려워진다. 검증도 가능해진다.

**3. 담당자·마감은 추측하지 않고, 빈칸의 «이유»를 구분한다.**

| 상태 | 뜻 | 다음 행동 |
|---|---|---|
| `stated` | 회의에서 명시됨 | — |
| `not_stated` | 녹취를 다 봤고 **회의에서 정하지 않음** (미확인) | 참석자에게 묻는다 |
| `unclear` | 녹취가 깨져 **확인 불가** (미조사) | 원본 오디오를 다시 듣는다 |

둘을 «미정» 하나로 뭉개면 받는 쪽이 이미 있는 답을 다시 찾는다. 그리고 빈칸은 모델의 실패가 아니라 **회의의 실패**다.

**3-1. 한 번 멈춘다.** 추출 결과를 바로 문서로 만들지 않고 `D1/A1/Q1` 번호로 제시하고 멈춘다. **회의에서 나온 말이 전부 결정은 아니고**, 어느 것이 결정인지는 참석한 사람만 안다.

**3-2. 조용히 덮어쓰지 않는다.** 같은 제목·날짜로 다시 돌리면 `_v2`, `_v3` 로 저장한다 (`.env` 의 `OUTPUT_OVERWRITE=true` 로 강제 가능).

**4. STT 오인식을 숨기지 않는다.** 문맥상 이상한 구간은 `unclear_notes`에 남긴다. 숫자·날짜·금액·고유명사가 여기 걸리면 반드시 확인해야 한다.

**5. STT는 로컬.** 회의 녹음에 계약·인사·환자 관련 언급이 섞일 수 있으므로 오디오를 외부로 보내지 않는다. 텍스트만 Claude로 간다.

## 추출을 누가 하는가 — 두 경로

`②추출` 만 갈린다. STT·렌더·업로드는 같다.

| | 누가 추출하나 | 어떻게 켜나 | 특징 |
|---|---|---|---|
| **경로 1** (기본) | `claude -p` (Claude Code 구독) | `.env` 의 `EXTRACT_MODE=cli` | **API 키 불필요.** 인용 검증·자동 재시도 있음 |
| 경로 2 | Anthropic API | `EXTRACT_MODE=api` + `ANTHROPIC_API_KEY` | 긴 회의 자동 분할·병합 |

두 경로 모두 **명령은 같다.** `EXTRACT_MODE` 만 바꾸면 된다.

```powershell
python -m src.pipeline            # 검수 모드
python -m src.pipeline --auto     # 오토 모드
```

경로 1 의 추출 규칙은 [`prompts/extract_system.md`](prompts/extract_system.md) 가 정본이다.
사람이 대신 추출할 때(채팅창에서 시킬 때) 지켜야 하는 절차는
[`.claude/skills/meeting-minutes/SKILL.md`](.claude/skills/meeting-minutes/SKILL.md) 에 있다.

스키마는 추측하지 말고 뽑아 쓴다.

```powershell
python -m src.pipeline --print-schema
```

## 설치

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env      # 여기가 모든 설정의 정본. 경로 2 를 쓸 때만 ANTHROPIC_API_KEY
```

- **ffmpeg 필요** (오디오 압축 해제): `winget install Gyan.FFmpeg`
- 로컬 Python 3.14는 CTranslate2 휠이 없어 **3.12 권장**

### GPU — 이 PC(AMD Radeon RX 7600)는 CPU 로만 돈다

faster-whisper 는 CTranslate2 기반이고 **CTranslate2 의 GPU 지원은 CUDA(NVIDIA) 전용**이다.
AMD·Intel GPU 는 몇 장이 있어도 쓰지 못한다 (ROCm·DirectML 미지원).

실행하면 판정 근거가 그대로 찍힌다.

```
[stt] 장치   cpu (int8)  <- CUDA 장치 없음 (CTranslate2 는 NVIDIA CUDA 전용 — AMD/Intel GPU 는 사용 불가)
[stt] CPU 로 돕니다. 모델이 클수록 오래 걸립니다 (.env 의 WHISPER_MODEL 조정)
[stt] [########............]  42.3%  25:23/1:00:00  0.51x  남은 33:29  세그먼트 412
```

`0.51x` 는 **실시간보다 2배 느리다**는 뜻이다 (1시간 오디오 = 약 2시간). 이 값을 보고 모델을 조정한다.

| 대응 | 방법 |
|---|---|
| 모델 낮추기 | `.env` 의 `WHISPER_MODEL=medium` 또는 `small` |
| AMD GPU 를 쓰고 싶다면 | **whisper.cpp (Vulkan 백엔드)** 또는 **Const-me/Whisper**(DirectCompute). 별도 도구이므로 결과 텍스트를 `--transcript` 로 넣는다 |
| 클라우드 STT | Clova Note 등. 단 오디오가 외부로 나간다 (기밀 정책 확인 필요) |

## GUI 가 어떻게 동작하나

| 모드 | 언제 | 무엇을 하는가 |
|---|---|---|
| **사람검수 모드** (기본) | 평소 | 추출 후 **멈춘다.** 결정·액션·미결이 체크박스로 뜨고, 고른 것만 확정·업로드 |
| **오토 모드** | 새벽 배치·급할 때 | 멈추지 않고 전부 반영 → 확정 → 드라이브·노션까지 한 번에 |

- **로직이 GUI 에 없다.** [`src/pipeline.py`](src/pipeline.py) 의 단계 함수를 그대로 부른다.
  GUI 와 CLI 가 다르게 동작하면 «어느 쪽이 맞나» 를 알 수 없어서다 (테스트가 이걸 검사한다)
- 창에서 고른 값은 **이번 실행만** 적용된다. `.env` 파일은 건드리지 않는다
- 작업은 **별 스레드**에서 돈다. STT 가 몇 분 걸려도 창이 얼지 않고 로그가 흐른다
- **어떤 오류도 창을 닫지 않는다.** 원인을 로그에 적고 버튼이 되살아난다
- 파이썬은 `.env` 의 `PYTHON=` → 폴더의 `.venv` → PATH 순으로 찾는다

## 노트북 (셀 단위로 들여다볼 때)

```powershell
jupyter lab       # meeting_minutes 폴더에서 실행
```
→ [`run.ipynb`](run.ipynb) 을 열고 1번 셀의 경로·제목만 고친 뒤 위에서 아래로 실행.

CLI 보다 노트북을 권하는 이유는 **4번 멈춤 게이트** 때문입니다. 검토 목록이 바로 위 셀에 있고, 다음 셀의 `ACCEPT = "D1,A1"` 만 고쳐 재실행하면 됩니다. CLI 는 draft 경로를 복사해 명령을 다시 쳐야 합니다.

> ⚠ **커밋 전 `Kernel → Restart & Clear All Outputs`.** 노트북 출력에 실제 회의 내용이 남습니다.
> (`data/` 는 이미 gitignore 돼 있지만 노트북 출력은 파일 안에 박힙니다.)

## CLI — 스케줄러·자동화용

`.env` 가 정본이고, 인자는 **이번 실행만** 덮어씁니다.

```powershell
# 검수 모드 (기본) — 추출하고 검토 목록만 출력하고 멈춘다
python -m src.pipeline

# 오토 모드 — 전부 반영 + 드라이브·노션 업로드까지. 새벽 스케줄러용
python -m src.pipeline --auto

# 멈춘 뒤, 고른 것만 확정
python -m src.pipeline --draft "data/minutes/draft/....json" --accept D1,D2,A1

# 입력·제목만 바꿔서
python -m src.pipeline --transcript data/transcripts/kickoff.txt --title 킥오프 --date 2026-08-25

# 연결 상태만 확인 (입력·드라이브 토큰·노션 페이지)
python -m src.pipeline --check

# STT 만
python -m src.pipeline --audio data/audio/kickoff.m4a --stt-only
```

GUI 와 CLI 는 **같은 함수**(`src/pipeline.py` 의 `resolve_input` · `do_extract` ·
`do_confirm` · `do_send` · `do_notion`)를 부릅니다. 두 쪽이 다르게 동작하면
«어느 쪽이 맞나» 를 알 수 없어서입니다.

검수 모드에서 멈출 때 나오는 목록 (GUI 는 이걸 체크박스로 그립니다):

```
[결정사항]
  D1. 1차 범위 3개 인텐트 확정
      근거: "그 세 개만 먼저 가죠" ·00:12:40
  D2. 주차 안내는 2차로 미룸
[액션아이템]
  A1. 시나리오 트리 export 요청
      담당 김PM / 마감 2026-09-01 / high
  A2. 합성 데이터 40건 작성
      담당 <회의에서 안 정해짐> / 마감 <회의에서 안 정해짐> / medium
  A3. 보안정책 확인
      담당 <녹취 불확실 — 오디오 재확인> / 마감 <녹취 불확실 — 오디오 재확인>
[미결 사항]
  Q1. [블로커] 외부 LLM API 가능한가

  ! 회의에서 담당/마감을 정하지 않은 액션 1건 — 참석자에게 확인
  ! 녹취가 불확실해 확인 못 한 액션 1건 — 원본 오디오 재확인
```

## 구글 드라이브 연동 — `SEND` 가 방식을 정한다

`.env` 의 `SEND` 한 값으로 갈린다. **지금 쓰는 값은 `SEND=api` 다.**

| `SEND` | 방식 | 설정 | 공유 링크 | Docs 변환 | 스케줄러 |
|---|---|---|---|---|---|
| `api` (현재) | Drive API 업로드 | Google Cloud OAuth | O | O | O |
| `sync` | Drive for desktop 폴더에 복사 | 없음 | X | X | X (가상드라이브가 안 보임) |
| (비움) | 로컬에만 저장 | — | — | — | O |

### `SEND=api` 준비 (이미 완료됨)

1. Google Cloud Console → **Google Drive API 사용 설정**
2. 사용자 인증 정보 → OAuth 클라이언트 ID → **데스크톱 앱** → JSON 을 `credentials.json` 으로 저장
   (`gcloud` 로는 만들 수 없다. Console UI 전용)
3. 첫 실행 때 브라우저 인증 → `token.json` 자동 생성

**스코프 주의**: 기본은 `drive.file`(이 앱이 만든 파일만 접근 — 최소 권한). 이미 수동으로
만들어 둔 폴더에 넣으려면 `DRIVE_SCOPE` 를 `https://www.googleapis.com/auth/drive` 로 올리고
`token.json` 을 지우고 재인증한다 (코드가 스코프 불일치를 감지해 알려준다).

### `SEND=sync` 를 쓸 때

`SYNC_DIR` 을 **직접** 적어야 한다. 자동 탐색을 일부러 안 한다 — 이 PC 에는 다른 사람
계정의 동기화 폴더도 있어서, 잘못 찾으면 남의 드라이브에 회의록이 올라간다.

```
SYNC_DIR=G:\내 드라이브\회의록
```

`MY_DRIVE_EMAIL` 을 채워 두면 업로드 전에 «내 계정인지» 확인하고, 아니면 막는다
([`src/drive_accounts.py`](src/drive_accounts.py)).

### 올라가는 구조

```
내 드라이브 / 회의록 / <날짜>_<제목> (업로드 2026-08-27 1502) /
├── <날짜>_<제목>.md
├── <날짜>_<제목>.html
├── <날짜>_<제목>.json
└── <날짜>_<제목>            ← Google Docs 변환본 (DRIVE_AS_GDOC=true)
```

## 노션 연동 — `NOTION_MODE=api`

**토큰 방식을 쓴다.** MCP 커넥터는 대화형 Claude 세션에서만 붙어서, 새벽 스케줄러처럼
사람이 없는 실행에서는 권한 승인 창이 없어 도구 호출이 막힌다.

준비물은 **두 가지**이고, 하나만 있으면 404 가 난다.

| | 무엇 | 어디서 |
|---|---|---|
| 1 | 토큰 → `.env` 의 `NOTION_TOKEN` | notion.so → 설정 → 연결 → **개발자 포털** → 내부 통합 |
| 2 | **그 통합을 페이지에 초대** | 올릴 페이지 → `•••` → 연결 → 만든 통합 추가 |
| 3 | 페이지 URL → `.env` 의 `NOTION_TARGET` | 그 페이지 → `•••` → 링크 복사 |

2 번이 노션의 함정이다. 토큰이 맞아도 페이지에 초대하지 않으면 **없는 페이지**로 보인다.
`--check` 나 GUI 의 «연결 확인» 이 이 둘을 구분해서 알려준다 (401 = 토큰, 404 = 초대).

마크다운을 거치지 않고 [`src/notion.py`](src/notion.py) 가 `Minutes` 구조를 **블록으로 직접**
만든다 — 액션은 체크박스, 근거는 인용, 한 줄 요약은 콜아웃. 마크다운 변환보다 정확하다.

## 파일 구성

```
src/schema.py       회의록 구조 (= 스키마 정본. 빈칸 라벨도 여기)
src/transcribe.py   로컬 STT (faster-whisper, VAD 필터)
src/extract.py      Claude API 추출 (경로 2 전용). 길면 분할 → 병합
src/extract_cli.py  claude -p 추출 (기본 경로). 인용 검증·재시도 포함
src/review.py       멈춤 게이트 — 번호 부여·선택·필터
src/render.py       md / html / json 렌더
src/sync.py         드라이브 동기화 폴더 복사 (설정 불필요)
src/drive.py        드라이브 API 업로드 (OAuth)
src/drive_accounts.py  내 드라이브가 맞는지 확인 (남의 계정 업로드 차단)
src/notion.py       노션 업로드 (API 토큰 · 블록 직접 생성)
src/pipeline.py     단계 함수 + CLI. GUI 도 여기를 부른다
src/gui.py          GUI (검수 모드 / 오토 모드)
prompts/            추출 규칙 (여기를 고쳐서 품질 조정)
templates/          출력 포맷
tests/test_offline.py  API 없이 도는 회귀 테스트 66개
setup.ipynb         환경 준비 (한 번)
run.ipynb           실제 사용 (매번)
회의록_GUI.bat      GUI 더블클릭 실행
.claude/skills/     Claude Code 가 따르는 절차서
```

```powershell
python tests/test_offline.py     # 코드 정상 여부 판정. 여기가 통과하면 실행 가능
```

**품질이 아쉬우면 코드가 아니라 [`prompts/extract_system.md`](prompts/extract_system.md)를 고친다.**

## 업로드에는 «업로드 시각» 이 붙는다

같은 회의를 여러 번 올리면 어느 것이 최신인지 알 수 없습니다. 그래서 올릴 때
괄호로 연·월·일·시각을 붙입니다.

| 대상 | 이름 |
|---|---|
| 노션 페이지 | `2026-08-25 킥오프 (업로드 2026-08-27 14:32)` |
| 드라이브 하위 폴더 | `2026-08-25_킥오프 (2026-08-27 1432)` |

로컬 파일명은 그대로 두고 `_v2` 규칙을 씁니다 — 로컬은 이미 덮어쓰기 방지가 있고,
파일명에 시각이 들어가면 같은 회의의 재생성본을 짝지어 보기 어렵습니다.

## 긴 회의 처리

입력이 `MAX_INPUT_TOKENS`(기본 300k)를 넘으면 자동으로 시간순 분할 추출 후 병합한다. 2시간 한국어 회의는 보통 분할 없이 한 번에 처리된다.

## 아직 안 한 것

- **화자분리(diarization)** — pyannote는 HF 토큰 + 약관 동의가 필요하고 오디오 처리 위치가 정책 문제가 된다. 지금은 화자 라벨 없이 진행하고, 발언에서 이름이 언급될 때만 참석자를 잡는다.
  > 담당자 확정률을 올리는 데는 diarization보다 **회의 마지막 3분에 "액션 확인" 순서를 넣는 것**이 훨씬 효과적이다.
- Slack / GitHub Issue 연동 (노션·드라이브는 완료)
- 액션아이템 칸반 뷰어 (`.json` 여러 개를 모아서)
- 새벽 3시 스케줄러 (작업 스케줄러 → `python -m src.pipeline --auto`)
