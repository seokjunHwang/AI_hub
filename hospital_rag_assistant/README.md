# hospital_rag_assistant

병원 앱 상담 챗봇을 **시나리오(레일) 단독** 구조에서 **시나리오를 가드레일로 쓰는 RAG** 구조로 옮기기 위한 연구/실험 리포지토리.

## 한 줄 문제 정의

> 할루시네이션이 무서워서 시나리오만 태웠더니, 사용자가 "이게 AI냐"고 느낀다.
> 반대로 생성형을 열면 정확성 보증이 안 된다.
> → **시나리오를 "출력을 통제하는 장치"로 재배치하고, 생성형은 "이해와 표현"만 담당하게 만든다.**

## 핵심 가설

기존 시나리오 트리의 각 노드(확정 답변)는 버려야 할 레거시가 아니라,
**이미 법무/의료진 검수를 통과한 최고 품질의 근거 코퍼스**다.

- 시나리오 답변 = canonical answer store → 인덱싱 대상
- LLM 역할 = ① 사용자 발화 이해 ② 근거 검색 ③ 검색된 확정 답변을 자연어로 재표현
- LLM이 하지 않는 것 = 없는 사실 만들기, 의학적 판단, 수치 추론

즉 **"자유 생성 + 사후 필터"가 아니라 "근거 제약 + 재표현"** 이 이 프로젝트의 노선이다.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/00_problem_and_requirements.md](docs/00_problem_and_requirements.md) | 요구사항, 이해관계자, 성공 기준 |
| [docs/01_architecture.md](docs/01_architecture.md) | 리스크 티어 라우팅 + Grounded RAG 아키텍처 |
| [docs/02_roadmap_experiments.md](docs/02_roadmap_experiments.md) | 연습 기획 P0~P7 (단계별 실험) |
| [docs/03_ideation_backlog.md](docs/03_ideation_backlog.md) | 아이데이션 / 백로그 |
| [docs/04_eval_plan.md](docs/04_eval_plan.md) | 평가 설계 (할루시네이션 측정 포함) |
| [docs/05_answer_policy.md](docs/05_answer_policy.md) | 답변 정책·금칙·에스컬레이션 규칙 |
| [docs/06_system_architecture.md](docs/06_system_architecture.md) | 시스템 아키텍처 · 런타임 흐름 · 지연 예산 |
| [docs/07_data_model.md](docs/07_data_model.md) | 데이터 모델 (STEP 1) · 스키마 구성 · 합성 데이터 계획 |
| [docs/08_guardrail_design.md](docs/08_guardrail_design.md) | 가드레일 7층 스택 · Kanana Safeguard 통합 |
| [docs/09_crag_harness.md](docs/09_crag_harness.md) | CRAG 하네스 · 채점 기준 C1~C7 · 재검색 액션 매핑 |
| [db/schema.sql](db/schema.sql) | PostgreSQL + pgvector DDL 초안 |
| [docs/10_sequence.md](docs/10_sequence.md) | 시퀀스 다이어그램 (런타임 · CRAG 루프 · 개발 순서) |
| [docs/11_dev_env.md](docs/11_dev_env.md) | 개발 환경 (Docker vs venv · GPU 전략) |

## 디렉터리

```
db/schema.sql   PostgreSQL + pgvector DDL (struct/rag/meta/scen/guard/evalh/ops)
data/raw        수집 원문 (공개 문서, 시나리오 export)
data/interim    파싱/정제 중간물
data/corpus     청킹·메타데이터 부착 완료된 인덱싱 입력
src/ingest      수집·파싱·청킹
src/retrieve    BM25 / dense / hybrid / rerank
src/generate    프롬프트, 근거강제 생성, abstain
src/guardrail   라우터, 금칙어, PII, claim 검증
src/eval        평가 러너, 메트릭
evalset         골든셋 (질문-정답-근거 문서)
experiments     실험별 설정·결과 로그
```

## 환경 주의

로컬 Python은 3.14. 임베딩/벡터DB 계열 휠이 아직 미비할 수 있으므로
**3.11 또는 3.12 가상환경**을 별도로 만들어 사용한다.
