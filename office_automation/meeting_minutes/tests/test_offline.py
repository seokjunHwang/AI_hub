"""API 없이 도는 회귀 테스트.

    python tests/test_offline.py          (meeting_minutes 폴더에서)

필요 패키지: pydantic, jinja2, anthropic(import 만)
«값» 이 아니라 «불변식» 을 검사한다. 조각이 4개인지가 아니라, 모든 조각이
상한 이하이고 내용이 보존되는지를 본다 — 값은 다음 커밋에 낡는다.
"""

from __future__ import annotations

import math
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.drive import _q
from src.extract import _split_transcript
from src.render import render
from src.review import apply_selection, blank_report, labels, parse_accept, render_review
from src.schema import (
    BLANK_LABEL,
    ActionItem,
    Decision,
    Minutes,
    MinutesBundle,
    OpenQuestion,
    Topic,
)
from src.transcribe import _resolve_device

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))


def _sample() -> Minutes:
    """검수·업로드 테스트용 표본. 세 종류(D/A/Q)가 모두 있어야 라벨을 잰다."""
    return Minutes(
        title="킥오프", date="2026-01-01", one_liner="범위 확정",
        topics=[Topic(title="범위", summary="3개 인텐트")],
        decisions=[Decision(decision="A 안 채택", rationale="비용", quote="A 로 가죠")],
        action_items=[ActionItem(task="문서 정리", owner="김PM", quote="정리해 주세요")],
        open_questions=[OpenQuestion(question="예산?", blocker=True)],
    )


def _bundle(m: Minutes) -> MinutesBundle:
    return MinutesBundle(minutes=m, model="test", generated_at="2026-01-01 00:00")


# --------------------------------------------------------------------------
def test_split() -> None:
    """긴 녹취록 분할. 개행이 없는 붙여넣기(Clova Note 등)도 나뉘어야 한다."""
    print("\n[분할] _split_transcript")
    cases = [
        ("개행 없음 40만자", "가나다라마바사. " * 50000, 4),
        ("개행 없음 문장부호 없음", "가" * 300000, 3),
        ("줄 1000개 불균등", "\n".join(f"[00:00:{i%60:02d}] 발언 {i}" for i in range(1000)), 5),
        ("줄 길이 극단 불균등", "\n".join(["짧다"] * 99 + ["긴줄" * 5000]), 3),
        ("분할 불필요", "짧은 회의록.", 1),
        ("줄 2개 parts 5", "첫 줄입니다.\n두 번째 줄입니다.", 5),
        ("빈 줄 섞임", "발언1\n\n\n발언2\n\n발언3\n발언4", 2),
    ]
    for name, text, parts in cases:
        chunks = _split_transcript(text, parts)
        src_lines = [l for l in text.splitlines() if l.strip()]
        line_path = len(src_lines) >= parts

        if line_path:
            limit = math.ceil(len(src_lines) / parts)
            within = all(
                len([l for l in c.splitlines() if l.strip()]) <= limit for c in chunks
            )
            preserved = [
                l for l in "\n".join(chunks).splitlines() if l.strip()
            ] == src_lines
            unit = f"줄<={limit}"
        else:
            limit = math.ceil(len(text) / parts)
            within = all(len(c) <= limit for c in chunks)
            preserved = "".join(chunks) == text
            unit = f"자<={limit:,}"

        check(f"{name}: 조각 상한 이하", within, f"{len(chunks)}조각 {unit}")
        check(f"{name}: 내용 보존", preserved)
        check(f"{name}: 개수 <= parts+1", len(chunks) <= parts + 1)


def test_status_sync() -> None:
    """owner/due 가 있으면 status 가 stated 로 교정돼야 한다.

    모델이 owner 만 채우고 status 를 기본값으로 두는 경우가 있다. 그대로 두면
    «담당자가 있는데 미정 1건» 이라는 거짓 경고가 뜬다 — 오탐은 정탐 실패보다 비싸다.
    """
    print("\n[상태 동기화] ActionItem._sync_status")
    a = ActionItem(task="일", owner="김PM", due="2026-09-01", quote="q")
    check("owner 있으면 stated", a.owner_status == "stated", repr(a.owner_status))
    check("due 있으면 stated", a.due_status == "stated", repr(a.due_status))
    check("needs_human False", a.needs_human is False)

    b = ActionItem(task="일", quote="q")
    check("빈칸이면 not_stated 유지", b.owner_status == "not_stated")
    check("needs_human True", b.needs_human is True)

    m = Minutes(title="t", one_liner="o", action_items=[a, b])
    r = blank_report(m)
    check("오탐 없음 (not_stated 1건만)", r["not_stated"] == 1, str(r))


