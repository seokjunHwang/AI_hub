-- hospital_rag_assistant : schema draft v0.1
-- PostgreSQL 17 + pgvector
-- 임베딩 차원은 baseline BGE-m3 기준 1024. 모델 교체 시 vector(N) 수정 + 재인덱싱.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS struct;   -- 정형 사실 (숫자·시간·금액의 유일 원천)
CREATE SCHEMA IF NOT EXISTS rag;      -- 비정형 코퍼스
CREATE SCHEMA IF NOT EXISTS meta;     -- 필터링 메타데이터
CREATE SCHEMA IF NOT EXISTS scen;     -- 시나리오
CREATE SCHEMA IF NOT EXISTS guard;    -- 가드레일 정책·감사
CREATE SCHEMA IF NOT EXISTS evalh;    -- 평가 하네스
CREATE SCHEMA IF NOT EXISTS ops;      -- 운영 로그

-- ============================================================
-- struct : 정형
-- ============================================================

CREATE TABLE struct.campus (
  campus_code   text PRIMARY KEY,
  name          text NOT NULL,
  address       text,
  main_tel      text
);

CREATE TABLE struct.building (
  building_id   bigserial PRIMARY KEY,
  campus_code   text NOT NULL REFERENCES struct.campus,
  name          text NOT NULL,
  floors        text
);

CREATE TABLE struct.department (
  dept_code     text PRIMARY KEY,
  name          text NOT NULL,
  aliases       text[] DEFAULT '{}',      -- '귀코목', 'ENT' 등 구어·약어
  parent_code   text REFERENCES struct.department
);
CREATE INDEX ON struct.department USING gin (aliases);

CREATE TABLE struct.doctor (
  doctor_id     bigserial PRIMARY KEY,
  name          text NOT NULL,
  title         text,
  active        boolean DEFAULT true
);

CREATE TABLE struct.doctor_department (
  doctor_id     bigint REFERENCES struct.doctor,
  dept_code     text   REFERENCES struct.department,
  campus_code   text   REFERENCES struct.campus,
  PRIMARY KEY (doctor_id, dept_code, campus_code)
);

-- 진료시간 : 모든 시간 질의의 정답 원천
CREATE TABLE struct.clinic_schedule (
  schedule_id     bigserial PRIMARY KEY,
  dept_code       text NOT NULL REFERENCES struct.department,
  doctor_id       bigint REFERENCES struct.doctor,      -- NULL = 진료과 전체
  campus_code     text NOT NULL REFERENCES struct.campus,
  dow             smallint NOT NULL CHECK (dow BETWEEN 0 AND 6),
  start_time      time NOT NULL,
  end_time        time NOT NULL,
  session         text,                                 -- 오전/오후
  reception_close time,                                 -- 접수 마감
  effective_from  date NOT NULL DEFAULT current_date,
  effective_to    date,                                 -- NULL = 현행
  note            text
);
CREATE INDEX ON struct.clinic_schedule (dept_code, campus_code, dow)
  WHERE effective_to IS NULL;

-- 휴진 : schedule 보다 우선
CREATE TABLE struct.closure (
  closure_id    bigserial PRIMARY KEY,
  scope         text NOT NULL CHECK (scope IN ('hospital','campus','dept','doctor')),
  campus_code   text REFERENCES struct.campus,
  dept_code     text REFERENCES struct.department,
  doctor_id     bigint REFERENCES struct.doctor,
  closed_from   date NOT NULL,
  closed_to     date NOT NULL,
  reason        text
);

CREATE TABLE struct.exam_type (
  exam_code     text PRIMARY KEY,
  name          text NOT NULL,
  aliases       text[] DEFAULT '{}',                    -- '엠알아이' 등
  dept_code     text REFERENCES struct.department
);

-- 검사 준비사항 : verbatim 필수 (금식 시간 등)
CREATE TABLE struct.exam_prep (
  prep_id        bigserial PRIMARY KEY,
  exam_code      text NOT NULL REFERENCES struct.exam_type,
  audience       text DEFAULT 'all',                    -- all | adult | child | pregnant
  prep_text      text NOT NULL,                         -- 원문 그대로 출력
  fasting_hours  numeric,
  effective_from date NOT NULL DEFAULT current_date,
  effective_to   date,
  approved_by    text,
  approved_at    timestamptz
);

