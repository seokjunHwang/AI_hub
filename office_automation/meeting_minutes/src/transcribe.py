"""오디오 -> 녹취록. faster-whisper 로 로컬 STT (외부 전송 없음)."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .config import CFG


def _hms(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def _progress(done: float, total: float, elapsed: float, segs: int) -> None:
    """진행 상황을 한 줄에 갱신해 보여준다.

    STT 는 길게 도는데 아무 출력이 없으면 멈춘 것과 구분이 안 된다.
    남은 시간은 «지금까지의 처리 속도» 로 추정한다.
    """
    if total <= 0:
        sys.stdout.write(f"\r[stt] {_hms(done)} 처리 · {segs} 세그먼트 · 경과 {_hms(elapsed)}   ")
        sys.stdout.flush()
        return

    pct = min(100.0, done / total * 100)
    filled = int(pct / 5)
    bar = "#" * filled + "." * (20 - filled)
    speed = (done / elapsed) if elapsed > 0 else 0.0
    eta = ((total - done) / speed) if speed > 0 else 0.0
    sys.stdout.write(
        f"\r[stt] [{bar}] {pct:5.1f}%  {_hms(done)}/{_hms(total)}  "
        f"{speed:.2f}x  남은 {_hms(eta)}  세그먼트 {segs}   "
    )
    sys.stdout.flush()


@dataclass
class Segment:
    start: float
    end: float
    text: str

    @property
    def ts(self) -> str:
        h, rem = divmod(int(self.start), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def line(self) -> str:
        return f"[{self.ts}] {self.text.strip()}"


def _cuda_count() -> tuple[int, str]:
    """CUDA 장치 수와 판정 근거. CTranslate2 의 GPU 지원은 CUDA(NVIDIA) 전용이다.

    AMD/Intel GPU 는 몇 장이 있어도 여기서 0 이 나온다 — ROCm/DirectML 미지원.
    """
    try:
        import ctranslate2
    except ImportError:
        return 0, "ctranslate2 미설치"
    try:
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:
        return 0, f"CUDA 조회 실패 ({e.__class__.__name__})"
    if n == 0:
        return 0, "CUDA 장치 없음 (CTranslate2 는 NVIDIA CUDA 전용 — AMD/Intel GPU 는 사용 불가)"
    return n, f"CUDA 장치 {n}개"


def _resolve_device() -> tuple[str, str, str]:
    """device / compute_type / 판정 근거를 돌려준다."""
    device, compute = CFG.whisper_device, CFG.whisper_compute
    if device != "auto":
        return device, ("float16" if compute == "auto" else compute), "WHISPER_DEVICE 로 지정됨"

    n, why = _cuda_count()
    if n > 0:
        return "cuda", ("float16" if compute == "auto" else compute), why
    return "cpu", ("int8" if compute == "auto" else compute), why


def _free_path(path: Path) -> Path:
    """같은 이름이 있으면 조용히 덮어쓰지 않고 _v2, _v3 로 넘긴다."""
    if not path.exists():
        return path
    n = 2
    while path.with_name(f"{path.stem}_v{n}{path.suffix}").exists():
        n += 1
    new = path.with_name(f"{path.stem}_v{n}{path.suffix}")
    print(f"[stt] 같은 이름이 이미 있어 {new.name} 로 저장합니다 (덮어쓰지 않음)")
    return new


def transcribe(audio_path: Path, out_path: Path | None = None) -> Path:
    """오디오를 타임스탬프가 붙은 텍스트로 변환하고 파일 경로를 돌려준다."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "faster-whisper 가 필요합니다: pip install faster-whisper\n"
            "(ffmpeg 도 PATH 에 있어야 합니다)"
        ) from e

    device, compute, why = _resolve_device()
    print(f"[stt] 장치   {device} ({compute})  <- {why}")
    print(f"[stt] 모델   {CFG.whisper_model}  언어={CFG.language}")
    if device == "cpu":
        print("[stt] CPU 로 돕니다. 모델이 클수록 오래 걸립니다 (.env 의 WHISPER_MODEL 조정)")
    print("[stt] 모델 로딩 중... (처음이면 다운로드가 있어 몇 분 걸립니다)")

    t_load = time.time()
    model = WhisperModel(CFG.whisper_model, device=device, compute_type=compute)
    print(f"[stt] 모델 로딩 완료 ({time.time() - t_load:.0f}초)")

    raw_segments, info = model.transcribe(
        str(audio_path),
        language=CFG.language,
        vad_filter=True,                      # 침묵 구간 제거 -> 환각 감소
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,     # 반복 루프 방지
        beam_size=5,
    )

    total = float(getattr(info, "duration", 0.0) or 0.0)
    print(f"[stt] 오디오 길이 {_hms(total)} — 변환 시작")

    segments: List[Segment] = []
    t0 = time.time()
    last_print = 0.0
    for s in raw_segments:
        if s.text.strip():
            segments.append(Segment(start=s.start, end=s.end, text=s.text))

        now = time.time()
        if now - last_print >= 1.0:            # 1초에 한 번만 갱신
            last_print = now
            _progress(done=s.end, total=total, elapsed=now - t0, segs=len(segments))

    elapsed = time.time() - t0
    _progress(done=total, total=total, elapsed=elapsed, segs=len(segments))
    print()                                    # 진행줄 마무리
    speed = (total / elapsed) if elapsed > 0 else 0.0
    print(f"[stt] 완료: {len(segments)} 세그먼트 · 오디오 {_hms(total)} · "
          f"소요 {_hms(elapsed)} · {speed:.2f}x 실시간")

    out_path = out_path or CFG.transcript_dir / f"{audio_path.stem}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path = _free_path(out_path)   # STT 는 비싸다. 기존 녹취록을 덮어쓰지 않는다
    out_path.write_text(render_transcript(segments), encoding="utf-8")
    print(f"[stt] 저장: {out_path}")
    return out_path


def render_transcript(segments: Iterable[Segment]) -> str:
    return "\n".join(s.line() for s in segments)


# --- 화자분리(diarization) ---------------------------------------------------
# 미구현. pyannote.audio 를 쓰려면 HuggingFace 토큰 + 모델 이용약관 동의가 필요하고,
# 오디오를 어디서 처리하는지가 기밀 정책 문제가 된다.
# 지금은 화자 라벨 없이 진행하고, Claude 가 발언 내용에서 이름이 언급될 때만 참석자를 잡는다.
# 담당자 확정률을 올리려면 diarization 보다 "회의 마지막 3분 액션 확인" 이 효과가 크다.
