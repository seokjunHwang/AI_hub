# 07. 데이터 모델 (STEP 1)

DDL 초안: [`db/schema.sql`](../db/schema.sql)

## 0. 가장 중요한 결정: DB를 쪼개지 않는다

요청에 "비정형은 pgvector DB, 정형은 PostgreSQL" 이라고 되어 있었는데, **pgvector는 PostgreSQL의 확장(extension)** 이라 별개 DB가 아니다. 두 개로 나누면 잃는 게 크다.

```
[나쁨] 별도 벡터DB + 별도 Postgres
   질의: "소아 이비인후과 강남캠퍼스 검사 준비"
   -> 벡터DB에서 검색 후, 부서·캠퍼스·개정일 필터를 애플리케이션에서 후처리
   -> top-k를 이미 잘라먹은 뒤 필터링 => 정답이 k 밖으로 밀려남 (post-filter 문제)
   -> 정형 테이블과 조인 불가, 트랜잭션 경계 분리, 백업/감사 이중화

[좋음] 단일 Postgres + pgvector
   SELECT ... FROM rag.chunk c JOIN rag.document d USING(doc_id)
   WHERE d.dept_scope && ARRAY['ENT'] AND d.campus_scope && ARRAY['GN']
     AND d.effective_to IS NULL
   ORDER BY c.embedding <=> :qvec LIMIT 20;
   -> pre-filter 후 벡터 정렬. 이게 이 도메인에서 정확도를 가장 크게 올린다.
```

병원 안내 도메인은 **"맞는 문서인데 틀린 답"** 이 최다 실패 원인이다(부서·캠퍼스·대상·개정일 불일치). 그 방어가 곧 메타데이터 pre-filter이고, 그러려면 한 DB에 있어야 한다.

> 규모가 커져 벡터 검색이 병목이 되면 그때 읽기 전용 복제 또는 외부 인덱스로 분리한다. 처음부터 나눌 이유는 없다.

---

## 1. 스키마 구성

```
                      +---------------------------+
                      |  struct  (정형 사실)       |
                      |  숫자·시간·금액의 유일 원천 |
                      +-------------+-------------+
                                    |
      +-----------------------------+------------------------------+
      |                             |                              |
+-----v---------+          +--------v--------+           +---------v-------+
| rag (비정형)  |          | scen (시나리오) |           | meta (필터)     |
| document      |<-------->| node            |<--------->| tag / scope     |
| chunk(vector) |  L0 변환 | node_utterance  |  공통 태그 | chunk_tag       |
| revision      |          | node_link       |           | node_tag        |
+-------+-------+          +--------+--------+           +-----------------+
        |                           |
        +-------------+-------------+
                      |
        +-------------v-------------+       +------------------------+
        | guard (정책·감사)          |       | evalh (평가)           |
        | rule / risk_tier_map      |       | criterion  <- 사람 기준 |
        | pii_pattern               |       | goldenset / run/result |
        | decision_audit            |       +------------------------+
        +-------------+-------------+
                      |
        +-------------v-------------+
        | ops (운영 로그)            |
        | conversation / turn       |
        | retrieval_round (CRAG)    |
        | unanswered_log            |
        | escalation_log            |
        +---------------------------+
```

---

## 2. struct — 정형 사실

**여기가 숫자의 유일한 출처(single source of truth)다.** 진료시간·금액·연락처를 문서 텍스트에서 뽑지 않는다.

| 테이블 | 내용 | 비고 |
|---|---|---|
| `campus`, `building` | 캠퍼스·건물·층 | 스코핑의 기준축 |
| `department` | 진료과 (코드, 정식명, 별칭[]) | 별칭에 "귀코목" 같은 구어 포함 |
| `doctor`, `doctor_department` | 의료진, 소속 | 전문분야 텍스트는 rag로 |
| `clinic_schedule` | 진료시간 (dept/doctor, 요일, 시작·종료, 캠퍼스, effective 기간) | **시간 질의는 전부 여기서** |
| `closure` | 휴진·공휴일 예외 | schedule보다 우선 적용 |
| `exam_type`, `exam_prep` | 검사 종목, 준비사항 | prep 텍스트는 `verbatim=true` |
| `fee_item` | 수납 항목·금액·기준일 | 금액 질의는 전부 여기서 |
| `certificate_type` | 증명서 종류·발급경로·수수료 | |
| `checkup_package`, `checkup_item` | 건강검진 패키지 구성 | |
| `contact` | 부서별 연락처 | 전화번호 질의는 전부 여기서 |

