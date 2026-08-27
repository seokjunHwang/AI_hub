"""멈춤 게이트.

회의에서 «나온 말» 과 «결정» 은 다르다. 추출 결과를 바로 문서로 만들지 않고
번호를 붙여 제시하고 멈춘다. 사람이 고른 것만 확정본에 들어간다.

라벨 규칙
  D1 D2 ...  결정사항 (decisions)
  A1 A2 ...  액션아이템 (action_items)
  Q1 Q2 ...  미결 사항 (open_questions)
"""

from __future__ import annotations

import re
from typing import List, Tuple

from .schema import Minutes


def labels(m: Minutes) -> List[str]:
    return (
        [f"D{i}" for i in range(1, len(m.decisions) + 1)]
        + [f"A{i}" for i in range(1, len(m.action_items) + 1)]
        + [f"Q{i}" for i in range(1, len(m.open_questions) + 1)]
    )


ORPHAN = "논의 연결 없음"


def orphan_items(m: Minutes) -> List[Tuple[str, str]]:
    """논의 내용(topics) 중 어디에도 연결되지 않은 항목. (라벨, 내용) 목록.

    항목이 논의에 없으면 읽는 사람은 근거를 되짚을 수 없다. 그런 항목은
    «논의가 빠졌거나» «애초에 잡담이라 항목이 아니었거나» 둘 중 하나다.
    조용히 넘기면 표에만 있고 본문에는 없는 항목이 남는다 — 표 전체가 의심받는다.
    """
    known = set(m.topic_titles)
    out: List[Tuple[str, str]] = []
    for kind, items, text in (
        ("D", m.decisions, lambda x: x.decision),
        ("A", m.action_items, lambda x: x.task),
        ("Q", m.open_questions, lambda x: x.question),
    ):
        for i, it in enumerate(items, 1):
            if not it.topic or it.topic not in known:
                out.append((f"{kind}{i}", text(it)))
    return out


def render_review(m: Minutes) -> str:
    """사람이 고를 목록. 번호 · 내용 · 빈칸 이유를 한 줄에 보여준다."""
    out: List[str] = []
    out.append("=" * 72)
    out.append(f"  {m.title}   {m.date or ''}")
    out.append(f"  {m.one_liner}")
    out.append("=" * 72)

    if m.decisions:
        out.append("\n[결정사항]")
        for i, d in enumerate(m.decisions, 1):
            out.append(f"  D{i}. {d.decision}")
            out.append(f"      논의: {_topic_of(m, d)}")
            out.append(f'      근거: "{d.quote[:60]}"{" ·" + d.timestamp if d.timestamp else ""}')

    if m.action_items:
        out.append("\n[액션아이템]")
        for i, a in enumerate(m.action_items, 1):
            owner = a.owner or f"<{a.owner_display}>"
            due = a.due or f"<{a.due_display}>"
            out.append(f"  A{i}. {a.task}")
            out.append(f"      논의: {_topic_of(m, a)}")
            out.append(f"      담당 {owner} / 마감 {due} / {a.priority}")

    if m.open_questions:
        out.append("\n[미결 사항]")
        for i, q in enumerate(m.open_questions, 1):
            mark = "[블로커] " if q.blocker else ""
            out.append(f"  Q{i}. {mark}{q.question}")
            out.append(f"      논의: {_topic_of(m, q)}")

    if m.unclear_notes:
        out.append("\n[녹취 불확실 — 확정 전 확인 필요]")
        for u in m.unclear_notes:
            out.append(f"  · {u}")

    orphans = orphan_items(m)
    if orphans:
        out.append(f"\n[{ORPHAN} — 논의 내용에서 근거를 되짚을 수 없는 항목]")
        for lab, text in orphans:
            out.append(f"  {lab}. {text}")
        out.append("  → 논의가 빠진 것인지, 잡담이라 항목이 아닌 것인지 보고 고르세요.")

    out.append("\n" + "-" * 72)
    out.append("반영할 항목을 고르세요. 회의에서 나온 말이 전부 결정은 아닙니다.")
    out.append("  python -m src.pipeline --accept D1,D2,A1,A3  --draft <draft.json>")
    out.append("  python -m src.pipeline --accept all          --draft <draft.json>")
    out.append("-" * 72)
    return "\n".join(out)