CREATE TABLE struct.fee_item (
  fee_id         bigserial PRIMARY KEY,
  category       text NOT NULL,                         -- 증명서/검사/제증명 등
  name           text NOT NULL,
  amount         numeric NOT NULL,
  currency       text DEFAULT 'KRW',
  campus_code    text REFERENCES struct.campus,
  effective_from date NOT NULL DEFAULT current_date,
  effective_to   date,
  note           text
);

CREATE TABLE struct.certificate_type (
  cert_code      text PRIMARY KEY,
  name           text NOT NULL,
  issue_channels text[] NOT NULL,                       -- app / kiosk / desk / online
  required_docs  text,
  fee_id         bigint REFERENCES struct.fee_item,
  process_note   text
);

CREATE TABLE struct.checkup_package (
  package_code  text PRIMARY KEY,
  name          text NOT NULL,
  target        text,
  duration_min  int,
  fee_id        bigint REFERENCES struct.fee_item
);

CREATE TABLE struct.checkup_item (
  package_code  text REFERENCES struct.checkup_package,
  exam_code     text REFERENCES struct.exam_type,
  PRIMARY KEY (package_code, exam_code)
);

CREATE TABLE struct.contact (
  contact_id    bigserial PRIMARY KEY,
  label         text NOT NULL,                           -- '예약센터', '원무과'
  tel           text NOT NULL,
  campus_code   text REFERENCES struct.campus,
  dept_code     text REFERENCES struct.department,
  hours_note    text
);

-- 파라미터화 SQL 템플릿 예시 (Text-to-SQL 대체)
CREATE OR REPLACE FUNCTION struct.fn_clinic_hours(
  p_dept text, p_campus text, p_date date
) RETURNS TABLE (dept text, campus text, dow smallint,
                 start_time time, end_time time, reception_close time, closed boolean)
LANGUAGE sql STABLE AS $$
  SELECT d.name, c.name, s.dow, s.start_time, s.end_time, s.reception_close,
         EXISTS (SELECT 1 FROM struct.closure cl
                  WHERE p_date BETWEEN cl.closed_from AND cl.closed_to
                    AND (cl.dept_code = s.dept_code OR cl.dept_code IS NULL)
                    AND (cl.campus_code = s.campus_code OR cl.campus_code IS NULL))
  FROM struct.clinic_schedule s
  JOIN struct.department d USING (dept_code)
  JOIN struct.campus     c USING (campus_code)
  WHERE s.dept_code = p_dept
    AND s.campus_code = p_campus
    AND s.dow = EXTRACT(dow FROM p_date)::smallint
    AND s.effective_from <= p_date
    AND (s.effective_to IS NULL OR s.effective_to >= p_date);
$$;

-- ============================================================
-- rag : 비정형
-- ============================================================

CREATE TABLE rag.document (
  doc_id            bigserial PRIMARY KEY,
  title             text NOT NULL,
  source_tier       text NOT NULL CHECK (source_tier IN ('L0','L1','L2','L3')),
  source_type       text NOT NULL,          -- scenario | web | manual | internal
  source_uri        text,
  risk_tier         char(1) NOT NULL DEFAULT 'B' CHECK (risk_tier IN ('A','B','C')),
  verbatim_required boolean NOT NULL DEFAULT false,
  dept_scope        text[],                 -- NULL = 전체 적용
  campus_scope      text[],
  audience          text DEFAULT 'all',
  lang              text DEFAULT 'ko',
  effective_from    date NOT NULL DEFAULT current_date,
  effective_to      date,                   -- NULL = 현행. 만료 문서는 검색 제외
  version           int  NOT NULL DEFAULT 1,
  checksum          text,
  owner_team        text,
  approved_by       text,
  approved_at       timestamptz,
  attrs             jsonb DEFAULT '{}',     -- 실험용 자유 속성
  created_at        timestamptz DEFAULT now(),
  updated_at        timestamptz DEFAULT now()
);
CREATE INDEX ON rag.document USING gin (dept_scope);
CREATE INDEX ON rag.document USING gin (campus_scope);
CREATE INDEX ON rag.document (source_tier) WHERE effective_to IS NULL;

