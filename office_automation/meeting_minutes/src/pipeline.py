"""회의록 파이프라인 — 노트북(run.ipynb)과 «같은 함수» 를 쓴다.

    STT ──▶ 추출 ──▶ (검토) ──▶ 확정 ──▶ 드라이브 · 노션

두 가지 모드
    검수 모드 (기본)   추출 후 멈춘다. 사람이 항목을 고른 뒤 --accept 로 확정.
                      회의에서 나온 말이 전부 결정은 아니므로 사람이 고른다.
    오토 모드          --auto. 전부 반영하고 전송까지 한 번에. 새벽 스케줄러용.
                      대신 «확인 필요» 항목이 회의록에 표시로 남는다.

설정은 전부 `.env` 에 있다. CLI 인자는 «이번 실행만» 덮어쓴다.

사용
    python -m src.pipeline                          # .env 대로 (검수 모드)
    python -m src.pipeline --auto                   # 전부 반영 + 전송
    python -m src.pipeline --accept D1,A1           # 저장된 draft 에서 확정
    python -m src.pipeline --stt-only               # 녹취록만
    python -m src.pipeline --check                  # 연결 상태만 확인
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


from .config import CFG, reload as reload_env
from .schema import MinutesBundle


# ─────────────────────────────────────────────────────────────────────────────
#  단계별 함수 — 노트북 셀과 1:1 대응
# ─────────────────────────────────────────────────────────────────────────────
def resolve_input() -> tuple[Path, str]:
    """입력을 확정한다. (녹취록 경로, 원본 이름)

    오디오면 STT 를 돌린다. 둘 다 비어 있으면 멈춘다 — 예전엔 Path('') 가
    '.' 가 되어 폴더를 읽으려다 PermissionError 가 났다.
    """
    audio, tr = CFG.resolve_input()
    if not audio and not tr:
        raise SystemExit(
            "입력이 비어 있습니다.\n"
            "  .env 의 INPUT_AUDIO 또는 INPUT_TRANSCRIPT 를 채우세요.\n"
            "  예) INPUT_TRANSCRIPT=data/transcripts/meeting.txt"
        )
    if audio:
        if not audio.is_file():
            raise SystemExit(f"오디오 파일이 없습니다: {audio}")
        from .transcribe import transcribe

        return transcribe(audio), audio.name
    if not tr.is_file():
        raise SystemExit(f"녹취록 파일이 아닙니다: {tr}")
    print(f"[입력] STT 건너뜀 — 기존 녹취록 사용: {tr}")
    return tr, tr.name


def do_extract(transcript_path: Path, transcript: str, source_name: str):
    """추출. 노트북과 같은 extract_cli.extract() 를 쓴다."""
    if CFG.extract_mode == "cli":
        from .extract_cli import extract as extract_cli

        return extract_cli(
            transcript_path=transcript_path,
            transcript=transcript,
            source_name=source_name,
            title=CFG.meeting_title,
            date=CFG.meeting_date,
        )

    #  api 모드 — 인용 검증이 없다. 필요하면 extract_cli 의 check_quotes 를 붙일 것.
    from datetime import datetime

    from .extract import extract as extract_api
    from .extract_cli import ExtractResult

    minutes = extract_api(transcript, title=CFG.meeting_title or None,
                          date=CFG.meeting_date or None)
    bundle = MinutesBundle(
        minutes=minutes, source_audio=source_name, transcript_chars=len(transcript),
        model=CFG.model, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    draft = CFG.output_dir / "draft" / f"{minutes.title}.api.json"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return ExtractResult(ok=True, bundle=bundle, draft_path=draft, attempts=1)


def do_confirm(bundle: MinutesBundle, accept: str):
    """고른 항목만 남겨 md/html/json 을 만든다."""
    from .render import render
    from .review import apply_selection, labels, parse_accept

    picked, unknown = parse_accept(accept, bundle.minutes)
    if unknown:
        raise SystemExit(f"알 수 없는 라벨: {', '.join(unknown)}")
    if not picked and labels(bundle.minutes):
        raise SystemExit(
            "선택된 항목이 없습니다. --accept D1,A2 또는 --accept all 을 쓰세요."
        )
    final = bundle.model_copy(update={"minutes": apply_selection(bundle.minutes, picked)})
    out = render(final)
    if picked:
        print(f"[확정] {len(picked)}개 반영: {', '.join(picked)}")
    else:
        print("[확정] 결정·액션·미결이 없는 회의입니다. 논의 내용만으로 만듭니다.")
    return final, out


def do_send(bundle: MinutesBundle, out) -> None:
    """드라이브 전송. .env 의 SEND 가 방식을 정한다."""
    mode = CFG.send
    if not mode:
        print("[전송] SEND 가 비어 있어 로컬에만 저장했습니다.")
        for p in (out.md, out.html, out.json):
            print(f"  {p}")
        return

    if mode == "api":
        from .drive import upload_minutes

        print(f"[전송] {out.md.parent}")
        print(res_report(upload_minutes(out.md, out.html, out.json,
                                        subfolder=CFG.subfolder_for(out.slug))))
    elif mode == "sync":
        from .drive_accounts import confirm_target
        from .sync import sync_files

        if CFG.my_drive_email:
            confirm_target(CFG.my_drive_email, CFG.sync_dir or None)
        for s in sync_files([out.md, out.html, out.json],
                            subfolder=CFG.subfolder_for(out.slug)):
            print(f"  {s.dst.name}")
    else:
        print(f"[전송] SEND={mode!r} 는 무인 실행에서 쓸 수 없습니다. 건너뜁니다.")


def res_report(res) -> str:
    return res.report() if hasattr(res, "report") else str(res)


def do_notion(bundle: MinutesBundle, out) -> None:
    """노션 업로드. api 모드만 무인으로 된다."""
    if CFG.notion_mode == "api":
        from .notion import upload as notion_upload

        print("[노션]")
        print(res_report(notion_upload(bundle)))
    elif CFG.notion_mode == "mcp":
        print("[노션] mcp 모드는 사람이 채팅창에서 시켜야 합니다. 건너뜁니다.")
        print(f'  "{out.md}" 이 회의록을 노션 «{CFG.notion_target}» 에 올려줘.')
    else:
        print("[노션] NOTION_MODE 가 비어 있어 건너뜁니다.")


# ─────────────────────────────────────────────────────────────────────────────
#  확인
# ─────────────────────────────────────────────────────────────────────────────
def do_check() -> int:
    print(CFG.summary())
    print()
    ok = True

    audio, tr = CFG.resolve_input()
    src = audio or tr
    print(f"입력      {'O' if src and src.is_file() else 'X'}  {src or '(비어 있음)'}")
    ok &= bool(src and src.is_file())

    if CFG.send == "api":
        c = CFG.drive_credentials.exists()
        t = CFG.drive_token.exists()
        print(f"드라이브   {'O' if c else 'X'}  credentials.json"
              f"   {'O' if t else '-'}  token.json{'' if t else ' (첫 실행 시 브라우저)'}")
        ok &= c
    if CFG.notion_mode == "api":
        from .notion import check as notion_check

        info = notion_check()
        print(f"노션      {'O' if info['ok'] else 'X'}  {info['account'] or ''}"
              f"  -> {info['parent_title'] or ''}")
        if not info["ok"]:
            print("          " + info["error"].replace("\n", "\n          "))
        ok &= info["ok"]

    print()
    print("준비 완료" if ok else "위 X 항목을 먼저 채우세요")
    return 0 if ok else 1


# ─────────────────────────────────────────────────────────────────────────────
#  실행
# ─────────────────────────────────────────────────────────────────────────────
def run(args: argparse.Namespace) -> int:
    reload_env()
    #  CLI 인자는 «이번 실행만» .env 를 덮어쓴다
    for key, val in (("input_audio", args.audio), ("input_transcript", args.transcript),
                     ("meeting_title", args.title), ("meeting_date", args.date),
                     ("send", args.send), ("notion_mode", args.notion)):
        if val:
            setattr(CFG, key, val)
    CFG.ensure_dirs()

    if args.print_schema:
        from .schema import Minutes

        print(json.dumps(Minutes.model_json_schema(), ensure_ascii=False, indent=2))
        return 0
    if args.check:
        return do_check()

    #  저장된 draft 에서 확정만 하는 경로
    if args.draft:
        p = Path(args.draft)
        if not p.is_file():
            print(f"draft 파일이 없습니다: {p}", file=sys.stderr)
            return 1
        bundle = MinutesBundle.model_validate(json.loads(p.read_text(encoding="utf-8")))
        if not (args.accept or args.auto):
            from .review import render_review

            print(render_review(bundle.minutes))
            return 0
        final, out = do_confirm(bundle, args.accept or "all")
        do_send(final, out)
        do_notion(final, out)
        return 0

    #  1) 입력
    transcript_path, source_name = resolve_input()
    if args.stt_only:
        print(f"완료: {transcript_path}")
        return 0
    transcript = transcript_path.read_text(encoding="utf-8")
    if not transcript.strip():
        print(f"녹취록이 비어 있습니다: {transcript_path}", file=sys.stderr)
        return 1

    #  2) 추출
    res = do_extract(transcript_path, transcript, source_name)
    print()
    print(res.report())
    if not res.ok:
        return 1

    #  3) 확정 — 검수 모드면 여기서 멈춘다
    accept = args.accept or ("all" if args.auto else CFG.accept)
    if not accept:
        from .review import render_review

        print()
        print(render_review(res.bundle.minutes))
        print()
        print("검수 모드입니다. 반영할 항목을 골라 다시 실행하세요:")
        print(f'  python -m src.pipeline --draft "{res.draft_path}" --accept D1,A1')
        print("  (전부 반영: --auto 또는 --accept all)")
        return 0

    final, out = do_confirm(res.bundle, accept)

    #  4) 전송
    print()
    do_send(final, out)
    print()
    do_notion(final, out)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="회의 녹음/녹취록 -> 회의록 -> 드라이브·노션",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--auto", action="store_true",
                   help="오토 모드: 전부 반영하고 전송까지 (사람 검수 없음)")
    p.add_argument("--accept", help="반영할 라벨. D1,A1,Q1 또는 all")
    p.add_argument("--draft", help="저장된 draft json 에서 확정만")

    p.add_argument("--audio", help=".env 의 INPUT_AUDIO 를 덮어씀")
    p.add_argument("--transcript", help=".env 의 INPUT_TRANSCRIPT 를 덮어씀")
    p.add_argument("--title", help=".env 의 MEETING_TITLE 을 덮어씀")
    p.add_argument("--date", help=".env 의 MEETING_DATE 를 덮어씀")
    p.add_argument("--send", choices=["api", "sync", "manual", ""],
                   help=".env 의 SEND 를 덮어씀")
    p.add_argument("--notion", choices=["api", "mcp", ""],
                   help=".env 의 NOTION_MODE 를 덮어씀")

    p.add_argument("--stt-only", action="store_true", help="녹취록만 만들고 종료")
    p.add_argument("--check", action="store_true", help="연결 상태만 확인")
    p.add_argument("--print-schema", action="store_true", help="Minutes JSON 스키마 출력")
    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
