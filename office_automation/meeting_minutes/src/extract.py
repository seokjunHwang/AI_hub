"""녹취록 -> 구조화 회의록. Claude structured output 사용."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import anthropic

from .config import CFG
from .schema import Minutes

_SYSTEM_CACHE: Optional[str] = None


def _system_prompt() -> str:
    global _SYSTEM_CACHE
    if _SYSTEM_CACHE is None:
        _SYSTEM_CACHE = (CFG.prompt_dir / "extract_system.md").read_text(encoding="utf-8")
    return _SYSTEM_CACHE


_CLIENT: Optional[anthropic.Anthropic] = None


def _client() -> anthropic.Anthropic:
    """클라이언트를 재사용한다. 호출마다 새로 만들면 커넥션 풀도 매번 새로 생긴다.

    (한 번의 extract 가 토큰 카운트 + 청크별 추출 + 병합으로 여러 번 호출된다)
    """
    global _CLIENT
    if _CLIENT is None:
        # 녹취록이 길면 응답까지 오래 걸린다. 기본 10분 타임아웃을 늘려둔다.
        _CLIENT = anthropic.Anthropic(timeout=CFG.api_timeout)
    return _CLIENT


def _system_blocks() -> list[dict]:
    # 시스템 프롬프트는 매 호출 동일 -> 캐시. 분할 추출 시 호출 수만큼 이득이 난다.
    return [
        {
            "type": "text",
            "text": _system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _user_block(transcript: str, title: str | None, date: str | None, note: str = "") -> str:
    head = []
    if title:
        head.append(f"회의 제목: {title}")
    if date:
        head.append(f"회의 날짜: {date}")
    if note:
        head.append(note)
    meta = "\n".join(head)
    return (
        (meta + "\n\n" if meta else "")
        + "아래는 회의 녹취록이다. 회의록을 구조화해서 추출하라.\n\n"
        + "<transcript>\n"
        + transcript
        + "\n</transcript>"
    )


def count_input_tokens(transcript: str, title: str | None, date: str | None) -> int:
    client = _client()
    r = client.messages.count_tokens(
        model=CFG.model,
        system=_system_blocks(),
        messages=[{"role": "user", "content": _user_block(transcript, title, date)}],
    )
    return r.input_tokens


def _extract_once(
    transcript: str, title: str | None, date: str | None, note: str = ""
) -> Minutes:
    client = _client()
    resp = client.messages.parse(
        model=CFG.model,
        max_tokens=CFG.max_tokens,
        thinking={"type": "adaptive"},
        system=_system_blocks(),
        messages=[{"role": "user", "content": _user_block(transcript, title, date, note)}],
        output_format=Minutes,
    )
    return resp.parsed_output


def _split_transcript(transcript: str, parts: int) -> List[str]:
    """줄 단위로 균등 분할. 타임스탬프 줄 경계를 깨지 않는다.

    줄 수가 부족하면(붙여넣은 녹취록은 개행이 아예 없는 경우가 있다 — Clova Note,
    회의앱 자막 복사) 문자 수로 잘라낸다. 이 폴백이 없으면 통째로 API 로 가서
    컨텍스트 초과로 실패한다.

    보장하는 것(불변식)
      · 줄 기반 경로: 조각당 줄 수 <= ceil(줄수 / parts)
      · 문자 기반 경로: 조각당 문자 수 <= ceil(길이 / parts)
      · 조각을 이어붙이면 원본과 동일 (내용 유실 없음)
    보장하지 않는 것
      · 조각 개수 == parts. 문장 경계에서 끊으므로 parts+1 이 될 수 있다
      · 조각의 «문자 수» 균등. 줄 길이가 불균등하면 평균을 넘는 조각이 생긴다
        -> 그래서 extract() 가 parts 를 계산할 때 여유(SAFETY)를 둔다
    """
    if parts <= 1:
        return [transcript]

    lines = [ln for ln in transcript.splitlines() if ln.strip()]
    if len(lines) >= parts:
        size = (len(lines) + parts - 1) // parts
        return ["\n".join(lines[i : i + size]) for i in range(0, len(lines), size)]

    # 폴백: 문자 기준 분할. 되도록 문장 끝(마침표·물음표)에서 끊는다.
    print(f"[extract] 줄 수({len(lines)})가 부족해 문자 기준으로 분할합니다")
    size = (len(transcript) + parts - 1) // parts
    chunks, start = [], 0
    while start < len(transcript):
        end = min(start + size, len(transcript))
        if end < len(transcript):
            window = transcript[start:end]
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("다. "))
            # 뒤쪽 15% 안에 문장 끝이 있을 때만 거기서 끊는다.
            # 더 앞까지 허용하면 조각이 짧아져 호출 수만 늘어난다.
            if cut > size * 0.85:
                end = start + cut + 1
        chunks.append(transcript[start:end])
        start = end
    return chunks


def _merge(partials: List[Minutes], title: str | None, date: str | None) -> Minutes:
    """분할 추출 결과를 하나로 병합. 중복 제거·시간순 정렬은 모델에게 맡긴다."""
    client = _client()
    payload = json.dumps(
        [p.model_dump() for p in partials], ensure_ascii=False, indent=2
    )
    # 병합에는 녹취록이 없다. 추출용 시스템 프롬프트("아래는 녹취록이다", "transcript
    # 에서 인용하라")를 그대로 재사용하면 없는 근거를 만들라는 지시가 된다.
    merge_system = (
        "당신은 같은 회의를 나눠 추출한 부분 회의록들을 하나로 병합한다.\n"
        "- 새로운 사실을 만들지 않는다. 입력에 있는 항목만 쓴다\n"
        "- quote 는 입력에 있는 문자열을 그대로 옮긴다. 새로 쓰거나 다듬지 않는다\n"
        "- owner/due 가 비어 있으면 비운 채로 두고 status 도 그대로 옮긴다\n"
        "- 한국어로 쓴다"
    )
    resp = client.messages.parse(
        model=CFG.model,
        max_tokens=CFG.max_tokens,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": merge_system, "cache_control": {"type": "ephemeral"}}],
        messages=[
            {
                "role": "user",
                "content": (
                    "같은 회의를 시간순으로 분할해 추출한 부분 회의록들이다. "
                    "하나의 회의록으로 병합하라.\n"
                    "- 중복 항목은 합친다\n"
                    "- 앞부분에서 논의만 됐다가 뒤에서 확정된 항목은 decisions 로 올린다\n"
                    "- quote 는 유지한다. 새로 만들지 않는다\n"
                    "- 항목을 임의로 버리지 않는다\n\n"
                    f"회의 제목: {title or '(미지정)'}\n회의 날짜: {date or '(미지정)'}\n\n"
                    f"<partials>\n{payload}\n</partials>"
                ),
            }
        ],
        output_format=Minutes,
    )
    return resp.parsed_output


def extract(transcript: str, title: str | None = None, date: str | None = None) -> Minutes:
    """녹취록에서 회의록을 추출한다. 길면 분할 추출 후 병합."""
    tokens = count_input_tokens(transcript, title, date)
    print(f"[extract] 입력 {tokens:,} 토큰 (상한 {CFG.max_input_tokens:,})")

    if tokens <= CFG.max_input_tokens:
        return _extract_once(transcript, title, date)

    # 줄 길이가 불균등하면 조각의 문자 수가 평균을 넘는다. 상한에 딱 맞춰 나누면
    # 그 편차만큼 초과할 수 있으므로 15% 여유를 두고 조각 수를 정한다.
    SAFETY = 0.85
    budget = max(1, int(CFG.max_input_tokens * SAFETY))
    parts = -(-tokens // budget)  # ceil
    chunks = _split_transcript(transcript, parts)
    print(f"[extract] 길어서 {len(chunks)}개로 분할 추출 (조각당 목표 {budget:,} 토큰)")

    partials: List[Minutes] = []
    for i, chunk in enumerate(chunks, 1):
        note = f"이것은 회의 전체 중 {i}/{len(chunks)} 구간이다. 이 구간에 있는 내용만 추출하라."
        print(f"[extract] {i}/{len(chunks)} ...")
        partials.append(_extract_once(chunk, title, date, note))

    print("[extract] 병합 중")
    return _merge(partials, title, date)


def load_transcript(path: Path) -> str:
    return path.read_text(encoding="utf-8")