CREATE TABLE rag.document_revision (
  rev_id      bigserial PRIMARY KEY,
  doc_id      bigint NOT NULL REFERENCES rag.document ON DELETE CASCADE,
  version     int NOT NULL,
  diff_note   text,
  changed_by  text,
  changed_at  timestamptz DEFAULT now()
);

CREATE TABLE rag.chunk (
  chunk_id            bigserial PRIMARY KEY,
  doc_id              bigint NOT NULL REFERENCES rag.document ON DELETE CASCADE,
  ord                 int NOT NULL,
  parent_chunk_id     bigint REFERENCES rag.chunk,     -- parent doc retrieval
  heading_path        text,                            -- '진료안내 > 검사 > MRI'
  text                text NOT NULL,
  token_count         int,
  verbatim            boolean NOT NULL DEFAULT false,
  has_exception_clause boolean NOT NULL DEFAULT false, -- '단, ~' 포함 (기준 C5)
  embedding           vector(1024),
  tsv                 tsvector,                        -- Kiwi 토크나이즈 결과 기반
  embed_model         text,
  chunk_strategy      text,                            -- fixed | heading | qa
  created_at          timestamptz DEFAULT now(),
  UNIQUE (doc_id, ord, chunk_strategy)
);
CREATE INDEX ON rag.chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON rag.chunk USING gin (tsv);
CREATE INDEX ON rag.chunk (doc_id);

-- ============================================================
-- meta : 필터링 메타데이터 (통제 어휘)
-- ============================================================

CREATE TABLE meta.tag_namespace (
  namespace   text PRIMARY KEY,             -- topic|dept|campus|audience|channel|lifecycle
  description text
);

CREATE TABLE meta.tag (
  tag_id      bigserial PRIMARY KEY,
  namespace   text NOT NULL REFERENCES meta.tag_namespace,
  code        text NOT NULL,
  label       text NOT NULL,
  parent_id   bigint REFERENCES meta.tag,   -- 계층 태그
  active      boolean DEFAULT true,
  UNIQUE (namespace, code)
);

CREATE TABLE meta.chunk_tag (
  chunk_id  bigint REFERENCES rag.chunk ON DELETE CASCADE,
  tag_id    bigint REFERENCES meta.tag,
  weight    real DEFAULT 1.0,
  source    text DEFAULT 'human',           -- human | llm | rule
  PRIMARY KEY (chunk_id, tag_id)
);

-- ============================================================
-- scen : 시나리오
-- ============================================================

CREATE TABLE scen.flow (
  flow_id     bigserial PRIMARY KEY,
  name        text NOT NULL,
  version     text NOT NULL,
  approved_by text,
  approved_at timestamptz,
  active      boolean DEFAULT false,
  UNIQUE (name, version)
);

CREATE TABLE scen.node (
  node_id            bigserial PRIMARY KEY,
  flow_id            bigint NOT NULL REFERENCES scen.flow,
  ext_code           text,                   -- 원 시나리오 툴의 노드 ID
  parent_id          bigint REFERENCES scen.node,
  label              text NOT NULL,          -- 분기 버튼 라벨
  canonical_answer   text,                   -- 검수 완료 확정 답변
  verbatim_required  boolean NOT NULL DEFAULT false,
  risk_tier          char(1) NOT NULL DEFAULT 'B',
  intent_code        text,
  mirrored_doc_id    bigint REFERENCES rag.document,   -- L0 미러링 대상
  approved_by        text,
  approved_at        timestamptz,
  UNIQUE (flow_id, ext_code)
);

CREATE TABLE scen.node_utterance (
  utt_id     bigserial PRIMARY KEY,
  node_id    bigint NOT NULL REFERENCES scen.node ON DELETE CASCADE,
  text       text NOT NULL,
  source     text NOT NULL DEFAULT 'llm_aug' CHECK (source IN ('branch_label','human','llm_aug','real_log')),
  embedding  vector(1024),
  verified   boolean DEFAULT false
);
CREATE INDEX ON scen.node_utterance USING hnsw (embedding vector_cosine_ops);

