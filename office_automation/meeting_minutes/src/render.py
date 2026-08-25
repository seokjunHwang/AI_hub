"""구조화 회의록 -> Markdown / HTML / JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
    bundle: MinutesBundle, out_dir: Path | None = None, overwrite: bool = False
) -> Rendered:
    out_dir = out_dir or CFG.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    m = bundle.minutes
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

    print(f"[render] {md_path.name} / {html_path.name} / {json_path.name}")
    return Rendered(md=md_path, html=html_path, json=json_path, slug=slug)
