# 09. CRAG 하네스 — 사람이 만든 기준으로 검색을 채점하고 자동 재검색

## 0. 무엇을 만드는가

```
검색 -> [그레이더: 사람이 만든 기준 C1~C7로 채점] -> 판정
                                                    |
                            +-----------------------+----------------------+
                            |                       |                      |
                        CORRECT                AMBIGUOUS              INCORRECT
                            |                       |                      |
                        생성으로              지식 정제 후 생성     실패유형 -> 액션 매핑
                                                                     -> 재검색 1라운드
                                                                     -> 실패 시 abstain
```

원본 CRAG(Corrective RAG)와의 차이 **두 가지**가 이 설계의 핵심이다.

| | 원본 CRAG | 이 프로젝트 |
|---|---|---|
| 그레이더 | 학습된 retrieval evaluator (스칼라 점수) | **사람이 작성한 기준 C1~C7 체크리스트** (DB에 저장, 감사 가능) |
| INCORRECT 폴백 | **웹 검색**으로 외부 지식 보강 | **웹 금지.** 쿼리 재작성 재검색 → 실패 시 abstain |

웹 검색 폴백을 제거하는 이유: 일반 의학 웹문서는 코퍼스 사용 금지 등급이다([docs/01](01_architecture.md) §3). 병원이 확정하지 않은 정보를 답변 근거로 쓰는 순간 이 프로젝트의 전제가 무너진다. **CRAG를 그대로 쓰면 안 되고, 폴백 방향을 안쪽으로 접어야 한다.**

---

## 1. 채점 기준 (사람이 작성 · `evalh.criterion` 테이블)

검색 결과 집합에 대해 채점한다. 각 기준은 실패 시 액션이 정해져 있다.

| 코드 | 기준 | 판정 방법 | 심각도 | 실패 액션 |
|---|---|---|---|---|
| **C1** 관련성 | 질의 의도에 답하는 정보를 포함하는가 | LLM 그레이더 | block | `REWRITE` |
| **C2** 충분성 | 답변 슬롯(누가·어디·언제·조건·예외)을 모두 덮는가 | LLM 그레이더 + 슬롯 체크 | retry | `DECOMPOSE` |
| **C3** 최신성 | `effective_to`가 유효한가, 개정 최신본인가 | **SQL 규칙** | block | `LATEST_ONLY` |
| **C4** 스코프 일치 | 질의의 부서·캠퍼스·대상(성인/소아)과 문서 스코프가 일치하는가 | **SQL 규칙** | block | `CLARIFY` |
| **C5** 예외조항 완결성 | "단, ~" 조항이 청크 경계에서 잘리지 않았는가 | 규칙(`has_exception_clause`) + LLM | block | `PARENT_EXPAND` |
| **C6** 상충 없음 | 여러 청크가 서로 다른 값을 말하지 않는가 | 규칙(숫자 diff) + LLM | block | `LATEST_ONLY` + 알림 |
| **C7** 소스 등급 | L0/L1 근거가 있는가 (L3 단독은 저신뢰) | **SQL 규칙** | retry | `TIER_FILTER` |

**설계 포인트: C3·C4·C7은 LLM을 쓰지 않는다.** SQL로 결정적으로 판정 가능하다 → 지연 0, 재현 100%, 감사 가능. LLM 그레이더는 C1·C2·C5·C6의 의미 판단에만 쓴다. 이게 "하네스"의 비용·신뢰성을 결정한다.

`C5`는 병원 도메인 특화 기준이다. 안내문의 예외조항("단, 만 6세 미만 소아는 보호자 동반")이 청크 경계에서 잘리면 **문법적으로 완전하고 사실상 위험한 답변**이 나온다. 일반 RAG 평가 기준에는 없다.

---

## 2. 판정 규칙

```python
GRADE(results, criteria):
    hard = [c for c in criteria if c.severity == 'block']
    soft = [c for c in criteria if c.severity == 'retry']

    if all(pass(c) for c in hard) and all(pass(c) for c in soft):
        return CORRECT
    if all(pass(c) for c in hard):
        return AMBIGUOUS          # soft만 실패 -> 정제 후 생성 가능
    return INCORRECT              # hard 실패 -> 재검색 또는 abstain
```

| 판정 | 처리 |
|---|---|
| **CORRECT** | 그대로 생성 단계로 |
| **AMBIGUOUS** | **지식 정제**: 청크에서 질의 관련 스트립만 추출·재구성 → 부족 슬롯 명시 → `sufficiency: partial`로 생성 (답변에 "이 부분은 확인이 어렵다" 포함) |
| **INCORRECT** | 실패 기준 → 액션 매핑표로 액션 결정 → 재검색 1라운드 |