**접근 방식: 파라미터화 SQL 템플릿 (Text-to-SQL 금지)**

```
intent: clinic_hours
slots:  {dept_code, campus_code, date}
->      SELECT * FROM struct.fn_clinic_hours(:dept, :campus, :date)
->      결과를 문자열로 포맷해 verbatim 슬롯에 주입
```

LLM의 역할은 슬롯 추출까지. SQL을 생성하게 하면 그 순간 숫자 정확성 보증이 깨진다.

---

## 3. rag — 비정형 코퍼스

`document` (문서 단위 메타데이터 = 필터의 주 무대)

| 컬럼 | 용도 |
|---|---|
| `source_tier` | `L0` 시나리오 확정답변 / `L1` 공개안내 / `L2` 앱매뉴얼 / `L3` 내부안내 |
| `risk_tier` | `A`/`B`/`C` — 문서 자체의 위험 등급 |
| `verbatim_required` | true면 재표현 금지 (금식지침·금액·법적고지) |
| `dept_scope[]`, `campus_scope[]`, `audience` | pre-filter 축. NULL = 전체 적용 |
| `effective_from`, `effective_to` | **개정일 관리. 만료 문서는 검색에서 제외** |
| `owner_team`, `approved_by`, `approved_at` | 책임 소재 (감사 대응) |
| `checksum`, `version` | 재인덱싱 판정 |

`chunk`

| 컬럼 | 용도 |
|---|---|
| `embedding vector(1024)` | HNSW 인덱스, cosine |
| `tsv tsvector` | BM25용. **Kiwi 형태소 사전 토크나이즈 후 `simple` config** |
| `heading_path` | "진료안내 > 검사 > MRI > 준비사항" — 청크 문맥 복원 |
| `parent_chunk_id` | 예외조항 절단 대비 **parent doc retrieval** |
| `has_exception_clause` | "단, ~" 조항 포함 플래그 (CRAG 기준 C5) |
| `verbatim` | document에서 상속, 청크 단위 override 가능 |

**한국어 BM25 주의**: Postgres 기본 `to_tsvector('korean')`은 없다. 선택지 3개 —
1. **Kiwi/Mecab으로 애플리케이션에서 토크나이즈** → `to_tsvector('simple', tokens)` (권장, 사전 커스터마이즈 가능)
2. `pg_bigm` — 2-gram, 설치 간단, 재현율 높고 정밀도 낮음
3. `pgroonga` — 성능 좋으나 운영 부담

병원 고유명사(진료과명, 검사명, 건물명)를 사용자 사전에 등록하는 게 1번의 최대 이점이다.

---

## 4. meta — 필터링 메타데이터

요청하신 "필터링을 위한 메타데이터 테이블". **통제 어휘(controlled vocabulary)를 테이블로 강제**하고, 자유 속성만 JSONB로 둔다.

```
tag_namespace   topic | dept | campus | audience | channel | lifecycle
tag             (namespace, code, label, parent_code)   <- 계층 태그
chunk_tag       (chunk_id, tag_id, weight)
node_tag        (node_id, tag_id)
```

왜 JSONB만 쓰지 않는가
- 운영팀이 태그를 관리해야 한다 → 목록·자동완성·오타 방지가 필요
- 태그 계층(`topic:예약 > topic:예약변경`)으로 상위 태그 검색이 하위를 포함해야 함
- 태그별 커버리지 통계(P6 갭 분석)를 SQL로 뽑아야 함

동시에 `document.attrs JSONB` 를 남겨 실험용 자유 속성을 받는다. 실험에서 자주 쓰이면 태그로 승격.

---

## 5. scen — 시나리오

```
scenario_flow      버전 단위 (승인 이력)
scenario_node      node_id, parent, label, canonical_answer,
                   verbatim_required, risk_tier, approved_by/at
node_utterance     발화 변형 + embedding  <- 인텐트 매칭 인덱스
                   source: 'branch_label' | 'human' | 'llm_aug'
node_link          전이 (from -> to, 조건)
node_fallback      abstain 시 착지 지점
```

**핵심 설계 2개**