CREATE TABLE scen.node_link (
  from_node  bigint REFERENCES scen.node ON DELETE CASCADE,
  to_node    bigint REFERENCES scen.node ON DELETE CASCADE,
  condition  text,
  PRIMARY KEY (from_node, to_node)
);

CREATE TABLE scen.node_tag (
  node_id bigint REFERENCES scen.node ON DELETE CASCADE,
  tag_id  bigint REFERENCES meta.tag,
  PRIMARY KEY (node_id, tag_id)
);

-- ============================================================
-- guard : 가드레일 정책·감사
-- ============================================================

CREATE TABLE guard.rule (
  rule_id    bigserial PRIMARY KEY,
  rule_type  text NOT NULL CHECK (rule_type IN
             ('emergency_keyword','forbidden_phrase','pii_pattern','regex_block','normalize')),
  pattern    text NOT NULL,
  is_regex   boolean DEFAULT false,
  action     text NOT NULL,                 -- interrupt|block|mask|reject_answer|warn
  severity   smallint NOT NULL DEFAULT 3,
  note       text,
  active     boolean DEFAULT true,
  version    int DEFAULT 1
);

CREATE TABLE guard.risk_tier_map (
  intent_code text PRIMARY KEY,
  risk_tier   char(1) NOT NULL CHECK (risk_tier IN ('A','B','C')),
  rationale   text,
  approved_by text
);

-- Siren I2 완화 근거가 되는 승인 인텐트 목록
CREATE TABLE guard.intent_whitelist (
  intent_code text PRIMARY KEY REFERENCES guard.risk_tier_map,
  min_confidence real NOT NULL DEFAULT 0.9,
  approved_by  text,
  approved_at  timestamptz
);

CREATE TABLE guard.decision_audit (
  audit_id       bigserial PRIMARY KEY,
  turn_id        bigint NOT NULL,
  stage          text NOT NULL,             -- G0|G1|G2|Q|R0|R1|CRAG|GEN|G5|OUT
  decision       text NOT NULL,             -- pass|block|interrupt|abstain|escalate
  label          text,                      -- SAFE | UNSAFE-A1 | UNSAFE-I2 ...
  score          real,                      -- <SAFE> vs <UNSAFE-*> 확률
  model_name     text,
  model_version  text,
  prompt_version text,
  corpus_version text,
  latency_ms     int,
  detail         jsonb DEFAULT '{}',
  created_at     timestamptz DEFAULT now()
);
CREATE INDEX ON guard.decision_audit (turn_id);
CREATE INDEX ON guard.decision_audit (created_at);

-- ============================================================
-- evalh : 평가 하네스 (사람이 만든 기준의 저장소)
-- ============================================================

CREATE TABLE evalh.criterion (
  code        text PRIMARY KEY,             -- C1..C7, A1..A5
  name        text NOT NULL,
  description text NOT NULL,                -- 판정 지침 (그레이더 프롬프트에 주입)
  applies_to  text NOT NULL CHECK (applies_to IN ('retrieval','answer','safety')),
  severity    text NOT NULL CHECK (severity IN ('block','retry','warn')),
  fail_action text,                         -- 실패 시 액션 코드 (docs/09 매핑표)
  version     int DEFAULT 1,
  active      boolean DEFAULT true,
  authored_by text
);

CREATE TABLE evalh.goldenset (
  qid             bigserial PRIMARY KEY,
  question        text NOT NULL,
  bucket          text NOT NULL CHECK (bucket IN
                  ('answerable_single','answerable_multi','unanswerable','risky','multiturn')),
  context_turns   jsonb,                    -- 멀티턴 선행 발화
  expected_action text NOT NULL,            -- answer|abstain|escalate|refuse|emergency
  expected_answer text,
  expected_doc_ids bigint[],
  expected_label  text,                     -- 기대 가드레일 라벨 (UNSAFE-I2 등)
  authored_by     text,
  active          boolean DEFAULT true
);

CREATE TABLE evalh.eval_run (
  run_id     bigserial PRIMARY KEY,
  label      text,
  config     jsonb NOT NULL,                -- 임베딩/청킹/프롬프트/임계/모델버전
  started_at timestamptz DEFAULT now(),
  ended_at   timestamptz
);

