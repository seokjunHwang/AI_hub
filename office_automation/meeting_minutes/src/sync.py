"""구글 드라이브 «동기화 폴더» 복사.

Drive for desktop 이 이미 돌고 있으면 OAuth·API·credentials.json 이 전혀 필요 없다.
동기화 폴더에 파일을 놓으면 Drive 가 알아서 올린다.

drive.py(API 방식)와의 차이
                     sync.py (이 파일)        drive.py (API)
  설정               없음                     Google Cloud OAuth 필요
  공유 링크 획득      X (탐색기에서 수동)       O (webViewLink)
  Google Docs 변환    X                        O
  업로드 완료 확인    X (Drive 가 비동기 처리)  O
  오프라인            대기 후 자동 업로드       실패

마운트 문자 주의: Drive for desktop 의 가상 드라이브는 «대화형 사용자 세션» 에만
보인다. 스크립트를 다른 컨텍스트(서비스·스케줄러)에서 돌리면 G: 가 없을 수 있다.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .config import CFG


@dataclass
class Synced:
    src: Path
    dst: Path


def find_drive_roots() -> List[Path]:
    """Drive for desktop 마운트 후보를 찾는다. 없으면 빈 목록."""
    found: List[Path] = []
    for letter in "GHIJKLMNOPQRSTUVWXYZ":
        for name in ("내 드라이브", "My Drive"):
            p = Path(f"{letter}:/") / name
            try:
                if p.is_dir():
                    found.append(p)
            except OSError:
                pass
    # 폴더 마운트 방식도 확인
    for cand in (Path.home() / "My Drive", Path.home() / "내 드라이브"):
        if cand.is_dir():
            found.append(cand)
    return found


def resolve_target(subfolder: str | None = None) -> Path:
    """복사 대상 폴더를 정한다. SYNC_DIR 이 있으면 그걸 쓰고, 없으면 자동 탐색."""
    if CFG.sync_dir:
        base = Path(CFG.sync_dir)
        if not base.is_dir():
            raise SystemExit(
                f"SYNC_DIR 경로가 없습니다: {base}\n"
                "탐색기에서 Google Drive 폴더 경로를 확인해 .env 의 SYNC_DIR 에 넣으세요.\n"
                r'예: SYNC_DIR=G:\내 드라이브\회의록'
            )
    else:
        roots = find_drive_roots()
        if not roots:
            raise SystemExit(
                "Google Drive 동기화 폴더를 찾지 못했습니다.\n"
                "탐색기에서 «내 드라이브» 경로를 확인해 .env 에 넣으세요.\n"
                r"  SYNC_DIR=G:\내 드라이브\회의록" "\n"
                "(Drive for desktop 의 가상 드라이브는 대화형 세션에만 보입니다)"
            )
        base = roots[0] / CFG.drive_folder_name
        print(f"[sync] 동기화 폴더 자동 탐색: {base}")

    target = base / subfolder if subfolder else base
    target.mkdir(parents=True, exist_ok=True)
    return target


def sync_files(paths: List[Path], subfolder: str | None = None) -> List[Synced]:
    """파일들을 동기화 폴더로 복사한다. 같은 이름이 있으면 덮어쓰지 않고 _v2."""
    target = resolve_target(subfolder)
    out: List[Synced] = []
    for src in paths:
        dst = target / src.name
        if dst.exists():
            n = 2
            while (target / f"{src.stem}_v{n}{src.suffix}").exists():
                n += 1
            dst = target / f"{src.stem}_v{n}{src.suffix}"
            print(f"[sync] 같은 이름이 있어 {dst.name} 로 복사합니다")
        shutil.copy2(src, dst)
        print(f"[sync] {src.name} -> {dst}")
        out.append(Synced(src=src, dst=dst))
    print("[sync] Drive 가 백그라운드로 업로드합니다 (탐색기 아이콘으로 진행 확인)")
    return out