1. **`node_utterance`를 별도 벡터 인덱스로 둔다** (rag.chunk와 통합하지 않음)
   - L0 우선 조회를 명시적 단계(R0)로 구현할 수 있다
   - 시나리오 고신뢰 히트 시 **생성·검증을 전부 스킵** → 안전·지연·비용 동시 이득
   - 통합 인덱스로 두면 이 최적화가 불가능해진다

2. **동일 `canonical_answer`를 `rag.document`(source_tier='L0')에도 미러링**
   - R0에서 미스했지만 RAG 검색에서는 히트할 수 있게
   - 미러링은 배치(SCENARIO SYNC)로, `checksum` 비교로 갱신

---

## 6. guard — 정책과 감사

| 테이블 | 내용 |
|---|---|
| `rule` | `type`: forbidden_phrase / emergency_keyword / pii_pattern / regex_block, `pattern`, `action`, `severity`, `active` |
| `risk_tier_map` | intent_code → tier (사람이 유지) |
| `intent_whitelist` | 승인된 안내 인텐트 (Siren I2 완화 근거) |
| `decision_audit` | **턴별 전 단계 결정 기록** |

`decision_audit` 이 감사 대응의 핵심이다. 저장 항목:

```
turn_id, stage(G0..G5), decision, label, score,
model_name, model_version, prompt_version, corpus_version,
latency_ms, created_at
```

모델·프롬프트·코퍼스 버전을 함께 남기지 않으면 **"3개월 전 그 답변이 왜 그렇게 나왔는지" 재현이 불가능**하다. 병원 감사에서 이걸 못 대면 신뢰를 잃는다.

---

## 7. evalh — 평가 (사람이 만든 기준의 저장소)

```
criterion       code(C1..C7), name, description, applies_to(retrieval|answer),
                severity, active, version     <- 사람이 작성·관리
goldenset       qid, question, bucket, expected_action,
                expected_answer, expected_doc_ids[]
eval_run        run_id, config JSONB (임베딩/청킹/프롬프트/임계/모델버전)
eval_result     run_id, qid, criterion_code, passed, score, note
```

**`criterion` 테이블을 런타임 CRAG 그레이더와 오프라인 평가 하네스가 공유한다.** 이게 하네스 엔지니어링의 핵심 — 기준이 한 곳에만 있으므로 평가 개선이 곧 런타임 개선이다. ([docs/09](09_crag_harness.md))

---

## 8. ops — 운영 로그

| 테이블 | 용도 |
|---|---|
| `conversation`, `turn`, `message` | 대화 이력 (PII 마스킹 후 저장) |
| `retrieval_round` | CRAG 라운드별: 쿼리, 필터, 결과 chunk_ids, grade, 실패유형, 액션 |
| `unanswered_log` | abstain·이관 질의 → P6 갭 분석 입력 |
| `escalation_log` | 상담원 이관 사유 분포 |

`retrieval_round` 가 CRAG 튜닝의 유일한 근거 데이터다. 라운드별로 남기지 않으면 "왜 재검색이 실패했는지"를 알 수 없다.

---

## 9. 합성 데이터 만들기 (실제 병원 데이터 없이 시작)

STEP 1을 지금 시작할 수 있게, 다음 순서로 자체 제작한다.

```
1) struct 시드      캠퍼스 2개 · 진료과 12개 · 의료진 30명 · 진료시간 · 휴진 20건
                    검사 15종 + 준비사항 · 수납항목 25건 · 증명서 8종 · 연락처
                    -> 여기서 이미 "정형 질의"의 정답셋이 자동 생성된다
2) rag 문서 40건    공개 병원 안내 페이지 문체를 모사해 직접 작성
                    ★ 반드시 포함할 함정:
                      - 예외조항 ("단, 만 6세 미만 소아는 …")
                      - 캠퍼스별 상이 운영시간
                      - 구버전/신버전 개정 쌍 (effective_to 설정)
                      - 표 형식 문서 (텍스트화 난이도 확인용)
3) scen 노드 25개   현재 앱 시나리오 구조를 모사, canonical_answer 작성
                    node_utterance는 노드당 10개 LLM 증강
4) 골든셋 200문항   docs/04 버킷 비율대로. 답변가능 55% / 불가 20% /
                    위험 15% / 멀티턴 10%
5) 가드레일 시드     응급 키워드 60개 · 금칙 표현 40개 · PII 패턴 12개
```

합성 데이터의 목적은 "잘 되는 데모"가 아니라 **실패 케이스 재현**이다. 함정 문서를 일부러 넣어야 파이프라인의 약점이 드러난다.
