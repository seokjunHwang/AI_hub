"""회의록 자동화 GUI (tkinter — 표준 라이브러리, 추가 설치 없음).

두 가지 모드
    사람검수 모드   추출 후 «멈춘다». 결정·액션·미결을 체크박스로 골라 확정.
                   회의에서 나온 말이 전부 결정은 아니므로 사람이 고른다.
    오토 모드       멈추지 않고 전부 반영 -> 확정 -> 드라이브·노션 까지 한 번에.

설계 원칙
    1. 로직을 여기에 쓰지 않는다. src/pipeline.py 의 단계 함수를 그대로 부른다.
       GUI 와 CLI 가 다르게 동작하면 «어느 쪽이 맞나» 를 알 수 없다.
    2. 작업은 «별 스레드» 에서 돈다. STT 는 몇 분씩 걸리는데 메인 스레드에서
       돌리면 창이 얼어 죽은 것처럼 보인다.
    3. 어떤 예외도 창을 닫지 않는다. 로그에 적고 버튼을 되살린다.

실행
    python -m src.gui        또는   회의록_GUI.bat  더블클릭
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import CFG, reload as reload_env

ROOT = Path(__file__).resolve().parent.parent
PAD = 8


# ─────────────────────────────────────────────────────────────────────────────
#  로그 — 작업 스레드가 큐에 넣고, 메인 스레드가 위젯에 그린다.
#  tkinter 는 다른 스레드에서 위젯을 만지면 조용히 깨진다.
# ─────────────────────────────────────────────────────────────────────────────
class TeeToQueue:
    """작업 스레드의 print() 를 가로채 로그창으로 보낸다."""

    def __init__(self, q: "queue.Queue[tuple]") -> None:
        self.q = q

    def write(self, s: str) -> None:
        if s:
            self.q.put(("log", s))

    def flush(self) -> None:
        pass


class App(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=PAD)
        master.title("회의록 자동화")
        master.geometry("980x760")
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self.bundle = None          # 추출 결과 (검수 대기)
        self.draft_path: Optional[Path] = None
        self.vars: Dict[str, tk.BooleanVar] = {}

        self._build()
        self._load_env()
        self.after(80, self._drain)

    # ── 화면 ────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        r = 0

        #  1) 입력
        box = ttk.LabelFrame(self, text="1. 입력", padding=PAD)
        box.grid(row=r, column=0, sticky="ew", pady=(0, PAD)); r += 1
        box.columnconfigure(1, weight=1)

        self.v_input = tk.StringVar()
        self.v_kind = tk.StringVar(value="transcript")
        ttk.Radiobutton(box, text="녹취록 (.txt)", variable=self.v_kind,
                        value="transcript").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(box, text="오디오 (STT 부터)", variable=self.v_kind,
                        value="audio").grid(row=0, column=1, sticky="w")
        ttk.Entry(box, textvariable=self.v_input).grid(row=1, column=0, columnspan=2,
                                                      sticky="ew", pady=(4, 0))
        ttk.Button(box, text="찾기…", command=self._pick).grid(row=1, column=2,
                                                              padx=(6, 0), pady=(4, 0))

        self.v_title = tk.StringVar()
        self.v_date = tk.StringVar()
        ttk.Label(box, text="제목 (비우면 모델이 정함)").grid(row=2, column=0,
                                                           sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=self.v_title).grid(row=3, column=0, columnspan=2,
                                                      sticky="ew")
        ttk.Label(box, text="날짜 YYYY-MM-DD").grid(row=2, column=2, sticky="w",
                                                   padx=(6, 0), pady=(6, 0))
        ttk.Entry(box, textvariable=self.v_date, width=14).grid(row=3, column=2,
                                                               sticky="w", padx=(6, 0))

        #  2) 모드
        box = ttk.LabelFrame(self, text="2. 모드", padding=PAD)
        box.grid(row=r, column=0, sticky="ew", pady=(0, PAD)); r += 1
        self.v_mode = tk.StringVar(value="review")
        ttk.Radiobutton(
            box, text="사람검수 모드 — 추출 후 멈춤. 항목을 골라 확정",
            variable=self.v_mode, value="review",
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            box, text="오토 모드 — 전부 반영하고 업로드까지 (검수 없음)",
            variable=self.v_mode, value="auto",
        ).grid(row=1, column=0, sticky="w")

        self.v_send = tk.BooleanVar(value=True)
        self.v_notion = tk.BooleanVar(value=True)
        sub = ttk.Frame(box)
        sub.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Checkbutton(sub, text="구글 드라이브", variable=self.v_send).grid(row=0, column=0)
        ttk.Checkbutton(sub, text="노션", variable=self.v_notion).grid(row=0, column=1,
                                                                     padx=(12, 0))
        self.l_env = ttk.Label(sub, text="", foreground="#666")
        self.l_env.grid(row=0, column=2, padx=(16, 0))

        #  3) 실행
        bar = ttk.Frame(self)
        bar.grid(row=r, column=0, sticky="ew", pady=(0, PAD)); r += 1
        self.b_run = ttk.Button(bar, text="▶  실행", command=self._start)
        self.b_run.grid(row=0, column=0)
        ttk.Button(bar, text="연결 확인", command=self._check).grid(row=0, column=1,
                                                                 padx=(6, 0))
        ttk.Button(bar, text=".env 다시 읽기", command=self._load_env).grid(row=0, column=2,
                                                                        padx=(6, 0))
        ttk.Button(bar, text="결과 폴더", command=self._open_out).grid(row=0, column=3,
                                                                   padx=(6, 0))
        self.l_state = ttk.Label(bar, text="대기")
        self.l_state.grid(row=0, column=4, padx=(12, 0))
        self.pb = ttk.Progressbar(bar, mode="indeterminate", length=140)
        self.pb.grid(row=0, column=5, padx=(8, 0))

        #  4) 검수 — 사람검수 모드에서만 채워진다
        self.f_review = ttk.LabelFrame(self, text="4. 검수 — 회의록에 넣을 항목", padding=PAD)
        self.f_review.grid(row=r, column=0, sticky="nsew", pady=(0, PAD))
        self.rowconfigure(r, weight=1); r += 1
        self.f_review.columnconfigure(0, weight=1)
        self.f_review.rowconfigure(0, weight=1)

        canvas = tk.Canvas(self.f_review, height=170, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(self.f_review, orient="vertical", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=sb.set)
        self.inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self.canvas = canvas

        act = ttk.Frame(self.f_review)
        act.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(act, text="전체 선택", command=lambda: self._all(True)).grid(row=0, column=0)
        ttk.Button(act, text="전체 해제", command=lambda: self._all(False)).grid(row=0, column=1,
                                                                             padx=(6, 0))
        self.b_confirm = ttk.Button(act, text="✔  확정 후 업로드", command=self._confirm,
                                    state="disabled")
        self.b_confirm.grid(row=0, column=2, padx=(16, 0))
        self.l_pick = ttk.Label(act, text="")
        self.l_pick.grid(row=0, column=3, padx=(12, 0))

        #  5) 로그
        box = ttk.LabelFrame(self, text="진행 상황", padding=4)
        box.grid(row=r, column=0, sticky="nsew")
        self.rowconfigure(r, weight=2)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.log = tk.Text(box, height=14, wrap="word", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        s2 = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)
        s2.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=s2.set)

    # ── 설정 ────────────────────────────────────────────────────────────────
    def _load_env(self) -> None:
        """`.env` 를 정본으로 읽어 화면 기본값을 채운다."""
        try:
            reload_env()
        except Exception as e:
            self._say(f"[경고] .env 를 읽지 못했습니다: {e}\n")
            return
        audio, tr = CFG.resolve_input()
        if audio:
            self.v_kind.set("audio")
            self.v_input.set(str(audio))
        elif tr:
            self.v_kind.set("transcript")
            self.v_input.set(str(tr))
        self.v_title.set(CFG.meeting_title or "")
        self.v_date.set(CFG.meeting_date or "")
        self.v_send.set(bool(CFG.send))
        self.v_notion.set(bool(CFG.notion_mode))
        self.l_env.config(text=f"모델 {CFG.cli_model} / {CFG.cli_effort}   "
                               f"전송 {CFG.send or '-'}   노션 {CFG.notion_mode or '-'}")
        self._say("[설정] .env 를 읽었습니다.\n")

    def _pick(self) -> None:
        audio = self.v_kind.get() == "audio"
        types = ([("오디오", "*.m4a *.mp3 *.wav *.mp4 *.flac *.ogg")] if audio
                 else [("텍스트", "*.txt *.md")])
        p = filedialog.askopenfilename(
            title="파일 선택", filetypes=types + [("모든 파일", "*.*")],
            initialdir=str(CFG.audio_dir if audio else CFG.transcript_dir),
        )
        if p:
            self.v_input.set(p)

    def _open_out(self) -> None:
        import os
        try:
            os.startfile(str(CFG.output_dir))          # noqa: S606  (Windows 전용)
        except Exception as e:
            self._say(f"[경고] 폴더를 열지 못했습니다: {e}\n")

    # ── 로그·상태 ───────────────────────────────────────────────────────────
    def _say(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def _drain(self) -> None:
        """작업 스레드가 보낸 메시지를 메인 스레드에서 처리한다."""
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._say(payload)
                elif kind == "state":
                    self.l_state.config(text=payload)
                elif kind == "review":
                    self._fill_review(payload)
                elif kind == "done":
                    self._idle(payload)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _idle(self, msg: str) -> None:
        self.busy = False
        self.pb.stop()
        self.b_run.config(state="normal")
        self.l_state.config(text=msg)

    def _work(self, fn) -> None:
        """작업을 별 스레드에서 돌린다. 어떤 예외도 창을 닫지 않는다."""
        if self.busy:
            messagebox.showinfo("실행 중", "지금 작업이 끝난 뒤에 다시 눌러 주세요.")
            return
        self.busy = True
        self.b_run.config(state="disabled")
        self.pb.start(12)

        def runner() -> None:
            out = TeeToQueue(self.q)
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = out
            try:
                msg = fn()
                self.q.put(("done", msg or "완료"))
            except SystemExit as e:
                #  우리 코드가 «사람에게 알리려고» 던지는 것. 원인이 문장으로 들어 있다.
                self.q.put(("log", f"\n[중단] {e}\n"))
                self.q.put(("done", "중단"))
            except BaseException:
                self.q.put(("log", "\n[예상 못한 오류]\n" + traceback.format_exc()))
                self.q.put(("done", "오류 — 로그 확인"))
            finally:
                sys.stdout, sys.stderr = old_out, old_err

        threading.Thread(target=runner, daemon=True).start()

    # ── 검수 목록 ───────────────────────────────────────────────────────────
    def _fill_review(self, bundle) -> None:
        from .review import review_items

        for w in self.inner.winfo_children():
            w.destroy()
        self.vars.clear()
        self.bundle = bundle

        items = review_items(bundle.minutes)
        if not items:
            ttk.Label(self.inner,
                      text="결정·액션·미결이 없는 회의입니다. 논의 내용만으로 만듭니다.").grid(
                          row=0, column=0, sticky="w")
        for i, (lab, text, note) in enumerate(items):
            var = tk.BooleanVar(value=True)     # 기본 전체 선택 — 빼는 게 사람의 판단
            self.vars[lab] = var
            row = ttk.Frame(self.inner)
            row.grid(row=i, column=0, sticky="w", pady=1)
            ttk.Checkbutton(row, text=f"{lab}  {text}", variable=var,
                            command=self._count).grid(row=0, column=0, sticky="w")
            if note:
                ttk.Label(row, text=note, foreground="#888").grid(row=0, column=1,
                                                                  padx=(8, 0))
        self.b_confirm.config(state="normal")
        self._count()
        self.canvas.yview_moveto(0)

    def _all(self, on: bool) -> None:
        for v in self.vars.values():
            v.set(on)
        self._count()

    def _count(self) -> None:
        n = sum(1 for v in self.vars.values() if v.get())
        self.l_pick.config(text=f"{n} / {len(self.vars)} 선택")

    # ── 동작 ────────────────────────────────────────────────────────────────
    def _snapshot(self) -> dict:
        """화면 값을 «메인 스레드에서» 사본으로 뜬다.

        tkinter 변수를 작업 스레드에서 읽으면 간헐적으로 깨진다. 그래서 값은
        여기서만 읽고, 작업 스레드에는 평범한 dict 만 넘긴다.
        """
        return {
            "path": self.v_input.get().strip(),
            "kind": self.v_kind.get(),
            "title": self.v_title.get().strip(),
            "date": self.v_date.get().strip(),
            "send": self.v_send.get(),
            "notion": self.v_notion.get(),
        }

    @staticmethod
    def _apply(form: dict) -> None:
        """사본을 CFG 에 반영한다. `.env` 파일은 건드리지 않는다 — 이번 실행만."""
        reload_env()
        CFG.input_audio = form["path"] if form["kind"] == "audio" else ""
        CFG.input_transcript = form["path"] if form["kind"] == "transcript" else ""
        CFG.meeting_title = form["title"]
        CFG.meeting_date = form["date"]
        if not form["send"]:
            CFG.send = ""
        if not form["notion"]:
            CFG.notion_mode = ""
        CFG.ensure_dirs()

    def _check(self) -> None:
        form = self._snapshot()

        def job() -> str:
            from .pipeline import do_check

            self._apply(form)
            do_check()
            return "확인 완료"

        self.q.put(("state", "확인 중"))
        self._work(job)

    def _start(self) -> None:
        if not self.v_input.get().strip():
            messagebox.showwarning("입력 없음", "녹취록 또는 오디오 파일을 고르세요.")
            return
        auto = self.v_mode.get() == "auto"
        if auto and not messagebox.askokcancel(
            "오토 모드",
            "검수 없이 전부 반영하고 업로드합니다.\n"
            "확인이 필요한 항목은 회의록 안에 «확인 필요» 로 남습니다.\n\n계속할까요?",
        ):
            return

        self.b_confirm.config(state="disabled")
        self.log.delete("1.0", "end")

        form = self._snapshot()

        def job():
            from .pipeline import do_confirm, do_extract, do_notion, do_send, resolve_input

            self._apply(form)
            self.q.put(("state", "입력 준비"))
            tr_path, source = resolve_input()

            text = tr_path.read_text(encoding="utf-8")
            if not text.strip():
                raise SystemExit(f"녹취록이 비어 있습니다: {tr_path}")
            print(f"[녹취록] {len(text)}자  {tr_path.name}")

            self.q.put(("state", f"추출 중 ({CFG.cli_model}/{CFG.cli_effort}) — 몇 분 걸립니다"))
            res = do_extract(tr_path, text, source)
            print()
            print(res.report())
            if not res.ok:
                raise SystemExit("추출에 실패했습니다. 위 원인 후보를 확인하세요.")

            self.draft_path = res.draft_path
            if not auto:
                #  여기서 «멈춘다». 확정은 사람이 «확정 후 업로드» 를 누를 때 일어난다.
                self.q.put(("review", res.bundle))
                self.q.put(("log", "\n검수 모드입니다. 아래에서 항목을 고르고 «확정 후 업로드» 를 누르세요.\n"))
                return "검수 대기"

            self.q.put(("state", "확정·업로드"))
            final, out = do_confirm(res.bundle, "all")
            print()
            do_send(final, out)
            print()
            do_notion(final, out)
            return "업로드 완료"

        self._work(job)

    def _confirm(self) -> None:
        if self.bundle is None:
            messagebox.showinfo("검수 대상 없음", "먼저 실행해서 추출부터 하세요.")
            return
        picked = [lab for lab, v in self.vars.items() if v.get()]
        if not picked and self.vars:
            if not messagebox.askokcancel(
                "선택 없음", "고른 항목이 없습니다. 논의 내용만으로 만들까요?"
            ):
                return

        form = self._snapshot()
        bundle = self.bundle
        spec = ",".join(picked) if picked else "all"

        def job() -> str:
            from .pipeline import do_confirm, do_notion, do_send

            self._apply(form)
            self.q.put(("state", "확정·업로드"))
            final, out = do_confirm(bundle, spec)
            print()
            do_send(final, out)
            print()
            do_notion(final, out)
            return "업로드 완료"

        self._work(job)


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
