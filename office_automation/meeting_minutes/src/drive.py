"""Google Drive 업로드.

인증: OAuth 설치형 앱 흐름(최초 1회 브라우저) -> token.json 캐시.
서비스 계정은 개인 My Drive 에 쓸 수 없다(공유 드라이브만) -> 사용자 OAuth 를 쓴다.

스코프 주의
  drive.file (기본)  이 앱이 만든 파일/폴더만 접근. 최소 권한이므로 권장.
                     -> DRIVE_PARENT_ID 로 "수동으로 만든 기존 폴더"를 지정하면 실패할 수 있다.
  drive              드라이브 전체 접근. 기존 폴더에 넣어야 할 때만 DRIVE_SCOPE 로 승격.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import CFG

GDOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass
class Uploaded:
    name: str
    file_id: str
    link: str


def _service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "구글 드라이브 연동에 필요한 패키지가 없습니다:\n"
            "  pip install google-api-python-client google-auth-oauthlib"
        ) from e

    scopes = [CFG.drive_scope]
    creds: Optional[Credentials] = None

    if CFG.drive_token.exists():
        creds = Credentials.from_authorized_user_file(str(CFG.drive_token), scopes)
        # DRIVE_SCOPE 를 올렸는데 토큰이 옛 스코프면 refresh 로는 권한이 늘지 않는다.
        # 그대로 두면 업로드 시점에 403 이 나므로 여기서 잡고 재인증한다.
        granted = set(creds.scopes or [])
        if granted and not set(scopes) <= granted:
            print(f"[drive] 토큰 스코프가 요청과 다릅니다. 재인증합니다.")
            print(f"        보유: {sorted(granted)}")
            print(f"        요청: {scopes}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CFG.drive_credentials.exists():
                raise SystemExit(
                    f"OAuth 클라이언트 파일이 없습니다: {CFG.drive_credentials}\n"
                    "Google Cloud Console > API 및 서비스 > 사용자 인증 정보 에서\n"
                    "'데스크톱 앱' OAuth 클라이언트를 만들어 credentials.json 으로 저장하세요."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CFG.drive_credentials), scopes
            )
            creds = flow.run_local_server(port=0)
        CFG.drive_token.write_text(creds.to_json(), encoding="utf-8")
        print(f"[drive] 토큰 저장: {CFG.drive_token}")

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _q(value: str) -> str:
    """Drive 쿼리 문자열 리터럴 이스케이프.

    폴더명/회의 제목에 ' 가 들어가면(예: "Kim's 회의록") 인용부호가 어긋나
    Drive API 가 400 을 던진다. 백슬래시 이스케이프가 규격이다.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_folder(svc, name: str, parent_id: str = "") -> str:
    """이름으로 폴더를 찾고 없으면 만든다. 폴더 ID 반환."""
    q = [
        f"mimeType = '{FOLDER_MIME}'",
        f"name = '{_q(name)}'",
        "trashed = false",
    ]
    if parent_id:
        q.append(f"'{parent_id}' in parents")
    res = (
        svc.files()
        .list(
            q=" and ".join(q),
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    body = {"name": name, "mimeType": FOLDER_MIME}
    if parent_id:
        body["parents"] = [parent_id]
    folder = (
        svc.files()
        .create(body=body, fields="id", supportsAllDrives=True)
        .execute()
    )
    print(f"[drive] 폴더 생성: {name}")
    return folder["id"]


def upload_file(
    svc, path: Path, folder_id: str, mime: str | None = None, as_gdoc: bool = False
) -> Uploaded:
    from googleapiclient.http import MediaFileUpload

    guess = {
        ".md": "text/markdown",
        ".html": "text/html",
        ".json": "application/json",
        ".txt": "text/plain",
    }
    src_mime = mime or guess.get(path.suffix.lower(), "application/octet-stream")

    body: dict = {"name": path.name, "parents": [folder_id]}
    if as_gdoc:
        # HTML -> Google Docs 변환 업로드. 확장자를 뗀 이름으로 문서 생성.
        body["name"] = path.stem
        body["mimeType"] = GDOC_MIME

    media = MediaFileUpload(str(path), mimetype=src_mime, resumable=True)
    f = (
        svc.files()
        .create(
            body=body,
            media_body=media,
            fields="id, name, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    up = Uploaded(name=f["name"], file_id=f["id"], link=f.get("webViewLink", ""))
    print(f"[drive] 업로드: {up.name} -> {up.link}")
    return up


def upload_minutes(
    md: Path, html: Path, json_path: Path, subfolder: str | None = None, as_gdoc: bool = True
) -> list[Uploaded]:
    """회의록 3종을 드라이브에 올린다.

    구조: {DRIVE_FOLDER_NAME}/{subfolder}/  (subfolder 는 보통 회의 slug)
    """
    svc = _service()
    root = ensure_folder(svc, CFG.drive_folder_name, CFG.drive_parent_id)
    target = ensure_folder(svc, subfolder, root) if subfolder else root

    out = [
        upload_file(svc, md, target),
        upload_file(svc, json_path, target),
    ]
    # HTML 은 원본 + (선택) Google Docs 변환본 둘 다. 변환본이 있으면 드라이브에서 바로 읽힌다.
    out.append(upload_file(svc, html, target))
    if as_gdoc:
        out.append(upload_file(svc, html, target, mime="text/html", as_gdoc=True))
    return out
