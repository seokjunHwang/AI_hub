"""노션 업로드 (API 방식).

왜 API 인가
    MCP 커넥터는 «대화형 Claude 세션» 에서만 붙는다. 새벽 스케줄러처럼 사람이
    없는 실행에서는 권한 승인 창이 없어 도구 호출이 차단된다.
    토큰 방식은 사람 없이도 돌고, 1~2초면 끝난다.

우리가 유리한 점
    보통 노션 API 의 걸림돌은 «마크다운 -> 블록 변환» 이다. 우리는 Minutes
    구조 데이터가 있으므로 변환 없이 블록을 직접 만든다 — 액션은 체크박스로,
    결정은 인용과 함께. 마크다운을 거치는 것보다 정확하다.

준비물 두 가지 (하나만 있으면 실패한다)
    1. 토큰            notion.so -> 설정 -> 연결 -> 개발자 포털 -> 내부 통합
    2. 페이지에 연결    대상 페이지 -> ••• -> 연결 -> 그 integration 추가
                       노션은 integration 을 페이지에 «초대» 하는 구조다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import CFG
from .schema import Minutes, MinutesBundle

API = "https://api.notion.com/v1"
#  페이지 부모(page_id)만 쓰므로 2025-09-03 의 data_source 변경과 무관하다.
#  DB 부모로 바꿀 때는 이 버전과 parent 형태를 함께 손대야 한다.
NOTION_VERSION = "2022-06-28"

UUID_RE = re.compile(r"([0-9a-f]{32})", re.I)


@dataclass
class NotionResult:
    page_id: str
    url: str
    parent_title: str = ""
    account: str = ""
    blocks: int = 0

    def report(self) -> str:
        return "\n".join([
            f"계정   {self.account or '(확인 안 됨)'}",
            f"상위   {self.parent_title or '(확인 안 됨)'}",
            f"생성   {self.url}",
            f"블록   {self.blocks}개",
        ])


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────────────────────────
def _req(method: str, path: str, body: Optional[dict] = None) -> dict:
    if not CFG.notion_token:
        raise SystemExit(
            "NOTION_TOKEN 이 비어 있습니다.\n"
            "  .env 에 넣으세요. notion.so -> 설정 -> 연결 -> 개발자 포털 -> 내부 통합"
        )
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {CFG.notion_token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            msg = json.loads(raw).get("message", raw)
        except json.JSONDecodeError:
            msg = raw
        hint = ""
        if e.code == 401:
            hint = "\n  -> 토큰이 잘못됐습니다. .env 의 NOTION_TOKEN 확인"
        elif e.code == 404:
            hint = (
                "\n  -> 페이지를 못 찾았습니다. 토큰이 아니라 «연결» 문제일 수 있습니다."
                "\n     대상 페이지 -> ••• -> 연결 -> integration 추가"
            )
        raise SystemExit(f"노션 API {e.code}: {msg}{hint}") from None


def _page_id(target: str) -> str:
    """URL 이든 ID 든 32자리 ID 로 만든다."""
    m = UUID_RE.search((target or "").replace("-", ""))
    if not m:
        raise SystemExit(
            f"NOTION_TARGET 에서 페이지 ID 를 찾지 못했습니다: {target!r}\n"
            "  페이지 URL 을 넣으세요 (••• -> 링크 복사)"
        )
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────────────
#  확인
# ─────────────────────────────────────────────────────────────────────────────
def check() -> dict:
    """토큰·계정·대상 페이지 접근을 확인한다. 페이지를 만들지 않는다."""
    out: Dict[str, Any] = {"ok": False, "account": "", "parent_title": "", "error": ""}
    try:
        me = _req("GET", "/users/me")
        name = me.get("name") or ""
        email = (me.get("bot", {}).get("owner", {}).get("user", {}) or {}).get(
            "person", {}
        ).get("email", "")
        out["account"] = f"{name} {('<' + email + '>') if email else ''}".strip()

        pid = _page_id(CFG.notion_target)
        page = _req("GET", f"/pages/{pid}")
        title = ""
        for prop in (page.get("properties") or {}).values():
            if prop.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
                break
        out["parent_title"] = title or "(제목 없음)"
        out["ok"] = True
    except SystemExit as e:
        out["error"] = str(e)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  블록 만들기 — Minutes 구조를 그대로 노션 블록으로
# ─────────────────────────────────────────────────────────────────────────────
def _rt(text: str, code: bool = False, italic: bool = False) -> List[dict]:
    """rich_text. 노션은 한 블록당 2000자 제한이 있어 잘라 넣는다."""
    text = (text or "")[:1900]
    return [{
        "type": "text",
        "text": {"content": text},
        "annotations": {"code": code, "italic": italic},
    }]


def _p(text: str, italic: bool = False) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rt(text, italic=italic)}}


def _h(text: str, level: int = 2) -> dict:
    key = f"heading_{level}"
    return {"object": "block", "type": key, key: {"rich_text": _rt(text)}}


def _todo(text: str) -> dict:
    return {"object": "block", "type": "to_do",
            "to_do": {"rich_text": _rt(text), "checked": False}}


def _quote(text: str) -> dict:
    return {"object": "block", "type": "quote", "quote": {"rich_text": _rt(text)}}


def _bullet(text: str) -> dict:
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rt(text)}}


def _callout(text: str, emoji: str = "⚠️") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": _rt(text), "icon": {"type": "emoji", "emoji": emoji}}}


def blocks_from(bundle: MinutesBundle) -> List[dict]:
    """Minutes -> 노션 블록. 마크다운을 거치지 않는다."""
    m: Minutes = bundle.minutes
    b: List[dict] = []

    head = []
    if m.date:
        head.append(m.date)
    if m.participants:
        head.append("참석 " + ", ".join(m.participants))
    if head:
        b.append(_p(" · ".join(head), italic=True))
    b.append(_callout(m.one_liner, "🎯"))

    if m.action_items:
        b.append(_h(f"액션아이템 ({len(m.action_items)})"))
        for a in m.action_items:
            #  담당·마감은 «왜 비었는지» 를 함께 적는다. 미확인과 미조사는 다음 행동이 다르다.
            b.append(_todo(f"{a.task}  —  담당 {a.owner_display} / 마감 {a.due_display}"))
            if a.quote:
                ts = f" · {a.timestamp}" if a.timestamp else ""
                b.append(_quote(f"{a.quote}{ts}"))
        pending = [a for a in m.action_items if a.needs_human]
        if pending:
            b.append(_callout(
                f"확정 전 확인 필요 {len(pending)}건 — «회의에서 안 정해짐» 은 참석자에게, "
                "«녹취 불확실» 은 원본 오디오를 다시 확인하세요. 추측으로 채우지 마세요."
            ))

    if m.decisions:
        b.append(_h(f"결정사항 ({len(m.decisions)})"))
        for i, d in enumerate(m.decisions, 1):
            b.append(_h(f"{i}. {d.decision}", 3))
            if d.rationale:
                b.append(_bullet(f"왜: {d.rationale}"))
            if d.alternatives:
                b.append(_bullet("검토했으나 미채택: " + " / ".join(d.alternatives)))
            if d.quote:
                ts = f" · {d.timestamp}" if d.timestamp else ""
                b.append(_quote(f"{d.quote}{ts}"))

    if m.open_questions:
        b.append(_h("미결 사항"))
        for q in m.open_questions:
            mark = "[블로커] " if q.blocker else ""
            who = f" — 확인: {q.who_should_answer}" if q.who_should_answer else ""
            b.append(_bullet(f"{mark}{q.question}{who}"))

    if m.topics:
        b.append(_h("논의 내용"))
        for t in m.topics:
            ts = f"  ({t.timestamp_start})" if t.timestamp_start else ""
            b.append(_h(f"{t.title}{ts}", 3))
            b.append(_p(t.summary))

    if m.unclear_notes:
        b.append(_h("확인 필요"))
        for u in m.unclear_notes:
            b.append(_bullet(u))

    src = f"{bundle.model} 자동 생성 · {bundle.generated_at}"
    if bundle.source_audio:
        src += f" · 원본 {bundle.source_audio}"
    src += f" · 녹취 {bundle.transcript_chars}자"
    b.append(_p(src, italic=True))
    return b


# ─────────────────────────────────────────────────────────────────────────────
#  업로드
# ─────────────────────────────────────────────────────────────────────────────
def upload(bundle: MinutesBundle, title: Optional[str] = None) -> NotionResult:
    """대상 페이지 아래에 회의록 페이지를 만든다."""
    parent = _page_id(CFG.notion_target)
    m = bundle.minutes
    #  제목에 «업로드 시각» 을 괄호로 붙인다. 같은 회의를 여러 번 올릴 때
    #  노션에서 어느 것이 최신인지 제목만 보고 구분하기 위함이다.
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    base = title or (f"{m.date} {m.title}" if m.date else m.title)
    name = f"{base} (업로드 {stamp})"

    blocks = blocks_from(bundle)
    #  한 요청에 100 블록 제한. 나머지는 append 로 이어 붙인다.
    first, rest = blocks[:100], blocks[100:]

    page = _req("POST", "/pages", {
        "parent": {"type": "page_id", "page_id": parent},
        "icon": {"type": "emoji", "emoji": "📝"},
        "properties": {"title": {"title": _rt(name)}},
        "children": first,
    })
    pid = page["id"]

    while rest:
        chunk, rest = rest[:100], rest[100:]
        _req("PATCH", f"/blocks/{pid}/children", {"children": chunk})

    info = check()
    return NotionResult(
        page_id=pid,
        url=page.get("url", ""),
        parent_title=info.get("parent_title", ""),
        account=info.get("account", ""),
        blocks=len(blocks),
    )
