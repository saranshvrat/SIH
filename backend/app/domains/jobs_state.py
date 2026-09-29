"""
Job state machine (master plan §10.1).

The design rule that makes the demo robust: ONLY ESSENTIAL STAGES CAN FAIL THE JOB.
If classification, metrics, uncertainty or analysis break at 4am, the user still gets a
super-resolved image with an honest "this part was skipped" note.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Stage(StrEnum):
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    PREPROCESSING = "PREPROCESSING"
    SCENE_ANALYSIS = "SCENE_ANALYSIS"
    MODEL_SELECTION = "MODEL_SELECTION"
    INFERENCE = "INFERENCE"
    QUALITY_ASSESSMENT = "QUALITY_ASSESSMENT"
    UNCERTAINTY = "UNCERTAINTY"
    POST_PROCESSING = "POST_PROCESSING"
    ANALYSIS = "ANALYSIS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


PIPELINE: list[Stage] = [
    Stage.QUEUED, Stage.DOWNLOADING, Stage.PREPROCESSING, Stage.SCENE_ANALYSIS,
    Stage.MODEL_SELECTION, Stage.INFERENCE, Stage.QUALITY_ASSESSMENT,
    Stage.UNCERTAINTY, Stage.POST_PROCESSING, Stage.ANALYSIS,
]

# Stages whose failure aborts the job. Everything else degrades with a warning.
ESSENTIAL: set[Stage] = {Stage.DOWNLOADING, Stage.PREPROCESSING, Stage.INFERENCE}

TERMINAL: set[Stage] = {Stage.COMPLETED, Stage.FAILED, Stage.CANCELLED}

# Fraction of total progress each stage occupies. Inference dominates deliberately so
# the bar moves believably during the long wait.
WEIGHTS: dict[Stage, float] = {
    Stage.QUEUED: 0.01, Stage.DOWNLOADING: 0.14, Stage.PREPROCESSING: 0.10,
    Stage.SCENE_ANALYSIS: 0.04, Stage.MODEL_SELECTION: 0.01, Stage.INFERENCE: 0.45,
    Stage.QUALITY_ASSESSMENT: 0.10, Stage.UNCERTAINTY: 0.06,
    Stage.POST_PROCESSING: 0.05, Stage.ANALYSIS: 0.04,
}

SKIP_REASONS = {
    "UPLOADED_SOURCE": "This scene was uploaded, so no download was needed.",
    "NOT_REQUESTED": "Not requested for this job.",
    "CLASSIFICATION_FAILED": "Scene classification could not be completed; a general-purpose model was used.",
    "TOO_CLOUDY": "Too much cloud cover for reliable scene classification.",
    "USER_SELECTED_MODELS": "Models were chosen manually, so automatic selection was not needed.",
    "METRICS_DISABLED": "Quality assessment was turned off for this job.",
    "NO_REFERENCE": "No high-resolution reference exists for this area, so full-reference metrics cannot be computed.",
    "MODEL_DOES_NOT_SUPPORT_UNCERTAINTY": "The selected model does not provide a per-pixel uncertainty estimate.",
    "NEEDS_MULTIPLE_RUNS": "Uncertainty from model disagreement needs at least two model outputs.",
    "REQUIRED_BANDS_MISSING": "The bands needed for this step are not present in the output.",
    "NO_ANALYSES_REQUESTED": "No geospatial analysis was requested.",
}


@dataclass
class StageState:
    name: str
    status: str = "pending"          # pending | running | done | skipped | failed
    progress: float = 0.0
    reason: str | None = None
    detail: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_s: float | None = None

    def to_dict(self) -> dict:
        d = {"name": self.name, "status": self.status}
        for k in ("progress", "reason", "detail", "started_at", "ended_at", "duration_s"):
            v = getattr(self, k)
            if v not in (None, 0.0) or (k == "progress" and self.status == "running"):
                d[k] = v
        if self.reason:
            d["reason_text"] = SKIP_REASONS.get(self.reason, self.reason)
        return d


@dataclass
class JobState:
    stages: list[StageState] = field(default_factory=lambda: [StageState(s.value) for s in PIPELINE])

    def get(self, stage: Stage) -> StageState:
        for s in self.stages:
            if s.name == stage.value:
                return s
        raise KeyError(stage)

    def overall_progress(self) -> float:
        total = 0.0
        for st in self.stages:
            w = WEIGHTS.get(Stage(st.name), 0.0)
            if st.status in ("done", "skipped"):
                total += w
            elif st.status == "running":
                total += w * max(0.0, min(1.0, st.progress))
        return round(min(total, 1.0), 4)

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.stages]

    @classmethod
    def from_list(cls, data: list[dict]) -> "JobState":
        st = cls()
        by_name = {s["name"]: s for s in (data or [])}
        for s in st.stages:
            raw = by_name.get(s.name)
            if raw:
                for k, v in raw.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
        return st


def is_fatal(stage: Stage) -> bool:
    return stage in ESSENTIAL
