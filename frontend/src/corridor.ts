import L from "leaflet";
import { RISK_STYLES } from "./routes";
import type { CorridorHour, RiskCategory, Route } from "./routes";

export function corridorSections(route: Route, positions: L.LatLngTuple[]): L.LatLngTuple[][] {
  if (positions.length < 2 || route.distance_m <= 0) return [];
  const distances = [0];
  for (let index = 1; index < positions.length; index += 1) {
    distances.push(distances[index - 1] + L.latLng(positions[index - 1]).distanceTo(positions[index]));
  }
  const geometryDistance = distances[distances.length - 1];
  if (geometryDistance === 0) return [];
  let edge = 1;
  let previous = positions[0];
  return route.checkpoints.slice(1).map((checkpoint) => {
    const target = geometryDistance * (checkpoint.distance_from_origin_m / route.distance_m);
    const section = [previous];
    while (edge < positions.length - 1 && distances[edge] < target) {
      section.push(positions[edge]);
      edge += 1;
    }
    const [startLat, startLon] = positions[edge - 1];
    const [endLat, endLon] = positions[edge];
    const length = distances[edge] - distances[edge - 1];
    const progress = length ? Math.min(1, Math.max(0, (target - distances[edge - 1]) / length)) : 0;
    previous = [startLat + progress * (endLat - startLat), startLon + progress * (endLon - startLon)];
    section.push(previous);
    return section;
  });
}

export function corridorCategory(hour: CorridorHour | undefined, segment: number): RiskCategory | "unknown" {
  const start = hour?.checkpoint_risk_categories[segment];
  const end = hour?.checkpoint_risk_categories[segment + 1];
  if (start === "no_travel" || end === "no_travel") return "no_travel";
  if (!start || !end) return "unknown";
  return RISK_STYLES[start].index >= RISK_STYLES[end].index ? start : end;
}
