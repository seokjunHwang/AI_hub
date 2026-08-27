"""회의록 구조 정의. Claude 의 structured output 스키마로 그대로 쓴다."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# 빈칸의 «이유» 를 구분한다.
#   stated      회의에서 명시됐다
#   not_stated  회의에서 정하지 않았다        (= 미확인. 전사를 다 봤고 없었다)
#   unclear     전사가 불확실해 확인 못 했다   (= 미조사. 원본 오디오를 다시 들어야 한다)
# 둘을 «미정» 하나로 뭉개면 받는 쪽이 이미 있는 답을 다시 찾는다.
BlankReason = Literal["stated", "not_stated", "unclear"]

# 빈칸 표시 문구의 «정본». review 출력 · markdown · HTML 이 모두 여기를 읽는다.
# 세 곳에 각자 적으면 반드시 갈라진다.
BLANK_LABEL: dict[str, str] = {
    "not_stated": "회의에서 안 정해짐",
    "unclear": "녹취 불확실 · 오디오 재확인",
}


class ActionItem(BaseModel):
    task: str = Field(description="해야 할 일. 동사로 시작하는 한 문장")
    owner: Optional[str] = Field(
        default=None,
        description="담당자 이름. 회의에서 명시되지 않았으면 null. 추측하지 말 것",
    )
    owner_status: BlankReason = Field(
        default="not_stated",
        description=(
            "owner 가 비어 있는 이유. "
            "stated=명시됨 / not_stated=회의에서 정하지 않음 / unclear=전사가 불확실해 확인 불가"
        ),
    )
    due: Optional[str] = Field(
        default=None,
        description="마감일 YYYY-MM-DD. 명시되지 않았으면 null. '다음 주' 같은 표현은 quote 에 남기고 null",
    )
    due_status: BlankReason = Field(
        default="not_stated", description="due 가 비어 있는 이유. owner_status 와 같은 기준"
    )
    priority: Literal["high", "medium", "low", "unknown"] = "unknown"
    topic: Optional[str] = Field(
        default=None,
        description=(
            "이 항목이 나온 논의 주제. topics[].title 과 «글자 그대로 같은» 문자열이어야 한다. "
            "어느 주제에도 속하지 않으면 그 주제를 topics 에 추가하거나, "
            "회의 산출물이 아닌 잡담이면 이 항목을 추출하지 않는다"
        ),
    )
    quote: str = Field(description="근거가 된 발언 원문 (transcript 에서 그대로 인용)")
    timestamp: Optional[str] = Field(default=None, description="발언 시각 HH:MM:SS")

    @model_validator(mode="after")
    def _sync_status(self) -> "ActionItem":
        """값이 있으면 status 를 stated 로 강제한다.

        모델이 owner 는 채우고 owner_status 는 기본값으로 두는 경우가 있다.
        그대로 두면 «담당자가 있는데 미정 1건» 이라는 거짓 경고가 뜨고,
        오탐은 정탐 실패보다 비싸다 — 사람이 경고 자체를 무시하게 된다.
        """
        if self.owner and self.owner_status != "stated":
            self.owner_status = "stated"
        if self.due and self.due_status != "stated":
            self.due_status = "stated"
        return self

    @property
    def owner_display(self) -> str:
        return self.owner or BLANK_LABEL.get(self.owner_status, "미정")

    @property
    def due_display(self) -> str:
        return self.due or BLANK_LABEL.get(self.due_status, "미정")

    @property
    def needs_human(self) -> bool:
        """사람이 손대야 하는 항목인가."""
        return self.owner_status != "stated" or self.due_status != "stated"


class Decision(BaseModel):
    decision: str = Field(description="확정된 결정사항 한 문장")
    rationale: Optional[str] = Field(default=None, description="왜 그렇게 결정했는가")
    alternatives: List[str] = Field(
        default_factory=list, description="검토했지만 채택하지 않은 대안"
    )
    topic: Optional[str] = Field(
        default=None,
        description=(
            "이 항목이 나온 논의 주제. topics[].title 과 «글자 그대로 같은» 문자열이어야 한다. "
            "어느 주제에도 속하지 않으면 그 주제를 topics 에 추가하거나, "
            "회의 산출물이 아닌 잡담이면 이 항목을 추출하지 않는다"
        ),
    )
    quote: str = Field(description="근거 발언 원문")
    timestamp: Optional[str] = None


class OpenQuestion(BaseModel):
    question: str = Field(description="회의에서 결론이 안 난 쟁점 또는 미확인 사항")
    blocker: bool = Field(default=False, description="이게 막히면 다른 일이 못 나가는가")
    who_should_answer: Optional[str] = None
    topic: Optional[str] = Field(
        default=None,
        description=(
            "이 항목이 나온 논의 주제. topics[].title 과 «글자 그대로 같은» 문자열이어야 한다. "
            "어느 주제에도 속하지 않으면 그 주제를 topics 에 추가하거나, "
            "회의 산출물이 아닌 잡담이면 이 항목을 추출하지 않는다"
        ),
    )



class Topic(BaseModel):
    title: str = Field(description="논의 주제 제목")
    summary: str = Field(description="해당 주제에서 논의된 내용 요약. 3문장 이내")
    timestamp_start: Optional[str] = None


class Minutes(BaseModel):
    """회의록 전체."""

    title: str = Field(description="회의 제목. 주어지지 않았으면 내용에서 생성")
    date: Optional[str] = Field(default=None, description="회의 날짜 YYYY-MM-DD")
    participants: List[str] = Field(
        default_factory=list,
        description="발언 내용에서 확인되는 참석자. 확실하지 않으면 넣지 말 것",
    )
    one_liner: str = Field(description="이 회의를 한 문장으로. 결론 중심")
    topics: List[Topic] = Field(default_factory=list)
    decisions: List[Decision] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    open_questions: List[OpenQuestion] = Field(default_factory=list)
    unclear_notes: List[str] = Field(
        default_factory=list,
        description="STT 오인식으로 의미가 불확실한 구간. 사람이 확인해야 할 지점",
    )

    @property
    def topic_titles(self) -> List[str]:
        """topic 연결이 유효한지 확인할 때 쓰는 정본 목록."""
        return [t.title for t in self.topics]

    def items_by_topic(self, title: str) -> List[tuple]:
        """이 주제에서 나온 결정·액션·미결. (라벨, 내용) 목록.

        표에서 논의로 가는 링크만 있으면 반쪽이다. 논의를 읽는 사람도
        "이 얘기가 뭐로 이어졌지" 를 되짚을 수 있어야 한다.
        """
        out: List[tuple] = []
        for kind, items, text in (
            ("D", self.decisions, lambda x: x.decision),
            ("A", self.action_items, lambda x: x.task),
            ("Q", self.open_questions, lambda x: x.question),
        ):
            for i, it in enumerate(items, 1):
                if it.topic == title:
                    out.append((f"{kind}{i}", text(it)))
        return out


class MinutesBundle(BaseModel):
    """파이프라인 산출물 묶음 (렌더링·업로드용)."""

    minutes: Minutes
    source_audio: Optional[str] = None
    transcript_chars: int = 0
    model: str = ""
    generated_at: str = ""
