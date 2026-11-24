"""Rank complete, travel-eligible routes by their distance exposed to weather risk."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum

from .checkpoints import METERS_PER_MILE
from .routing_models import RiskCategory, Route
from .weather_risk import CheckpointAssessment


@dataclass(frozen=True, slots=True)
class RouteAssessment:
    route: Route
    checkpoints: tuple[CheckpointAssessment, ...]
    scored_checkpoints: int
    risk_category: RiskCategory | None
    severe_miles: float | None
    high_miles: float | None
    no_travel_miles: float | None
    average_risk_score: float | None

    @property
    def weather_coverage(self) -> str:
        if self.scored_checkpoints and self.scored_checkpoints == len(self.checkpoints):
            return "complete"
        return "partial" if self.scored_checkpoints else "unavailable"

    @property
    def eligible(self) -> bool:
        return self.weather_coverage == "complete" and self.risk_category != RiskCategory.NO_TRAVEL


def rank_routes(
    routes: Sequence[Route],
    checkpoints: Sequence[tuple[CheckpointAssessment, ...]],
) -> list[RouteAssessment]:
    """Return eligible routes in recommendation order before unranked routes."""
    eligible = []
    unranked = []
    for route, assessments in zip(routes, checkpoints, strict=True):
        assessment = _summarize(route, assessments)
        (eligible if assessment.eligible else unranked).append(assessment)
    eligible.sort(key=lambda item: (item.severe_miles, item.high_miles, item.average_risk_score, item.route.duration_s))
    return eligible + unranked


def _summarize(route: Route, checkpoints: tuple[CheckpointAssessment, ...]) -> RouteAssessment:
    categories = [point.risk.category if point.risk else None for point in checkpoints]
    known = [category for category in categories if category is not None]
    complete = bool(known) and len(known) == len(checkpoints)
    peak = max(known, default=None)
    if not complete and peak != RiskCategory.NO_TRAVEL:
        return RouteAssessment(route, checkpoints, len(known), None, None, None, None, None)

    distances = {category: [] for category in RiskCategory}
    for index, (left, right) in enumerate(zip(checkpoints, checkpoints[1:])):
        start, end = categories[index:index + 2]
        if RiskCategory.NO_TRAVEL in (start, end):
            category = RiskCategory.NO_TRAVEL
        elif start is not None and end is not None:
            category = max(start, end)
        else:
            continue
        distances[category].append(right.checkpoint.distance_from_origin_m - left.checkpoint.distance_from_origin_m)

    totals = {category: fsum(parts) for category, parts in distances.items()}
    no_travel = round(totals[RiskCategory.NO_TRAVEL] / METERS_PER_MILE, 9)
    if not complete:
        return RouteAssessment(route, checkpoints, len(known), peak, None, None, no_travel, None)

    distance = fsum(totals.values())
    average = fsum(category * 25 * total for category, total in totals.items()) / distance if distance else peak * 25
    return RouteAssessment(
        route, checkpoints, len(known), peak,
        round(totals[RiskCategory.SEVERE] / METERS_PER_MILE, 9),
        round(totals[RiskCategory.HIGH] / METERS_PER_MILE, 9),
        no_travel, average,
    )
