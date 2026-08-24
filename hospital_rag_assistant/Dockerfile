# hospital_rag_assistant : app (CPU) — 수집·청킹·임베딩·검색·CRAG·평가
# GPU 가드레일 서빙은 별도 서비스(vllm/vllm-openai)로 분리한다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/cache/hf

# kiwipiepy·psycopg 빌드 및 런타임 최소 의존
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 의존성만 먼저 복사 → 코드 수정 시 이 레이어는 캐시됨 (재빌드 빠름)
COPY requirements.txt .
RUN pip install -U pip && \
    pip install --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install -r requirements.txt

# 코드는 compose에서 bind mount 한다 (개발 중 재빌드 불필요)
# 배포 이미지를 굽을 때만 아래 주석을 해제
# COPY . .

EXPOSE 8000 8888
CMD ["bash"]
