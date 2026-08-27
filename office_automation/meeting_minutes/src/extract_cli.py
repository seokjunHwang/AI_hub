"""추출 (CLI 방식) — `claude -p` 로 녹취록을 Minutes 구조로 만든다.

API 키를 쓰지 않는다. Claude Code 구독 계정으로 돈다.

노트북 셀은 `extract()` 한 줄만 부른다. 로직이 셀에 있으면
  · 셀이 길어져 «무엇을 하는 셀인지» 가 안 보이고
  · pipeline.py 와 갈라진다 (실제로 갈라졌다)
프롬프트도 prompts/ 로 뺐다 — 품질 조정은 코드가 아니라 그 파일에서 한다.

세 번 실패하고 나온 설계
  1. Claude 가 JSON 파일을 저장   -> 헤드리스에서 쓰기 권한 막힘
  2. 사용자 프롬프트로 «JSON만»   -> 사람용 리포트를 출력 (에이전트라서)
  3. --append-system-prompt       -> 성공. 형식 강제는 시스템 층에서 해야 한다
그리고 도중에 «사건»(권한 거부 등)이 생기면 또 산문으로 보고하므로,
시스템 프롬프트에 «무슨 일이 있어도 JSON 만» 을 명시하고 권한을 미리 준다.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .config import CFG
from .schema import Minutes, MinutesBundle

_PUNCT = re.compile(r"[\s,.!?~…·\"'“”‘’()\[\]]+")
_TS = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*")


# ─────────────────────────────────────────────────────────────────────────────
#  결과
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class QuoteCheck:
    exact: List[Tuple[str, str, float]] = field(default_factory=list)
    tidied: List[Tuple[str, str, float]] = field(default_factory=list)
    missing: List[Tuple[str, str, float]] = field(default_factory=list)

    def report(self) -> str:
        out = [
            f"인용 검증: 원문 그대로 {len(self.exact)} / "
            f"다듬어짐 {len(self.tidied)} / 불일치 {len(self.missing)}"
        ]
        for label, q, r in self.tidied:
            out.append(f"  [다듬]   {label} ({r:.0%}) {(q or '')[:55]}")
        for label, q, r in self.missing:
            out.append(f"  [불일치] {label} ({r:.0%}) {(q or '(빈 인용)')[:55]}")
        return "\n".join(out)


@dataclass
class ExtractResult:
    """실패해도 예외를 던지지 않는다. ok 로 판정한다."""

    ok: bool
    bundle: Optional[MinutesBundle] = None
    draft_path: Optional[Path] = None
    quotes: QuoteCheck = field(default_factory=QuoteCheck)
    attempts: int = 0
    raw_dump: Optional[Path] = None
    raw_head: str = ""
    fixes: List[str] = field(default_factory=list)

    def report(self) -> str:
        if not self.ok:
            return "\n".join([
                f"추출 실패 (시도 {self.attempts}회). 다음 셀로 넘어가지 마세요.",
                f"  받은 응답 전문: {self.raw_dump}",
                f"  앞부분: {self.raw_head[:300]}",
                "",
                "  자주 있는 원인",
                "   · Claude 가 도중에 «권한 거부» 같은 사건을 만나 그걸 설명하려 함",
                "   · data/transcripts 에 비슷한 파일이 여러 개라 되물으려 함",
                "   · 녹취록이 비었거나 경로가 틀림",
                "",
                "  해볼 것: .env 의 INPUT_TRANSCRIPT 확인 -> 이 셀 재실행",
            ])
        m = self.bundle.minutes
        lines = list(self.fixes)
        lines.append(self.quotes.report())
        lines.append(
            f"결정 {len(m.decisions)} · 액션 {len(m.action_items)} "
            f"· 미결 {len(m.open_questions)} · 확인필요 {len(m.unclear_notes)}"
        )
        lines.append(f"저장: {self.draft_path}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  인용 검증 — 막지 않고 «분류» 한다
# ─────────────────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return _PUNCT.sub("", s or "")


def _units(transcript: str) -> List[str]:
    """비교 단위: 정규화한 줄 + 연속 2·3줄 묶음 (여러 줄에 걸친 인용 대응)."""
    raw = [_TS.sub("", l).strip() for l in transcript.splitlines() if l.strip()]
    lines = [_norm(l) for l in raw if _norm(l)]
    out = list(lines)
    for n in (2, 3):
        out += ["".join(lines[i:i + n]) for i in range(len(lines) - n + 1)]
    return out


def _score(q: str, units: List[str]) -> float:
    """가장 닮은 단위의 점수 (0~1).

    «앞에서 몇 글자 맞나» 로 재면 조사 하나 바뀌어도 급락한다
    (그놈으로 -> 그놈이라 = 38%). ratio 와 최장블록 커버리지의 큰 값을 쓴다.
    """
    if not q:
        return 0.0
    best = 0.0
    for u in units:
        if q in u:
            return 1.0
        sm = difflib.SequenceMatcher(None, q, u, autojunk=False)
        if sm.quick_ratio() < best:
            continue
        blk = sm.find_longest_match(0, len(q), 0, len(u))
        best = max(best, sm.ratio(), blk.size / len(q))
    return best


def check_quotes(minutes: Minutes, transcript: str) -> QuoteCheck:
    """멈추지 않는 이유: 다듬어진 인용은 «틀림» 이 아니라 «확인할 지점» 이다."""
    units = _units(transcript)
    res = QuoteCheck()
    for kind, items in (("D", minutes.decisions), ("A", minutes.action_items)):
        for n, it in enumerate(items, 1):
            r = _score(_norm(it.quote), units)
            row = (f"{kind}{n}", it.quote, r)
            (res.exact if r >= 0.99 else res.tidied if r >= 0.6 else res.missing).append(row)
    return res


# ─────────────────────────────────────────────────────────────────────────────
#  claude -p 호출
# ─────────────────────────────────────────────────────────────────────────────
def _prompt(transcript_path: Path, title: str, date: str, strict: bool) -> str:
    tpl = (CFG.prompt_dir / "extract_task.md").read_text(encoding="utf-8")
    text = tpl.format(
        transcript_path=transcript_path,
        title=title or "(미지정 - 내용에서 생성)",
        date=date or "(미지정)",
    )
    if strict:
        text += (
            "\n※ 직전 시도에서 JSON 이 아닌 설명문을 출력했다."
            "\n※ 이번에는 { 로 시작해 } 로 끝나는 JSON «만» 출력한다. 한 문장도 덧붙이지 않는다.\n"
        )
    return text


def _run(transcript_path: Path, title: str, date: str, strict: bool) -> str:
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("claude CLI 가 없습니다: npm i -g @anthropic-ai/claude-code")

    sys_prompt = (CFG.prompt_dir / "extract_output.md").read_text(encoding="utf-8")
    cmd = [
        claude, "-p", _prompt(transcript_path, title, date, strict),
        "--append-system-prompt", sys_prompt,
        #  권한 거부가 «사건» 이 되면 Claude 가 그걸 설명하려 하고 JSON 을 밀어낸다
        "--permission-mode", "acceptEdits",
    ]
    if CFG.cli_model:
        cmd += ["--model", CFG.cli_model]
    if CFG.cli_effort:
        cmd += ["--effort", CFG.cli_effort]

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(CFG.prompt_dir.parent), timeout=CFG.api_timeout * 4,
    )
    return ((r.stdout or "") + (r.stderr or "")).strip()


def _find_json(text: str) -> Optional[str]:
    """코드펜스나 앞뒤 설명이 붙어도 JSON 본문만 꺼낸다."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return m.group(1)
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if (i != -1 and j > i) else None