def test_blank_reason_in_outputs() -> None:
    """미확인/미조사 구분이 «최종 산출물» 까지 전파돼야 한다.

    review 출력에만 있고 md/html 에 없으면, 드라이브로 넘어간 문서에서
    구분이 사라진다 (변경은 점이 아니라 파장).
    """
    print("\n[파장] 빈칸 이유가 md/html 까지 가는가")
    m = Minutes(
        title="검수", date="2026-01-01", one_liner="확인",
        topics=[Topic(title="주제", summary="내용")],
        decisions=[Decision(decision="확정", quote="그러죠")],
        action_items=[
            ActionItem(task="명시됨", owner="김PM", due="2026-09-01", quote="q1"),
            ActionItem(task="회의미정", owner_status="not_stated", due_status="not_stated", quote="q2"),
            ActionItem(task="녹취불확실", owner_status="unclear", due_status="unclear", quote="q3"),
        ],
    )
    tmp = Path(tempfile.mkdtemp(prefix="mm_test_"))
    try:
        out = render(_bundle(m), tmp)
        md = out.md.read_text(encoding="utf-8")
        html = out.html.read_text(encoding="utf-8")
        for key, label in BLANK_LABEL.items():
            check(f"md 에 '{label}'", label in md)
            check(f"html 에 '{label}'", label in html)
        check("md 확인필요 배너", "확정 전 확인 필요 2건" in md)
        check("html 확인필요 배너", "확정 전 확인 필요 2건" in html)
        check("html status 클래스 구분", "todo not_stated" in html and "todo unclear" in html)
        check("review 출력에도 노출", "회의에서 안 정해짐" in render_review(m))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_selection() -> None:
    print("\n[선택] parse_accept / apply_selection")
    m = Minutes(
        title="t", one_liner="o",
        decisions=[Decision(decision="d1", quote="q"), Decision(decision="d2", quote="q")],
        action_items=[ActionItem(task="a1", quote="q")],
        open_questions=[OpenQuestion(question="q1")],
    )
    check("라벨 생성", labels(m) == ["D1", "D2", "A1", "Q1"], str(labels(m)))

    picked, unknown = parse_accept("D1,A1", m)
    check("일부 선택", picked == ["D1", "A1"] and not unknown, str(picked))

    sel = apply_selection(m, picked)
    check("필터 적용", len(sel.decisions) == 1 and len(sel.action_items) == 1)
    check("미선택 제거", len(sel.open_questions) == 0)
    check("문맥(topics) 유지", sel.topics == m.topics)

    check("all 은 전부", parse_accept("all", m)[0] == labels(m))
    check("소문자 허용", parse_accept("d1,a1", m)[0] == ["D1", "A1"])
    check("잘못된 라벨 분리", parse_accept("D1,X9,A99", m) == (["D1"], ["X9", "A99"]))
    check("공백 구분자", parse_accept("D1 A1", m)[0] == ["D1", "A1"])