def parse_accept(spec: str, m: Minutes) -> Tuple[List[str], List[str]]:
    """'D1,A3' 또는 'all' 을 라벨 목록으로. (선택, 알 수 없는 라벨) 반환."""
    valid = labels(m)
    if spec.strip().lower() == "all":
        return valid, []
    picked, unknown = [], []
    for raw in re.split(r"[,\s]+", spec.strip()):
        if not raw:
            continue
        lab = raw.upper()
        (picked if lab in valid else unknown).append(lab)
    # 중복 제거, 원래 순서 유지
    seen, ordered = set(), []
    for lab in valid:
        if lab in picked and lab not in seen:
            seen.add(lab)
            ordered.append(lab)
    return ordered, unknown


def apply_selection(m: Minutes, picked: List[str]) -> Minutes:
    """고른 항목만 남긴 새 Minutes. topics 와 unclear_notes 는 문맥이라 유지한다."""
    keep = set(picked)
    return m.model_copy(
        update={
            "decisions": [d for i, d in enumerate(m.decisions, 1) if f"D{i}" in keep],
            "action_items": [a for i, a in enumerate(m.action_items, 1) if f"A{i}" in keep],
            "open_questions": [
                q for i, q in enumerate(m.open_questions, 1) if f"Q{i}" in keep
            ],
        }
    )


def blank_report(m: Minutes) -> dict:
    """빈칸을 이유별로 집계. 미확인과 미조사를 섞지 않는다."""
    not_stated = sum(
        1 for a in m.action_items if a.owner_status == "not_stated" or a.due_status == "not_stated"
    )
    unclear = sum(
        1 for a in m.action_items if a.owner_status == "unclear" or a.due_status == "unclear"
    )
    return {
        "not_stated": not_stated,
        "unclear": unclear,
        "notes": len(m.unclear_notes),
        "orphans": len(orphan_items(m)),
    }


def _topic_of(m: Minutes, item) -> str:
    """항목에 붙은 논의 주제. 연결이 끊겼으면 그렇다고 말한다."""
    if item.topic and item.topic in set(m.topic_titles):
        return item.topic
    return f"<{ORPHAN}>" + (f" (topic={item.topic!r})" if item.topic else "")


def review_items(m: Minutes) -> List[Tuple[str, str, str]]:
    """GUI 체크박스용. (라벨, 본문, 부가정보) 목록.

    render_review() 는 «사람이 읽는 글», 이 함수는 «UI 가 그리는 데이터» 다.
    라벨 규칙을 GUI 가 따로 계산하면 두 곳이 어긋나므로 여기서만 만든다.
    """
    out: List[Tuple[str, str, str]] = []
    orphans = {lab for lab, _ in orphan_items(m)}

    def note(lab: str, extra: str = "") -> str:
        bits = [extra] if extra else []
        if lab in orphans:
            bits.append(ORPHAN)
        return " · ".join(bits)

    for i, d in enumerate(m.decisions, 1):
        lab = f"D{i}"
        out.append((lab, d.decision, note(lab, d.rationale and f"왜: {d.rationale}" or "")))
    for i, a in enumerate(m.action_items, 1):
        lab = f"A{i}"
        out.append((lab, a.task,
                    note(lab, f"담당 {a.owner_display} / 마감 {a.due_display}")))
    for i, q in enumerate(m.open_questions, 1):
        lab = f"Q{i}"
        out.append((lab, q.question, note(lab, "블로커" if q.blocker else "")))
    return out