---

## 3. 실패유형 → 액션 매핑표 (결정적)

**자유 재시도를 허용하지 않는다.** 실패 기준마다 액션이 하나로 정해져 있다. 이게 루프 폭주를 막고 감사 가능성을 만든다.

| 액션 | 실행 내용 | 트리거 |
|---|---|---|
| `REWRITE` | 용어 정규화·동의어 확장으로 쿼리 재작성 후 재검색 (`department.aliases`, `exam_type.aliases` 활용) | C1 |
| `DECOMPOSE` | 서브질의로 분해 → 각각 검색 → 병합 (예: "예약 변경하고 주차 확인" → 2개) | C2 |
| `PARENT_EXPAND` | `parent_chunk_id`를 따라 상위 청크/문서 전체로 확장 검색 | C5 |
| `LATEST_ONLY` | `effective_to IS NULL` 강제 + 최신 `version`만. **필터 완화는 절대 금지** | C3, C6 |
| `TIER_FILTER` | `source_tier IN ('L0','L1')`로 제한 재검색 | C7 |
| `CLARIFY` | **재검색하지 않고 사용자에게 되묻는다** ("어느 캠퍼스 기준으로 안내드릴까요?") | C4 |
| `ABSTAIN` | 답변 포기 → 시나리오 폴백 또는 상담 연결 | 재검색 후에도 hard 실패 |

**`CLARIFY`가 가장 중요한 액션이다.** 스코프 불일치(C4)는 재검색으로 해결되지 않는다. 정보가 부족한 게 아니라 **질의가 모호한** 것이다. 이때 되묻는 건 시나리오 분기를 자연어로 수행하는 것과 같고, 사용자에게는 가장 "AI답게" 느껴진다([docs/03](03_ideation_backlog.md) A3).

액션 충돌 시 우선순위: `CLARIFY` > `LATEST_ONLY` > `PARENT_EXPAND` > `DECOMPOSE` > `REWRITE` > `TIER_FILTER`

---

## 4. 루프 제어

```
MAX_ROUNDS = 2                    (초기 검색 1 + 재검색 1)
LATENCY_BUDGET = 1200ms           CRAG 전체 예산. 초과 시 즉시 ABSTAIN
NO_REPEAT      쿼리 해시 이력 유지. 동일 쿼리 재실행 금지
NO_RELAX       필터 완화(개정일·스코프) 절대 금지. 완화보다 abstain이 낫다
LOG_EVERY      모든 라운드를 ops.retrieval_round 에 기록
```

`MAX_ROUNDS = 2` 근거: 지연 예산([docs/06](06_system_architecture.md) §3)에서 재검색 1라운드가 한계선(2960ms)이다. 3라운드는 P95 3초를 깬다. 그리고 실무적으로 2라운드에서 못 찾으면 **코퍼스에 없는 것**이다 — 재시도가 아니라 P6 갭 리포트로 보낼 신호다.

`NO_RELAX`가 특히 중요하다. "결과가 없으니 개정일 필터를 풀어보자"는 유혹이 반드시 생기는데, 그게 곧 작년 진료시간을 안내하는 사고다.

---

## 5. 루프 다이어그램

```
      질의(정규화 완료) + 스코프 슬롯
                  |
        +---------v---------+
        | R1  하이브리드 검색 |  BM25(Kiwi) + Dense -> RRF -> Rerank -> top-k
        |     + pre-filter  |  effective_to IS NULL, dept/campus/audience
        +---------+---------+
                  |
        +---------v-------------------------------+
        | GRADER                                  |
        |  SQL 규칙   C3 최신 · C4 스코프 · C7 등급 |  <- LLM 미사용, 0ms
        |  LLM 판정   C1 관련 · C2 충분 ·          |
        |             C5 예외절단 · C6 상충        |
        +---------+-------------------------------+
                  |
     +------------+------------+-----------------------+
     |            |                                    |
  CORRECT     AMBIGUOUS                            INCORRECT
     |            |                                    |
     |     +------v---------+                 +--------v-------------+
     |     | 지식 정제       |                 | 실패기준 -> 액션 매핑 |
     |     | 관련 스트립 추출|                 +--------+-------------+
     |     | 부족 슬롯 명시  |                          |
     |     +------+---------+          +---------+------+------+---------+
     |            |                    |         |             |         |
     |            |                CLARIFY   LATEST_ONLY  PARENT_EXPAND  REWRITE
     |            |                    |         |             |      DECOMPOSE
     |            |                    |         +------+------+---------+
     |            |                    |                |
     |            |              되묻기 응답        round_no < 2 ?
     |            |                    |            /          \
     |            |                    |          yes           no
     |            |                    |           |             |
     |            |                    |     R1로 재진입      ABSTAIN
     |            |                    |                          |
     +------------+--------------------+--------------------------+
                  |                    |                          |
            생성(GEN) 진행         END(되묻기)            END(폴백/상담연결)
                  |
             모든 라운드 -> ops.retrieval_round 기록
             (query, filters, strategy, grade, failed_criteria, next_action)
```

