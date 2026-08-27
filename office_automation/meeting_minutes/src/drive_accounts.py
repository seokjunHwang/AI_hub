"""Drive for desktop 에 로그인된 «계정» 을 확인한다.

왜 필요한가
    이 PC 는 Drive for desktop 에 여러 계정이 로그인돼 있고, 그 중 사용자 계정이
    아닌 것이 섞여 있었다. 그 상태로 동기화 폴더에 복사하면 회의록이 남의
    드라이브로 올라간다. 파일을 옮기기 «전에» 대상 계정을 확인해야 한다.

    자동 탐색(먼저 찾은 드라이브)에 맡기면 안 되는 이유가 이것이다.

읽는 곳 (전부 로컬 · 네트워크 호출 없음)
    HKCU\\Software\\Google\\DriveFS  PerAccountPreferences  -> 계정ID ↔ 마운트 문자
    %LOCALAPPDATA%\\Google\\DriveFS\\<계정ID>\\...           -> 계정 이메일 흔적
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
DRIVE_ROOT_NAMES = ("My Drive", "내 드라이브")

# 바이너리에서 긁은 이메일은 뒤에 잡음이 붙는다 ("gmail.comZ", "thegrowlabs.ioj").
# TLD 를 아래 목록에 맞춰 «가장 긴 유효 접두사» 로 잘라낸다.
KNOWN_TLDS = {
    "com", "net", "org", "io", "co", "kr", "ai", "dev", "app", "me", "us",
    "uk", "jp", "cn", "de", "fr", "edu", "gov", "info", "biz", "xyz", "cc",
    "tv", "pro", "team", "cloud", "tech", "so", "sh", "gg",
}


def _clean_email(raw: str) -> Optional[str]:
    """뒤에 붙은 잡음을 떼고 유효해 보이는 이메일만 남긴다."""
    e = raw.strip().strip(".")
    if "@" not in e:
        return None
    local, _, domain = e.rpartition("@")
    if "." not in domain:
        return None
    head, _, tld = domain.rpartition(".")
    low = tld.lower()
    # 긴 것부터 잘라가며 아는 TLD 를 찾는다 (comZ -> com, ioj -> io)
    for n in range(len(low), 1, -1):
        if low[:n] in KNOWN_TLDS:
            return f"{local}@{head}.{low[:n]}"
    return None


@dataclass
class DriveAccount:
    account_id: str
    email: Optional[str] = None
    mount: Optional[str] = None          # 'G' 같은 드라이브 문자
    is_current: bool = False
    roots: List[Path] = field(default_factory=list)   # 실제로 접근 가능한 My Drive 경로

    @property
    def label(self) -> str:
        who = self.email or f"(이메일 미확인 · id={self.account_id[:8]}…)"
        where = f"{self.mount}:" if self.mount else "(마운트 미지정)"
        return f"{who}  ->  {where}"


def _reg_query(key: str, value: str) -> str:
    try:
        out = subprocess.run(
            ["reg", "query", key, "/v", value],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        return (out.stdout or "") + (out.stderr or "")
    except Exception:
        return ""


def _mounts_and_current() -> tuple[dict, str]:
    """계정ID -> 마운트 문자, 그리고 현재 활성 계정ID."""
    key = r"HKCU\Software\Google\DriveFS"
    raw = _reg_query(key, "PerAccountPreferences")
    mounts: dict = {}
    m = re.search(r"(\{.*\})", raw, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            for entry in data.get("per_account_preferences", []):
                acct = str(entry.get("key", ""))
                mp = (entry.get("value") or {}).get("mount_point_path")
                if acct:
                    mounts[acct] = mp
        except json.JSONDecodeError:
            pass

    cur_raw = _reg_query(key, "CurrentAccountToken")
    cur = ""
    cm = re.search(r"REG_SZ\s+(\d{10,})", cur_raw)
    if cm:
        cur = cm.group(1)
    return mounts, cur


def _emails_for(account_dir: Path) -> List[str]:
    """계정 캐시 폴더에서 이메일 문자열을 긁어온다. 순수 로컬 파일 스캔."""
    found: List[str] = []
    targets = ["metadata_sqlite_db", "root_preference_sqlite.db", "experiments.db"]
    for name in targets:
        p = account_dir / name
        if not p.is_file():
            continue
        try:
            blob = p.read_bytes()
        except OSError:
            continue
        text = blob.decode("utf-8", errors="ignore")
        for raw in EMAIL_RE.findall(text):
            e = _clean_email(raw)
            if e and e not in found:
                found.append(e)
    return found


def list_accounts() -> List[DriveAccount]:
    """로그인된 Drive 계정 목록. Drive 가 안 돌고 있으면 빈 목록."""
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "DriveFS"
    if not base.is_dir():
        return []

    mounts, current = _mounts_and_current()
    out: List[DriveAccount] = []

    for d in sorted(base.iterdir()):
        if not d.is_dir() or not d.name.isdigit():
            continue
        emails = _emails_for(d)
        acct = DriveAccount(
            account_id=d.name,
            email=emails[0] if emails else None,
            mount=mounts.get(d.name),
            is_current=(d.name == current),
        )
        # 실제 접근 가능한 My Drive 경로 확인 (대화형 세션에서만 보인다)
        letters = [acct.mount] if acct.mount else list("GHIJKLMNOPQRSTUVWXYZ")
        for letter in letters:
            if not letter:
                continue
            for name in DRIVE_ROOT_NAMES:
                p = Path(f"{letter}:/") / name
                try:
                    if p.is_dir():
                        acct.roots.append(p)
                except OSError:
                    pass
        out.append(acct)
    return out


def confirm_target(expected_email: str, sync_dir: str | None = None) -> Path:
    """전송 대상이 «본인» 계정인지 확인하고 대상 폴더를 돌려준다.

    expected_email 과 일치하는 계정이 없으면 SystemExit 로 멈춘다.
    파일을 옮기기 전에 부르는 것이 요점이다.
    """
    if not expected_email or "@" not in expected_email:
        raise SystemExit("본인 Google 계정 이메일을 MY_DRIVE_EMAIL 에 적어주세요.")

    accounts = list_accounts()
    if not accounts:
        raise SystemExit(
            "Drive for desktop 계정을 찾지 못했습니다. Drive 가 실행 중인지 확인하세요."
        )

    target_acct = next(
        (a for a in accounts if (a.email or "").lower() == expected_email.lower()), None
    )
    if target_acct is None:
        lines = ["로그인된 계정 중에 본인 계정이 없습니다.", f"  찾는 계정: {expected_email}", "  현재 로그인:"]
        lines += [f"    - {a.label}" for a in accounts]
        lines.append("")
        lines.append("작업표시줄 Drive 아이콘 -> 톱니바퀴 -> 계정 추가 로 본인 계정을 넣으세요.")
        lines.append("또는 6번 셀에서 SEND='api' 로 두고 브라우저 인증 때 본인 계정으로 로그인하세요.")
        raise SystemExit("\n".join(lines))

    if sync_dir:
        base = Path(sync_dir)
        letter = base.drive.rstrip(":\\/").upper()[:1]
        if target_acct.mount and letter and letter != target_acct.mount.upper():
            raise SystemExit(
                f"SYNC_DIR 이 {letter}: 인데 {expected_email} 은 "
                f"{target_acct.mount}: 에 마운트돼 있습니다. 경로를 고치세요."
            )
        return base

    if not target_acct.roots:
        raise SystemExit(
            f"{expected_email} 의 마운트({target_acct.mount or '미지정'}:)를 이 프로세스에서 볼 수 없습니다.\n"
            "탐색기에서 경로를 확인해 .env 의 SYNC_DIR 에 넣어주세요."
        )
    return target_acct.roots[0]
