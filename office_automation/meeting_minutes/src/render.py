"""구조화 회의록 -> Markdown / HTML / JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .config import CFG
from .schema import MinutesBundle


@dataclass
class Rendered:
    md: Path
    html: Path
    json: Path
    slug: str
    dir: Path


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(CFG.template_dir)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def slugify(title: str, date: str | None) -> str:
    s = re.sub(r"[^\w가-힣\s-]", "", title).strip()
    s = re.sub(r"\s+", "_", s)[:60] or "회의록"
    return f"{date}_{s}" if date else s


# «회의/미팅/정기» 같은 말은 어느 회의록에나 붙어서 폴더 이름으로는 아무것도
# 구분해주지 않는다. 짧은 폴더 이름일수록 남는 한 단어가 내용어여야 한다.
_GENERIC = (
    "회의록", "회의", "미팅", "논의", "세미나", "워크숍", "워크샵",
    "정기", "주간", "월간", "분기", "리뷰", "공유", "보고", "결과", "관련",
)


def short_title(title: str, limit: int = 6) -> str:
    """폴더 이름용 한 단어 제목. 기본 6글자 이내."""
    s = re.sub(r"\d{4}[-.\s]?\d{1,2}[-.\s]?\d{1,2}", " ", title or "")
    s = re.sub(r"[^\w가-힣\s]", " ", s)
    words = [w for w in s.split() if w]
    core = [w for w in words if w not in _GENERIC] or words
    # 「AI 파이프라인 구조」의 «AI» 처럼 두 글자 이하 머리말은 폴더 이름으로 쓰면
    # 회의끼리 구분이 안 된다. 뒤에 더 긴 내용어가 있으면 그쪽을 쓴다.
    name = next((w for w in core if len(w) >= 3), core[0] if core else "")
    for g in _GENERIC:  # 「킥오프회의」처럼 붙어 있는 접미어도 떼어낸다
        if len(name) > len(g) and name.endswith(g):
            name = name[: -len(g)]
            break
    return name[:limit] or "회의록"


def _date_dir(bundle: MinutesBundle) -> str:
    """날짜 폴더 이름. 회의 날짜 > 생성 시각 > 오늘 순으로 고른다."""
    for cand in (bundle.minutes.date, bundle.generated_at):
        if cand and re.match(r"\d{4}-\d{2}-\d{2}", cand):
            return cand[:10]
    return _date.today().isoformat()


def _free_slug(out_dir: Path, slug: str) -> str:
    """같은 slug 가 이미 있으면 조용히 덮어쓰지 않고 _v2, _v3 로 넘긴다.

    덱 교훈(spec 버전 번호 충돌): 이미 쓰인 번호를 추정으로 다시 쓰면 남의 작업이 사라진다.
    """
    if not (out_dir / f"{slug}.md").exists():
        return slug
    n = 2
    while (out_dir / f"{slug}_v{n}.md").exists():
        n += 1
    new = f"{slug}_v{n}"
    print(f"[render] 같은 이름이 이미 있어 {new} 로 저장합니다 (덮어쓰지 않음)")
    return new


def render(
    bundle: MinutesBundle, out_dir: Path | None = None, overwrite: bool | None = None
) -> Rendered:
    """회의록을 md/html/json 으로 저장한다.

    폴더 구조와 덮어쓰기 여부는 `.env` 가 정한다 (OUTPUT_LAYOUT / OUTPUT_OVERWRITE).
    인자를 주면 그게 우선한다 — 테스트·일회성 호출용.
    """
    base = out_dir or CFG.output_dir
    if overwrite is None:
        overwrite = CFG.output_overwrite

    m = bundle.minutes

    #  OUTPUT_LAYOUT
    #    flat    <base>/                     한 폴더에 모두
    #    date    <base>/2026-08-25/          날짜별
    #    nested  <base>/2026-08-25/킥오프/    날짜+제목별 (기본)
    layout = CFG.output_layout.lower()
    if layout == "flat":
        out_dir = base
    elif layout == "date":
        out_dir = base / _date_dir(bundle)
    else:
        out_dir = base / _date_dir(bundle) / short_title(m.title)
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify(m.title, m.date)
    if not overwrite:
        slug = _free_slug(out_dir, slug)
    env = _env()

    md_path = out_dir / f"{slug}.md"
    md_path.write_text(
        env.get_template("minutes.md.j2").render(m=m, b=bundle), encoding="utf-8"
    )

    html_path = out_dir / f"{slug}.html"
    html_path.write_text(
        env.get_template("viewer.html.j2").render(m=m, b=bundle), encoding="utf-8"
    )

    json_path = out_dir / f"{slug}.json"
    json_path.write_text(
        json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"[render] {out_dir} 에 "
        f"{md_path.name} / {html_path.name} / {json_path.name}"
    )
    return Rendered(md=md_path, html=html_path, json=json_path, slug=slug, dir=out_dir)