CREATE TABLE evalh.eval_result (
  run_id         bigint REFERENCES evalh.eval_run ON DELETE CASCADE,
  qid            bigint REFERENCES evalh.goldenset,
  criterion_code text REFERENCES evalh.criterion,
  passed         boolean NOT NULL,
  score          real,
  actual_action  text,
  note           text,
  PRIMARY KEY (run_id, qid, criterion_code)
);

-- ============================================================
-- ops : 운영 로그
-- ============================================================

CREATE TABLE ops.conversation (
  conv_id     bigserial PRIMARY KEY,
  user_ref    text,                          -- 앱 사용자 해시 (원문 저장 금지)
  channel     text DEFAULT 'app',
  started_at  timestamptz DEFAULT now()
);

CREATE TABLE ops.turn (
  turn_id        bigserial PRIMARY KEY,
  conv_id        bigint NOT NULL REFERENCES ops.conversation ON DELETE CASCADE,
  seq            int NOT NULL,
  user_text_masked text NOT NULL,            -- PII 마스킹 후
  user_text_hash text,                       -- 원문 해시 (감사 대조용)
  rewritten_query text,
  risk_tier      char(1),
  final_action   text,                       -- answer|abstain|escalate|refuse|emergency
  answer_text    text,
  citations      bigint[],
  total_latency_ms int,
  created_at     timestamptz DEFAULT now(),
  UNIQUE (conv_id, seq)
);

-- CRAG 라운드별 로그 : 재검색 튜닝의 유일한 근거 데이터
CREATE TABLE ops.retrieval_round (
  round_id      bigserial PRIMARY KEY,
  turn_id       bigint NOT NULL REFERENCES ops.turn ON DELETE CASCADE,
  round_no      smallint NOT NULL,
  query_text    text NOT NULL,
  filters       jsonb,
  strategy      text,                        -- bm25|dense|hybrid|parent_expand
  result_chunks bigint[],
  grade         text CHECK (grade IN ('CORRECT','AMBIGUOUS','INCORRECT')),
  failed_criteria text[],                    -- ['C2','C5']
  next_action   text,                        -- rewrite|decompose|parent_expand|clarify|abstain
  latency_ms    int,
  created_at    timestamptz DEFAULT now(),
  UNIQUE (turn_id, round_no)
);

CREATE TABLE ops.unanswered_log (
  id           bigserial PRIMARY KEY,
  turn_id      bigint REFERENCES ops.turn,
  query_text   text NOT NULL,
  reason       text NOT NULL,                -- no_evidence|verify_failed|tier_a|timeout
  embedding    vector(1024),                 -- 갭 클러스터링용
  cluster_id   int,
  created_at   timestamptz DEFAULT now()
);
CREATE INDEX ON ops.unanswered_log USING hnsw (embedding vector_cosine_ops);

CREATE TABLE ops.escalation_log (
  id         bigserial PRIMARY KEY,
  turn_id    bigint REFERENCES ops.turn,
  reason     text NOT NULL,
  created_at timestamptz DEFAULT now()
);

-- ============================================================
-- 검색 예시 : 메타데이터 pre-filter + 하이브리드
-- ============================================================
-- WITH filtered AS (
--   SELECT c.chunk_id, c.text, c.embedding, c.tsv, d.source_tier, d.verbatim_required
--   FROM rag.chunk c JOIN rag.document d USING (doc_id)
--   WHERE d.effective_to IS NULL
--     AND (d.dept_scope   IS NULL OR d.dept_scope   && ARRAY[:dept])
--     AND (d.campus_scope IS NULL OR d.campus_scope && ARRAY[:campus])
--     AND (d.audience = 'all' OR d.audience = :audience)
--     AND d.source_tier <> 'L3'
-- ),
-- dense AS (SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> :qvec) r
--           FROM filtered LIMIT 50),
-- sparse AS (SELECT chunk_id, row_number() OVER (ORDER BY ts_rank(tsv, :qts) DESC) r
--            FROM filtered WHERE tsv @@ :qts LIMIT 50)
-- SELECT chunk_id, SUM(1.0/(60+r)) AS rrf   -- Reciprocal Rank Fusion
-- FROM (SELECT * FROM dense UNION ALL SELECT * FROM sparse) u
-- GROUP BY chunk_id ORDER BY rrf DESC LIMIT 20;
