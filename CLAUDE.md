# AI_hub

프로젝트 2개 + 공용 도구. 상세는 각 폴더 README·진행상황 참조.

| 폴더 | 무엇 | 먼저 읽을 것 |
|---|---|---|
| `hospital_rag_assistant/` | 세브란스 병원 챗봇: 시나리오 단독 → 시나리오를 가드레일로 쓰는 RAG | `docs/00~11`, `진행상황.md` |
| `office_automation/` | 사무업무 자동화 (회의록 · 하네스 문서 · jsx 뷰어) | `meeting_minutes/README.md` |
| `docs/` | 이 리포에서 쓰는 기능 정리 | `docs/기능정리.md` |

진행 기록은 루트 `진행상황.md` 에 날짜별 불릿으로만 씁니다 (장황하게 쓰지 않음).

## 쓰고 있는 기능

- **MCP**: Google Drive · Notion · Gmail · Calendar (claude.ai 커넥터) · Playwright (브라우저)
- **Skill**: `meeting-minutes` (`office_automation/meeting_minutes/.claude/skills/`)
- **추출은 `claude -p` (CLI)** — Anthropic API 키를 쓰지 않음
- 상세: `docs/기능정리.md`

## 이 리포의 규칙

1. **우리가 아는 값은 모델에게 맡기지 않는다.** 제목·날짜는 코드가 덮어쓰고, 인용은 코드가 원문과 대조한다 (실제로 모델이 제목을 바꾸고 다른 파일을 읽은 사고가 있었다)
2. **설정은 한 곳.** `meeting_minutes/.env` 가 정본. 노트북·코드에 하드코딩 금지
3. **규칙을 두 곳에 적지 않는다.** 추출 규칙 정본은 `prompts/extract_system.md`. 프롬프트에 다시 쓰지 않는다
4. **조용히 덮어쓰지 않는다.** 같은 이름이면 `_v2` 로 넘긴다
5. **막힌 것도 기록한다.** 다음에 같은 시도를 반복하지 않도록 진행상황에 남긴다

## 알아둘 제약

- `config.py` 는 dataclass → `%autoreload` 가 갱신 못 함. 고치면 **커널 재시작** (노트북은 `%aimport -src.config` 로 제외해 둠)
- 이 PC GPU 는 AMD → `faster-whisper` 는 **CPU 전용**으로 돈다 (CTranslate2 는 CUDA 전용)
- Google «데스크톱 앱» OAuth 클라이언트는 **Console UI 에서만** 만들 수 있다 (gcloud 불가)
- Windows heredoc 은 백슬래시·`\n` 을 먹는다 → 스크립트는 파일로 쓰고 실행
- 콘솔이 cp949 → 파이썬 실행 시 `PYTHONIOENCODING=utf-8` 필요
