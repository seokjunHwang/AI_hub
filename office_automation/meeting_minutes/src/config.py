"""설정. 사용자가 바꾸는 값은 전부 `.env` 에 있고, 이 파일이 그걸 읽는다.

정본은 `.env.example` 이다 — 새 설정을 추가하면 거기 «설명과 함께» 적는다.
코드에만 있고 .env.example 에 없는 설정은 사용자가 존재를 알 수 없다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv 없으면 환경변수만 사용
    pass

ROOT = Path(__file__).resolve().parent.parent


def _s(key: str, default: str = "") -> str:
    """문자열 설정. 공백만 있으면 기본값."""
    v = os.getenv(key, "")
    return v.strip() or default


def _b(key: str, default: bool = False) -> bool:
    """true/false/1/0/yes/no 를 받는다."""
    v = _s(key).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def _i(key: str, default: int) -> int:
    try:
        return int(_s(key, str(default)))
    except ValueError:
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(_s(key, str(default)))
    except ValueError:
        return default


def _path(key: str, default_rel: str) -> Path:
    """경로 설정. 상대경로면 프로젝트 루트 기준으로 푼다."""
    v = _s(key)
    p = Path(v) if v else (ROOT / default_rel)
    return p if p.is_absolute() else (ROOT / p)


@dataclass
class Config:
    # ---- 1. 입력 ----
    input_audio: str = field(default_factory=lambda: _s("INPUT_AUDIO"))
    input_transcript: str = field(default_factory=lambda: _s("INPUT_TRANSCRIPT"))
    meeting_title: str = field(default_factory=lambda: _s("MEETING_TITLE"))
    meeting_date: str = field(default_factory=lambda: _s("MEETING_DATE"))

    # ---- 2. STT ----
    whisper_model: str = field(default_factory=lambda: _s("WHISPER_MODEL", "medium"))
    whisper_device: str = field(default_factory=lambda: _s("WHISPER_DEVICE", "auto"))
    whisper_compute: str = field(default_factory=lambda: _s("WHISPER_COMPUTE", "auto"))
    language: str = field(default_factory=lambda: _s("STT_LANGUAGE", "ko"))

    # ---- 3. 추출 ----
    extract_mode: str = field(default_factory=lambda: _s("EXTRACT_MODE", "cli"))
    #  cli 모드: claude -p 에 넘기는 --model / --effort
    cli_model: str = field(default_factory=lambda: _s("CLI_MODEL"))
    cli_effort: str = field(default_factory=lambda: _s("CLI_EFFORT"))
    model: str = field(default_factory=lambda: _s("CLAUDE_MODEL", "claude-opus-5"))
    max_tokens: int = field(default_factory=lambda: _i("CLAUDE_MAX_TOKENS", 16000))
    max_input_tokens: int = field(default_factory=lambda: _i("MAX_INPUT_TOKENS", 300000))
    api_timeout: float = field(default_factory=lambda: _f("CLAUDE_TIMEOUT", 900))

    # ---- 4. 검토 ----
    accept: str = field(default_factory=lambda: _s("ACCEPT"))

    # ---- 5. 산출물 ----
    output_dir: Path = field(default_factory=lambda: _path("OUTPUT_DIR", "data/minutes"))
    output_layout: str = field(default_factory=lambda: _s("OUTPUT_LAYOUT", "nested"))
    output_overwrite: bool = field(default_factory=lambda: _b("OUTPUT_OVERWRITE", False))

    # ---- 6. 전송 ----
    send: str = field(default_factory=lambda: _s("SEND"))
    my_drive_email: str = field(default_factory=lambda: _s("MY_DRIVE_EMAIL"))
    drive_folder_name: str = field(default_factory=lambda: _s("DRIVE_FOLDER_NAME", "회의록"))
    drive_subfolder: str = field(default_factory=lambda: _s("DRIVE_SUBFOLDER", "slug"))
    drive_parent_id: str = field(default_factory=lambda: _s("DRIVE_PARENT_ID"))
    drive_as_gdoc: bool = field(default_factory=lambda: _b("DRIVE_AS_GDOC", True))
    drive_scope: str = field(
        default_factory=lambda: _s(
            "DRIVE_SCOPE", "https://www.googleapis.com/auth/drive.file"
        )
    )
    drive_credentials: Path = field(
        default_factory=lambda: _path("DRIVE_CREDENTIALS", "credentials.json")
    )
    drive_token: Path = field(default_factory=lambda: _path("DRIVE_TOKEN", "token.json"))
    sync_dir: str = field(default_factory=lambda: _s("SYNC_DIR"))

    # ---- 7. 노션 ----
    #  api = 토큰으로 직접 호출 (무인 자동화 가능) / mcp = 채팅창에서 Claude 에게
    notion_mode: str = field(default_factory=lambda: _s("NOTION_MODE", "api"))
    notion_token: str = field(default_factory=lambda: _s("NOTION_TOKEN"))
    notion_target: str = field(default_factory=lambda: _s("NOTION_TARGET"))

    # ---- 8. 기타 ----
    open_html: bool = field(default_factory=lambda: _b("OPEN_HTML", False))

    # ---- 경로 (고정) ----
    audio_dir: Path = field(default_factory=lambda: ROOT / "data/audio")
    transcript_dir: Path = field(default_factory=lambda: ROOT / "data/transcripts")
    prompt_dir: Path = field(default_factory=lambda: ROOT / "prompts")
    template_dir: Path = field(default_factory=lambda: ROOT / "templates")

    def ensure_dirs(self) -> None:
        for d in (self.audio_dir, self.transcript_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def resolve_input(self) -> tuple[Optional[Path], Optional[Path]]:
        """(오디오, 녹취록) 중 실제로 쓸 것을 돌려준다. 오디오가 우선."""
        audio = Path(self.input_audio) if self.input_audio else None
        if audio and not audio.is_absolute():
            audio = ROOT / audio
        tr = Path(self.input_transcript) if self.input_transcript else None
        if tr and not tr.is_absolute():
            tr = ROOT / tr
        return audio, tr

    def subfolder_for(self, slug: str) -> Optional[str]:
        """DRIVE_SUBFOLDER 규칙에 따른 드라이브 하위 폴더 이름."""
        mode = self.drive_subfolder.lower()
        if mode == "none":
            return None
        if mode == "month":
            return slug[:7] if len(slug) >= 7 else slug
        return slug

    def summary(self) -> str:
        """설정을 사람이 읽는 형태로. 비밀값은 마스킹한다."""
        key = "설정됨" if os.getenv("ANTHROPIC_API_KEY") else "없음"
        lines = [
            f"입력      audio={self.input_audio or '-'}  transcript={self.input_transcript or '-'}",
            f"회의      title={self.meeting_title or '(자동)'}  date={self.meeting_date or '(미지정)'}",
            f"STT       {self.whisper_model} / {self.whisper_device} / {self.language}",
            f"추출      mode={self.extract_mode}"
            + (f"  {self.cli_model or '(기본)'}/{self.cli_effort or '(기본)'}"
               if self.extract_mode == "cli" else f"  model={self.model}  API키={key}"),
            f"검토      accept={self.accept or '(노트북에서 직접)'}",
            f"산출물    {self.output_dir}  layout={self.output_layout}  overwrite={self.output_overwrite}",
            f"전송      send={self.send or '(로컬만)'}",
            f"드라이브   {self.drive_folder_name} / subfolder={self.drive_subfolder}"
            f"  gdoc={self.drive_as_gdoc}",
            f"          scope={self.drive_scope.rsplit('/', 1)[-1]}",
            f"노션      mode={self.notion_mode}  토큰={'설정됨' if self.notion_token else '없음'}"
            f"  대상={'있음' if self.notion_target else '없음'}",
        ]
        return "\n".join(lines)


CFG = Config()


def reload() -> Config:
    """`.env` 를 다시 읽어 CFG 를 «제자리에서» 갱신한다.

    왜 새 객체를 만들지 않는가
        다른 모듈들이 `from .config import CFG` 로 «그 객체» 를 붙잡고 있다.
        새로 만들어 대입하면 이 모듈의 이름만 바뀌고 남들은 옛 객체를 계속 본다.
        그래서 필드만 덮어써서 «같은 객체» 를 갱신한다.

    커널 재시작이 필요한 경우는 여전히 있다 — config.py 자체(필드 추가·삭제)를
    고쳤을 때다. 값만 바뀐 `.env` 는 이 함수로 충분하다.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)   # override=True 여야 이미 로드된 값을 덮어쓴다
    except ImportError:
        pass

    fresh = Config()
    for k, v in vars(fresh).items():
        setattr(CFG, k, v)
    return CFG
