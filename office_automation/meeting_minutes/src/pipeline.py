"""회의록 파이프라인 CLI.

기본 흐름은 «2단» 이다. 추출 결과를 바로 문서로 만들지 않고 한 번 멈춘다.
회의에서 나온 말이 전부 결정은 아니므로, 사람이 고른 것만 확정본에 들어간다.

  1단 · 추출 + 검토 목록
    python -m src.pipeline --audio data/audio/kickoff.m4a --title "킥오프" --date 2026-08-25
    python -m src.pipeline --transcript data/transcripts/kickoff.txt

  2단 · 고른 것만 확정 (+업로드)
    python -m src.pipeline --draft data/minutes/draft/2026-08-25_킥오프.draft.json \
                           --accept D1,D2,A1,A3 --upload
    python -m src.pipeline --draft ... --accept all --upload

  건너뛰기 (예전 방식 그대로 한 번에)
    python -m src.pipeline --transcript ... --accept all --upload

  STT 만
    python -m src.pipeline --audio ... --stt-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .config import CFG
from .extract import extract, load_transcript
from .render import render, slugify
from .review import apply_selection, blank_report, parse_accept, render_review
from .schema import MinutesBundle


def _draft_dir() -> Path:
    d = CFG.output_dir / "draft"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_draft(bundle: MinutesBundle) -> Path:
    m = bundle.minutes
    path = _draft_dir() / f"{slugify(m.title, m.date)}.draft.json"
    path.write_text(
        json.dumps(bundle.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def _load_draft(path: Path) -> MinutesBundle:
    return MinutesBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _load_minutes_json(path: Path, source_name: str, chars: int) -> MinutesBundle:
    """Claude Code(CLI) 가 만들어 준 Minutes JSON 을 받아 bundle 로 감싼다.

    API 키 없이 쓰는 경로다. 추출은 CLI 가 하고, 이 스크립트는 렌더·동기화만 한다.
    Minutes 든 MinutesBundle 든 받아준다.
    """
    from .schema import Minutes

    raw = json.loads(path.read_text(encoding="utf-8"))
    if "minutes" in raw:                       # 이미 bundle 형태
        return MinutesBundle.model_validate(raw)
    minutes = Minutes.model_validate(raw)      # Minutes 만 준 경우
    return MinutesBundle(
        minutes=minutes,
        source_audio=source_name,
        transcript_chars=chars,
        model="claude-code(cli)",
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def _warn_blanks(bundle: MinutesBundle) -> None:
    """빈칸을 이유별로 나눠 알린다. «미확인» 과 «미조사» 는 다음 행동이 다르다."""
    r = blank_report(bundle.minutes)
    if r["not_stated"]:
        print(f"  ! 회의에서 담당/마감을 정하지 않은 액션 {r['not_stated']}건 — 참석자에게 확인")
    if r["unclear"]:
        print(f"  ! 녹취가 불확실해 확인 못 한 액션 {r['unclear']}건 — 원본 오디오 재확인")
    if r["notes"]:
        print(f"  ! 녹취 불확실 구간 {r['notes']}건 — 확정 전 확인 필요")


def _finalize(bundle: MinutesBundle, args: argparse.Namespace) -> int:
    """고른 항목만 남겨 확정본을 렌더하고, 필요하면 업로드."""
    from .review import labels

    picked, unknown = parse_accept(args.accept, bundle.minutes)
    if unknown:
        print(f"알 수 없는 라벨: {', '.join(unknown)}", file=sys.stderr)
        return 1
    if not picked:
        # 고를 항목이 애초에 없는 회의(정보 공유·논의만 한 회의)와
        # 사용자가 잘못 입력한 경우를 구분한다. 전자는 회의록을 만들어야 한다.
        if labels(bundle.minutes):
            print(
                "선택된 항목이 없습니다. 라벨을 지정하세요 (예: --accept D1,A2 또는 all)",
                file=sys.stderr,
            )
            return 1
        print("[확정] 결정·액션·미결 항목이 없는 회의입니다. 논의 내용만으로 회의록을 만듭니다.")

    selected = apply_selection(bundle.minutes, picked)
    final = bundle.model_copy(update={"minutes": selected})

    out = render(final, Path(args.out) if args.out else None, overwrite=args.overwrite)
    if picked:
        print(f"[확정] {len(picked)}개 항목 반영: {', '.join(picked)}")

    # 동기화 폴더 방식 (설정 불필요, 권장)
    if args.sync:
        from .sync import sync_files

        sync_files([out.md, out.html, out.json], subfolder=out.slug)

    # API 방식 (공유 링크·Docs 변환이 필요할 때)
    if args.upload:
        from .drive import upload_minutes

        res = upload_minutes(out.md, out.html, out.json, subfolder=out.slug)
        print(res.report())

    _warn_blanks(final)
    return 0


def run(args: argparse.Namespace) -> int:
    CFG.ensure_dirs()

    # --- 스키마 출력: Claude Code(CLI) 가 따를 JSON 스키마 ---
    if args.print_schema:
        from .schema import Minutes

        print(json.dumps(Minutes.model_json_schema(), ensure_ascii=False, indent=2))
        return 0

    # --- 동기화 폴더 확인 ---
    if args.check_sync:
        from .sync import find_drive_roots, resolve_target

        roots = find_drive_roots()
        print(f"자동 탐색된 «내 드라이브»: {roots or '없음'}")
        print(f"SYNC_DIR: {CFG.sync_dir or '(미설정 — 자동 탐색 사용)'}")
        try:
            print(f"복사 대상: {resolve_target()}")
            print("준비 완료 — --sync 를 쓸 수 있습니다")
        except SystemExit as e:
            print(e)
            return 1
        return 0

    # --- CLI 추출 결과를 받아 확정 (API 키 없이 쓰는 경로) ---
    if args.minutes_json:
        mj = Path(args.minutes_json)
        if not mj.exists():
            print(f"Minutes JSON 이 없습니다: {mj}", file=sys.stderr)
            return 1
        bundle = _load_minutes_json(mj, mj.name, 0)
        draft_path = _save_draft(bundle)
        if args.accept:
            return _finalize(bundle, args)
        print("\n" + render_review(bundle.minutes))
        _warn_blanks(bundle)
        print(f"\n[draft] {draft_path}")
        return 0

    # --- 2단만 실행: 저장된 draft 에서 확정 ---
    if args.draft:
        draft = Path(args.draft)
        if not draft.exists():
            print(f"draft 파일이 없습니다: {draft}", file=sys.stderr)
            return 1
        bundle = _load_draft(draft)
        if not args.accept:
            print(render_review(bundle.minutes))
            return 0
        return _finalize(bundle, args)

    # --- 1단: STT ---
    if args.audio:
        from .transcribe import transcribe

        audio = Path(args.audio)
        if not audio.exists():
            print(f"오디오 파일이 없습니다: {audio}", file=sys.stderr)
            return 1
        transcript_path = transcribe(audio)
        if args.stt_only:
            print(f"완료: {transcript_path}")
            return 0
        source_name = audio.name
    else:
        transcript_path = Path(args.transcript)
        if not transcript_path.exists():
            print(f"녹취록 파일이 없습니다: {transcript_path}", file=sys.stderr)
            return 1
        source_name = transcript_path.name

    transcript = load_transcript(transcript_path)
    if not transcript.strip():
        print("녹취록이 비어 있습니다.", file=sys.stderr)
        return 1

    # --- 1단: 구조화 추출 ---
    minutes = extract(transcript, title=args.title, date=args.date)
    bundle = MinutesBundle(
        minutes=minutes,
        source_audio=source_name,
        transcript_chars=len(transcript),
        model=CFG.model,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    draft_path = _save_draft(bundle)

    # --accept 를 같이 줬으면 멈추지 않고 바로 확정
    if args.accept:
        return _finalize(bundle, args)

    # --- 멈춤 게이트 ---
    print("\n" + render_review(bundle.minutes))
    _warn_blanks(bundle)
    print(f"\n[draft] {draft_path}")
    print("확정하려면:")
    print(f'  python -m src.pipeline --draft "{draft_path}" --accept D1,A1 --upload')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="회의 녹음 -> 회의록 -> 구글 드라이브")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--audio", help="회의 녹음 파일 (m4a/mp3/wav ...)")
    src.add_argument("--transcript", help="이미 있는 녹취록 텍스트 파일")
    src.add_argument("--draft", help="1단에서 저장된 draft json (2단 확정용)")
    src.add_argument(
        "--minutes-json",
        help="Claude Code(CLI) 가 만든 Minutes JSON. API 키 없이 쓰는 경로",
    )
    src.add_argument("--print-schema", action="store_true", help="Minutes JSON 스키마 출력")
    src.add_argument("--check-sync", action="store_true", help="드라이브 동기화 폴더 확인")

    p.add_argument("--accept", help="반영할 라벨. 예: D1,D2,A1,A3 또는 all")
    p.add_argument("--title", help="회의 제목 (없으면 내용에서 생성)")
    p.add_argument("--date", help="회의 날짜 YYYY-MM-DD")
    p.add_argument("--out", help="산출물 디렉터리 (기본 data/minutes)")
    p.add_argument("--sync", action="store_true", help="드라이브 동기화 폴더로 복사 (설정 불필요)")
    p.add_argument("--upload", action="store_true", help="드라이브 API 업로드 (OAuth 필요)")
    p.add_argument("--overwrite", action="store_true", help="같은 이름 파일을 덮어쓴다")
    p.add_argument("--stt-only", action="store_true", help="녹취록만 만들고 종료")

    return run(p.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
