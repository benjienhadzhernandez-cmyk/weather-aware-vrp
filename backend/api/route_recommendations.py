"""Rank truck routes whose sampled weather risk is fully known."""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from .routing_models import Route
from .weather_risk import CheckpointAssessment


@dataclass(frozen=True, slots=True)
class RouteAssessment:
    route: Route
    checkpoints: tuple[CheckpointAssessment, ...]
    scored_checkpoints: int
    peak_risk_score: float | None
    mean_risk_score: float | None

    @property
    def weather_coverage(self) -> str:
        if self.peak_risk_score is not None:
            return "complete"
        return "partial" if self.scored_checkpoints else "unavailable"


def rank_routes(
    routes: Sequence[Route],
    checkpoints: Sequence[tuple[CheckpointAssessment, ...]],
) -> list[RouteAssessment]:
    """Return eligible routes ordered by risk and duration, then unranked routes."""
    eligible = []
    unranked = []
    for route, assessments in zip(routes, checkpoints, strict=True):
        scores = [checkpoint.risk.score for checkpoint in assessments if checkpoint.risk is not None]
        if scores and len(scores) == len(assessments):
            eligible.append(RouteAssessment(route, assessments, len(scores), max(scores), fmean(scores)))
        else:
            unranked.append(RouteAssessment(route, assessments, len(scores), None, None))

    eligible.sort(key=lambda item: (item.peak_risk_score, item.mean_risk_score, item.route.duration_s))
    return eligible + unranked