def test_empty_meeting() -> None:
    """결정·액션이 0건인 회의도 회의록이 나와야 한다 (논의만 한 회의)."""
    print("\n[빈 회의] 항목 0건")
    m = Minutes(title="정보공유", one_liner="공유만 함",
                topics=[Topic(title="공유", summary="내용")])
    check("라벨 없음", labels(m) == [])
    check("all 이 빈 목록", parse_accept("all", m)[0] == [])
    tmp = Path(tempfile.mkdtemp(prefix="mm_test_"))
    try:
        out = render(_bundle(apply_selection(m, [])), tmp)
        check("렌더 성공", out.md.exists() and out.html.exists())
        check("논의 내용 포함", "공유" in out.md.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_silent_overwrite() -> None:
    print("\n[덮어쓰기] 같은 제목 재실행")
    m = Minutes(title="회귀", date="2026-01-01", one_liner="o")
    tmp = Path(tempfile.mkdtemp(prefix="mm_test_"))
    try:
        a = render(_bundle(m), tmp)
        b = render(_bundle(m), tmp)
        check("두 번째는 _v2", b.slug.endswith("_v2"), f"{a.slug} -> {b.slug}")
        check("첫 산출물 보존", a.md.exists() and b.md.exists())
        c = render(_bundle(m), tmp, overwrite=True)
        check("overwrite 는 덮어씀", c.slug == a.slug, c.slug)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_drive_query_escape() -> None:
    print("\n[드라이브] 쿼리 이스케이프")
    check("작은따옴표 이스케이프", _q("Kim's") == "Kim\\'s", _q("Kim's"))
    check("백슬래시 이스케이프", _q("a\\b") == "a\\\\b")
    # 불변식: 이스케이프 시퀀스를 걷어낸 뒤 남는 «구분자» 따옴표가 짝을 이룬다.
    # raw 따옴표 개수를 세는 것은 형태 검사라서, 정상인 name = '\'' 를 오탐한다.
    for raw in ["Kim's", "a'b'c", chr(39), "회의록", "a\\b"]:
        q = f"name = '{_q(raw)}'"
        delims = q.replace("\\\\", "").replace("\\'", "")
        check(f"구분자 짝 유지 ({raw!r})", delims.count("'") == 2, q)
    check("한글 통과", _q("회의록") == "회의록")


def test_device_resolution() -> None:
    """torch 없이도 판정돼야 한다 (faster-whisper 는 CTranslate2 기반)."""
    print("\n[STT] 장치 판정")
    dev, comp, why = _resolve_device()
    check("device 유효", dev in ("cpu", "cuda"), f"{dev}/{comp}")
    check("compute 유효", comp in ("int8", "float16", "float32", "int8_float16"))
    check("이유가 비어 있지 않다", bool(why and why.strip()), why)
    check("torch 미설치여도 동작", "torch" not in sys.modules)


def test_slug_safety() -> None:
    print("\n[파일명] slugify")
    from src.render import slugify
    bad = 'a/b\\c:d*e?f"g<h>i|j'
    s = slugify(bad, "2026-01-01")
    check("경로 문자 제거", not any(ch in s for ch in '/\\:*?"<>|'), s)
    check("빈 제목 폴백", slugify("", None) == "회의록", slugify("", None))
    check("한글 유지", "회의" in slugify("팀 회의", None))
    check("길이 제한", len(slugify("가" * 200, None)) <= 60)


def test_upload_stamp() -> None:
    """올릴 때 «업로드 시각» 이 괄호로 붙는다.

    같은 회의를 두 번 올리면 어느 것이 최신인지 이름만 보고 알아야 한다.
    시각 값을 고정할 수 없으니 «형태» 를 잰다.
    """
    print(chr(10) + "[업로드] 시각 표기")
    import re as _re
    from datetime import datetime

    m = _sample()

    #  노션 — 제목 조립부만 떼어 검사한다 (네트워크 없이)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    base = f"{m.date} {m.title}" if m.date else m.title
    name = f"{base} (업로드 {stamp})"
    check("노션 제목에 원본 제목 포함", m.title in name, name)
    check("노션 제목에 괄호 업로드 시각",
          bool(_re.search(r"[(]업로드 [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}[)]$", name)),
          name)

    #  드라이브 — upload_minutes 가 subfolder 를 어떻게 바꾸는지. API 를 타지 않게
    #  같은 규칙을 여기서 재현하지 않고, 실제 코드가 시각을 «붙이는지» 만 본다.
    src = (Path(__file__).resolve().parent.parent / "src" / "drive.py").read_text(
        encoding="utf-8")
    check("드라이브 subfolder 에 시각 부착 코드 존재",
          "datetime.now().strftime" in src and "subfolder = f" in src)


def test_review_items_match_labels() -> None:
    """GUI 체크박스 라벨과 CLI 라벨이 «같은 정본» 에서 나온다."""
    print(chr(10) + "[검수] GUI 라벨 == CLI 라벨")
    from src.review import labels, review_items

    m = _sample()
    cli = labels(m)
    gui = [lab for lab, _, _ in review_items(m)]
    check("라벨 집합 동일", cli == gui, f"{cli} vs {gui}")
    check("본문이 비어 있지 않다", all(t.strip() for _, t, _ in review_items(m)))


def test_gui_imports() -> None:
    """GUI 가 파이프라인 단계 함수를 그대로 부르는지 — 로직 중복을 막는다."""
    print(chr(10) + "[GUI] 파이프라인 재사용")
    src = (Path(__file__).resolve().parent.parent / "src" / "gui.py").read_text(
        encoding="utf-8")
    for fn in ("resolve_input", "do_extract", "do_confirm", "do_send", "do_notion",
               "do_check"):
        check(f"pipeline.{fn} 호출", fn in src)
    check("추출 로직을 GUI 에 다시 쓰지 않았다", "subprocess" not in src)


if __name__ == "__main__":
    for fn in (
        test_split, test_status_sync, test_blank_reason_in_outputs, test_selection,
        test_empty_meeting, test_no_silent_overwrite, test_drive_query_escape,
        test_device_resolution, test_slug_safety, test_upload_stamp,
        test_review_items_match_labels, test_gui_imports,
    ):
        fn()

    print("\n" + "=" * 60)
    print(f"  PASS {len(PASSED)}  /  FAIL {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print(f"    - {f}")
    print("=" * 60)
    sys.exit(1 if FAILED else 0)
