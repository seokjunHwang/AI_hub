"""설정. .env 로 덮어쓴다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 없으면 환경변수만 사용
    pass

ROOT = Path(__file__).resolve().parent.parent


def _p(env: str, default: str) -> Path:
    return Path(os.getenv(env, str(ROOT / default)))


@dataclass
class Config:
    # --- Claude ---
    model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")
    max_tokens: int = int(os.getenv("CLAUDE_MAX_TOKENS", "16000"))
    # 1M 컨텍스트지만, 이 이상이면 분할 추출 후 병합한다
    max_input_tokens: int = int(os.getenv("MAX_INPUT_TOKENS", "300000"))
    api_timeout: float = float(os.getenv("CLAUDE_TIMEOUT", "900"))

    # --- STT ---
    whisper_model: str = os.getenv("WHISPER_MODEL", "large-v3")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "auto")
    whisper_compute: str = os.getenv("WHISPER_COMPUTE", "auto")
    language: str = os.getenv("STT_LANGUAGE", "ko")

    # --- 경로 ---
    audio_dir: Path = field(default_factory=lambda: _p("AUDIO_DIR", "data/audio"))
    transcript_dir: Path = field(
        default_factory=lambda: _p("TRANSCRIPT_DIR", "data/transcripts")
    )
    output_dir: Path = field(default_factory=lambda: _p("OUTPUT_DIR", "data/minutes"))
    prompt_dir: Path = field(default_factory=lambda: ROOT / "prompts")
    template_dir: Path = field(default_factory=lambda: ROOT / "templates")

    # --- Google Drive: 동기화 폴더 방식 (설정 불필요, 권장) ---
    # Drive for desktop 이 돌고 있으면 이 폴더에 복사만 하면 업로드된다.
    # 비우면 G:~Z: 의 «내 드라이브» 를 자동 탐색한다.
    sync_dir: str = os.getenv("SYNC_DIR", "")

    # --- Google Drive: API 방식 (공유 링크·Docs 변환이 필요할 때만) ---
    drive_folder_name: str = os.getenv("DRIVE_FOLDER_NAME", "회의록")
    drive_parent_id: str = os.getenv("DRIVE_PARENT_ID", "")  # 비우면 My Drive 루트
    drive_scope: str = os.getenv(
        # drive.file = 이 앱이 만든 파일만 접근 (최소 권한, 권장)
        # 기존에 수동으로 만든 폴더에 넣으려면 .../auth/drive 필요
        "DRIVE_SCOPE",
        "https://www.googleapis.com/auth/drive.file",
    )
    drive_credentials: Path = field(
        default_factory=lambda: _p("DRIVE_CREDENTIALS", "credentials.json")
    )
    drive_token: Path = field(default_factory=lambda: _p("DRIVE_TOKEN", "token.json"))

    def ensure_dirs(self) -> None:
        for d in (self.audio_dir, self.transcript_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)


CFG = Config()