---

## 6. 하네스 엔지니어링: 런타임과 평가가 같은 기준을 쓴다

이 설계의 핵심 구조.

```
                    evalh.criterion   (사람이 작성 · 버전 관리)
                     C1..C7 + description + severity + fail_action
                              |
              +---------------+---------------+
              |                               |
        [런타임 그레이더]              [오프라인 평가 하네스]
        매 요청마다 채점                골든셋 200문항 일괄 채점
        -> 재검색 액션 결정             -> criterion별 통과율 리포트
              |                               |
        ops.retrieval_round            evalh.eval_result
              |                               |
              +---------------+---------------+
                              |
                  주간 분석: 어느 기준이 가장 많이 실패하는가
                  -> 기준 문구 개선 / 청킹 개선 / 액션 매핑 조정
                  -> 개선이 런타임에 즉시 반영 (같은 테이블을 읽으므로)
```

기준이 **한 곳에만 존재**하므로:
- 평가에서 발견한 개선이 런타임에 자동 반영된다 (기준 문구를 고치면 그레이더 프롬프트가 바뀜)
- "런타임은 관대하고 평가는 엄격했다" 같은 괴리가 구조적으로 불가능하다
- 병원에 제출할 문서가 곧 실행 코드의 일부다 → **기준표가 계약 문서 역할을 한다**

이게 "기준은 사람이 제작한다"를 시스템으로 구현한 형태다.

---

## 7. 그레이더 프롬프트 스켈레톤

```
당신은 병원 안내 챗봇의 검색 결과 심사관이다. 답변을 작성하지 말고 채점만 한다.

[질의]        {rewritten_query}
[스코프]      dept={dept} campus={campus} audience={audience} date={date}
[검색 결과]   {chunks: id, source_tier, effective_from, heading_path, text}

[사전 판정 결과 — SQL로 확정됨, 재판정 금지]
  C3 최신성:   PASS
  C4 스코프:   FAIL (질의 캠퍼스=GN, 문서 스코프=[SC])
  C7 소스등급: PASS

[네가 판정할 기준]
  C1 관련성:        {criterion.description}
  C2 충분성:        {criterion.description}
  C5 예외조항 완결: {criterion.description}
  C6 상충 없음:     {criterion.description}

출력(JSON only):
{ "C1": {"pass": bool, "reason": str, "evidence_chunk_ids": [int]},
  "C2": {"pass": bool, "missing_slots": [str]},
  "C5": {"pass": bool, "truncated_chunk_ids": [int]},
  "C6": {"pass": bool, "conflicts": [{"chunk_ids":[int], "field": str}]} }
```

포인트
- SQL 확정 판정을 **미리 알려주고 재판정을 금지**한다 → LLM이 결정적 사실을 흔들지 못함
- `missing_slots`, `truncated_chunk_ids`, `conflicts` 같은 **구조화된 실패 사유**를 강제 → 액션 매핑이 코드로 결정됨 (LLM이 다음 행동을 고르지 않는다)
- 기준 설명(`description`)은 DB에서 주입 → 기준 개선이 코드 수정 없이 반영

---

## 8. 이 하네스가 만드는 지표

`ops.retrieval_round` 집계로 나오는 것들:

| 지표 | 의미 | 활용 |
|---|---|---|
| 라운드 1 CORRECT 비율 | 검색 1회 성공률 | 청킹·임베딩 개선 대상 |
| 기준별 실패 분포 | 어디서 깨지는가 | C5 다발 → 청킹 전략 문제. C4 다발 → 스코프 슬롯 추출 문제 |
| 액션별 성공률 | 재검색이 실제로 구제하는가 | 성공률 낮은 액션은 제거 후보 |
| CLARIFY 비율 | 질의 모호성 수준 | 높으면 UI에서 미리 부서 선택을 받는 게 낫다 |
| ABSTAIN 사유 분포 | 코퍼스 공백 vs 검색 실패 | 전자는 P6 갭 리포트, 후자는 P1 검색 개선 |

마지막 행이 중요하다. **"못 답했다"를 코퍼스 문제와 검색 문제로 분리**할 수 있어야 개선 방향이 정해진다. 이 분리가 안 되면 팀은 계속 엉뚱한 곳을 고친다.