# ─────────────────────────────────────────────────────────────────────────────
#  공개 함수
# ─────────────────────────────────────────────────────────────────────────────
def extract(
    transcript_path: Path,
    transcript: str,
    source_name: str,
    title: str = "",
    date: str = "",
    tries: int = 2,
    on_log=print,
) -> ExtractResult:
    """녹취록 -> Minutes. 실패해도 예외를 던지지 않고 ok=False 로 돌려준다."""
    minutes: Optional[Minutes] = None
    raw = ""
    used = 0

    for attempt in range(1, tries + 1):
        used = attempt
        on_log(f"추출 중... (시도 {attempt}/{tries}, 녹취록 길이에 따라 몇 분)")
        raw = _run(transcript_path, title, date, strict=(attempt > 1))
        payload = _find_json(raw)
        if payload:
            try:
                data = json.loads(payload)
                minutes = Minutes.model_validate(data.get("minutes", data))
                break
            except Exception as e:                      # noqa: BLE001
                on_log(f"  JSON 을 찾았지만 스키마 검증 실패: {str(e)[:200]}")
        else:
            on_log("  JSON 형식이 아닌 응답을 받았습니다.")
        if attempt < tries:
            on_log("  더 강한 지시로 다시 시도합니다.")

    draft_dir = CFG.output_dir / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)

    if minutes is None:
        dump = draft_dir / "last_extract_raw.txt"
        dump.write_text(raw, encoding="utf-8")
        return ExtractResult(ok=False, attempts=used, raw_dump=dump, raw_head=raw)

    #  우리가 아는 값은 모델에게 맡기지 않는다 (제목 변경·날짜 누락을 실제로 확인).
    fixes, upd = [], {}
    if title and minutes.title != title:
        fixes.append(f"제목 보정: {minutes.title!r} -> {title!r}")
        upd["title"] = title
    if date and minutes.date != date:
        fixes.append(f"날짜 보정: {minutes.date!r} -> {date!r}")
        upd["date"] = date
    if upd:
        minutes = minutes.model_copy(update=upd)

    quotes = check_quotes(minutes, transcript)

    #  검증 결과를 회의록에 남긴다. 확정 전에 사람이 볼 수 있어야 한다.
    notes = list(minutes.unclear_notes)
    for label, _q, r in quotes.tidied:
        notes.append(f"[{label}] 인용이 다듬어짐 ({r:.0%} 유사) — 원문 확인 권장")
    for label, _q, r in quotes.missing:
        notes.append(f"[{label}] 녹취록에서 근거를 찾지 못함 ({r:.0%}) — 반드시 확인")
    if len(notes) != len(minutes.unclear_notes):
        minutes = minutes.model_copy(update={"unclear_notes": notes})

    bundle = MinutesBundle(
        minutes=minutes,
        source_audio=source_name,
        transcript_chars=len(transcript),
        model=f"claude-code({CFG.cli_model or 'default'}/{CFG.cli_effort or 'default'})",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    stem = ((date + "_") if date else "") + (title or transcript_path.stem)
    draft = draft_dir / f"{stem}.cli.json"
    draft.write_text(
        json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return ExtractResult(ok=True, bundle=bundle, draft_path=draft, quotes=quotes,
                         attempts=used, fixes=fixes)
