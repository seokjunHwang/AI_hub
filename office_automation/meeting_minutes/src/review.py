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
            out.append(f'      근거: "{d.quote[:60]}"{" ·" + d.timestamp if d.timestamp else ""}')

    if m.action_items:
        out.append("\n[액션아이템]")
        for i, a in enumerate(m.action_items, 1):
            owner = a.owner or f"<{a.owner_display}>"
            due = a.due or f"<{a.due_display}>"
            out.append(f"  A{i}. {a.task}")
            out.append(f"      담당 {owner} / 마감 {due} / {a.priority}")

    if m.open_questions:
        out.append("\n[미결 사항]")
        for i, q in enumerate(m.open_questions, 1):
            mark = "[블로커] " if q.blocker else ""
            out.append(f"  Q{i}. {mark}{q.question}")

    if m.unclear_notes:
        out.append("\n[녹취 불확실 — 확정 전 확인 필요]")
        for u in m.unclear_notes:
            out.append(f"  · {u}")

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
    return {"not_stated": not_stated, "unclear": unclear, "notes": len(m.unclear_notes)}
