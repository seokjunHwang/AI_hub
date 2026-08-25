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

## 선행 조건

- **커넥터 인증 필요.** Slack / Notion / Google Drive / Gmail / Calendar / Atlassian 커넥터가
  현재 미인증 상태 → claude.ai 커넥터 설정에서 인증해야 사용 가능
- 회의 녹음은 기밀·개인정보를 포함할 수 있음 → **STT를 로컬(whisper)로 돌릴지 외부 API를 쓸지 먼저 정책 결정**
