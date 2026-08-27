# office_automation

사무업무 자동화 프로젝트. Claude 커넥터 · Skills · CLI(Claude Code) · Artifacts를 조합해
**반복되는 사무 작업을 입력 하나로 끝내는 파이프라인**을 만든다.

## 기본 구조

```
[입력]                    [처리: Claude Code CLI + Skills]           [출력]
회의 녹음 / 문서              STT -> 요약 -> 구조화 추출                Slack 게시
Slack 스레드        ──▶      (액션아이템·결정사항·담당자·마감)   ──▶    Notion DB
GitHub 활동                  검증 -> 포맷 변환                        GitHub Issue
캘린더 일정                                                          HTML 뷰어(Artifact)
```

- **Skills** = 업무별 절차를 문서로 고정 (회의록 정리 규칙, 주간보고 포맷, 톤 규칙)
- **CLI + hooks** = 트리거·정기 실행 (`/loop`, cron, 파일 감지)
- **커넥터(MCP)** = Slack / Notion / GitHub / Calendar / Drive 읽기·쓰기
- **Artifacts** = 액션 칸반, 프로세스 다이어그램, 타임라인 시각화

## 현재 상태

| 프로젝트 | 상태 | 진입점 |
|---|---|---|
| **회의록 정리** | **작동 중.** STT → 추출 → 검수 → 드라이브 · 노션 | [`meeting_minutes/README.md`](meeting_minutes/README.md) |
| jsx 뷰어 (서비스팀장님 코드) | 참고용 보관 | `서비스팀장님_자동화,하네스/jsx_viewer/` |
| 주간보고 · 릴리즈노트 | 미착수 | [`docs/자동화_후보.md`](docs/자동화_후보.md) |

회의록은 **`회의록_GUI.bat` 더블클릭**이 기본 사용법이다 (사람검수 모드 / 오토 모드).

## 문서

| 문서 | 내용 |
|---|---|
| [docs/자동화_후보.md](docs/자동화_후보.md) | 자동화 대상 업무 후보 + 우선순위 |
| [docs/하네스_엔지니어링_플로우.md](docs/하네스_엔지니어링_플로우.md) | 발표덱 2부 분석 — 하네스 플로우 · 문서 다섯 벌 · 우리 리포 적용안 |

## 디렉터리

```
skills/      업무별 Skill 정의 (회의록·주간보고·릴리즈노트 등)
pipelines/   입력별 처리 스크립트
templates/   출력 포맷 (Notion 템플릿, HTML 뷰어, 보고서 양식)
docs/        기획·설계
```

## 정해진 정책 (다시 논의하지 않음)

- **STT 는 로컬.** 회의 녹음에 계약·인사·환자 언급이 섞일 수 있어 오디오를 외부로 보내지
  않는다. 텍스트만 Claude 로 간다
- **추출은 `claude -p` (구독).** Anthropic API 키를 쓰지 않는다
- **노션·드라이브는 무인 실행 때문에 API 토큰/OAuth.** MCP 커넥터는 대화형 세션에서만
  붙어서 새벽 스케줄러에서는 권한 승인 창이 없어 막힌다
- **드라이브 스코프는 `drive.file`** (이 앱이 만든 파일만). 최소 권한을 유지한다

### 아직 인증 안 된 커넥터

Slack / Figma / Atlassian → claude.ai → 설정 → 커넥터. 회의록 파이프라인에는 필요 없다.
