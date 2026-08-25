# 11. 개발 환경 — Docker Compose 스택

앱까지 컨테이너로 간다. 단, **한 컨테이너에 다 넣지 않고 compose 서비스로 나눈다.**

```
docker compose
├── db         pgvector/pgvector:pg17        volume: hra_pgdata
├── app        python:3.12-slim (직접 빌드)   bind mount: 프로젝트 전체
└── guardrail  vllm/vllm-openai  [profile: gpu]  volume: hra_hfcache
                        같은 네트워크. app 에서 db:5432 / guardrail:8000 으로 호출
```

## 0. 왜 한 컨테이너에 다 넣지 않는가

| | 단일 컨테이너 | compose 서비스 분리 |
|---|---|---|
| 프로세스 관리 | supervisor 등으로 postgres+python 동시 구동 (안티패턴) | 각 1프로세스 |
| 앱 이미지 재빌드 | **DB 데이터가 날아갈 위험** | DB는 별개 볼륨, 무영향 |
| 로그 | 뒤섞임 | `docker compose logs app` 으로 분리 |
| GPU | 앱 이미지에 CUDA를 얹어야 함 (수 GB) | 가드레일만 GPU 이미지 |
| 재시작 | 전체 재시작 | 개별 재시작 |

체감은 동일하다. `docker compose up -d` 한 줄이고, 서비스끼리 서비스명으로 통신한다.

---

## 1. 기동

```powershell
cp .env.example .env          # 값 채우기 (.env 는 커밋 금지)
docker compose build app
docker compose up -d db app
docker compose exec app bash  # 여기서 작업
```

- 최초 기동 시 `./db` 마운트로 **`schema.sql` 자동 적용**
- 스키마 변경 후 재적용: `docker compose down -v && docker compose up -d db` (초기 스크립트는 첫 기동에만 실행)
- DB 접속: 컨테이너 안에서 `db:5432`, 호스트(DBeaver 등)에서 `localhost:5433`

노트북:

```powershell
docker compose exec app jupyter lab --ip 0.0.0.0 --no-browser --allow-root
# http://localhost:8888
```

---

## 2. 개발 루프를 느리게 만들지 않는 3가지

컨테이너로 옮기면서 실험 회전 속도를 잃으면 안 된다. 3개로 방어한다.

**(1) 코드는 bind mount — 재빌드 없음**

`volumes: - .:/workspace` 이므로 로컬에서 파일을 고치면 컨테이너에 즉시 반영된다. **`requirements.txt`를 바꿀 때만** `docker compose build app`.

Dockerfile에서 `requirements.txt`를 먼저 COPY하고 코드는 COPY하지 않는 이유가 이것이다 — 의존성 레이어가 캐시된다.

**(2) HF 모델 캐시는 named volume**

```
hra_hfcache:/cache/hf   +   HF_HOME=/cache/hf
```

8B 모델이 16GB다. 컨테이너를 재생성할 때마다 다시 받으면 재앙이다. named volume이라 `docker compose down` 후에도 남는다. app과 guardrail이 **같은 캐시를 공유**한다.

**(3) 대용량 산출물은 Windows 파일시스템에 두지 않는다**

Windows + WSL2에서 bind mount(`C:\Users\...`)는 I/O가 느리다. 코드 편집은 문제없지만, 임베딩 인덱스·중간 산출물을 여기에 쓰면 체감이 나빠진다.

```
코드 · 문서 · 시드 데이터  ->  bind mount (편집 편의 우선)
임베딩 · 인덱스 · 캐시     ->  named volume hra_artifacts (/artifacts, 속도 우선)
```

---

## 3. GPU는 나중에, profile로

`guardrail` 서비스는 `profiles: ["gpu"]`라서 **평소엔 뜨지 않는다.**

```powershell
# 준비 확인
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# 준비되면
docker compose --profile gpu up -d
```

vLLM은 Windows 네이티브 미지원 → Docker Desktop의 **WSL2 백엔드 + nvidia-container-toolkit** 경유가 강제다.

| VRAM | 전략 |
|---|---|
| 48GB+ | 8B 2개 + 2.1B 1개 bf16 |
| 24GB | 8B는 AWQ 4bit, 2.1B는 bf16 |
| 12~16GB | prompt-2.1b 상시, siren-8b 4bit 조건부 호출 |
| GPU 없음 | prompt-2.1b를 CPU/GGUF로 (1토큰 생성이라 견딜 만함) |

> **STEP 1~4는 GPU 없이 전부 진행된다.** app 컨테이너는 CPU 전용(torch CPU 휠)으로 빌드했으므로 이미지가 가볍다. GPU 조달 결정은 STEP 5 직전으로 미룬다.

---

## 4. 파일 구성

```
Dockerfile           app 이미지 (python:3.12-slim + CPU torch)
docker-compose.yml   db / app / guardrail(gpu profile)
requirements.txt     app 의존성 (torch 제외 — Dockerfile에서 CPU 인덱스로 설치)
.dockerignore        .git, .venv, data/*, experiments 제외
.env.example         POSTGRES_PASSWORD, ANTHROPIC_API_KEY
```

`.env`는 커밋하지 않는다. API 키를 이미지에 굽지 않는다 (compose environment로 주입).

---

## 5. venv를 쓸 이유가 남아 있나

거의 없다. 다만 두 경우엔 로컬 venv가 편하다.

- IDE 자동완성·타입힌트 (또는 VSCode Dev Containers로 컨테이너에 붙으면 해결)
- 컨테이너 밖에서 빠른 스크립트 한 번

로컬에 만들 거라면 **3.12로 만든다.** 로컬 Python 3.14는 torch·vLLM 휠이 없다.

```powershell
py -3.12 -m venv .venv
```

---

## 6. 최종 구성도

```
Windows 11 / Docker Desktop (WSL2 backend)
│
├── hra-db          pgvector:pg17        :5433 -> 5432
│                   volume hra_pgdata
│
├── hra-app         python:3.12-slim     :8000 API  :8888 jupyter
│                   bind  ./ -> /workspace
│                   volume hra_hfcache /cache/hf
│                   volume hra_artifacts /artifacts
│
└── hra-guardrail   vllm/vllm-openai     :8001 -> 8000   [profile gpu]
                    volume hra_hfcache (app과 공유)
```
