export type Coordinates = { latitude: number; longitude: number };
export type Endpoint = "origin" | "destination";
export type RiskCategory = "low" | "moderate" | "high" | "severe" | "no_travel";
export type CorridorHour = {
  hour_offset: number;
  valid_at: string;
  risk_category: RiskCategory | null;
  scored_checkpoints: number;
  checkpoint_count: number;
  checkpoint_risk_categories: (RiskCategory | null)[];
};

export type Forecast = {
  valid_at: string;
  temperature_c: number | null;
  precipitation_mm_per_h: number | null;
  rain_in_per_h: number | null;
  snowfall_in_per_h: number | null;
  wind_speed_m_per_s: number | null;
  wind_gust_m_per_s: number | null;
  visibility_m: number | null;
};

export type Checkpoint = {
  position: Coordinates;
  distance_from_origin_m: number;
  elapsed_s: number;
  arrival_at: string;
  forecast: Forecast | null;
  risk: { score: number; factors: string[]; category: RiskCategory } | null;
};

export type Route = {
  route_id: string;
  distance_m: number;
  duration_s: number;
  geometry: Coordinates[];
  rank: number | null;
  weather_coverage: "complete" | "partial" | "unavailable";
  assessment_error?: string | null;
  checkpoint_count: number;
  scored_checkpoints: number;
  peak_risk_score: number | null;
  mean_risk_score: number | null;
  risk_category: RiskCategory | null;
  severe_miles: number | null;
  high_miles: number | null;
  no_travel_miles?: number | null;
  average_risk_score: number | null;
  corridor_forecast: CorridorHour[];
  checkpoints: Checkpoint[];
};

export type Recommendations = {
  departure_at: string;
  cargo_weight_kg: number;
  recommended_route_id: string | null;
  recommendation_status: "recommended" | "no_routes" | "insufficient_weather" | "no_travel";
  weather_attribution: { name: string; url: string; license_url: string; description: string };
  routes: Route[];
};

export type TripFields = { origin: string; destination: string; cargo: string; departure: string; interval: string };

const METERS_PER_MILE = 1609.344;
const KG_PER_POUND = 0.45359237;

export const RISK_STYLES = {
  low: { label: "Low", color: "#0f766e", index: 0 },
  moderate: { label: "Moderate", color: "#a16207", index: 1 },
  high: { label: "High", color: "#c2410c", index: 2 },
  severe: { label: "Severe", color: "#b91c1c", index: 3 },
  no_travel: { label: "No Travel", color: "#6b2173", index: 4 },
  unknown: { label: "Unknown", color: "#64748b", index: -1 },
} as const;

export function formatPounds(kilograms: number): string {
  return `${formatNumber(kilograms / KG_PER_POUND)} lb`;
}

const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * 60 * MINUTE_MS;
const WEEK_MS = 7 * DAY_MS;
const NUMBER_FORMAT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short",
});

export const ROUTE_STYLES = [
  { color: "#157863" },
  { color: "#3757ac", dashes: "7 6" },
  { color: "#a75a1d", dashes: "2 6" },
] as const;

export function routeStyle(index: number): { color: string; dashes?: string } {
  return ROUTE_STYLES[index % ROUTE_STYLES.length];
}

export function formatNumber(value: number): string {
  return NUMBER_FORMAT.format(value);
}

export function formatDistance(meters: number): string {
  return `${formatNumber(meters / METERS_PER_MILE)} mi`;
}

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes} min`;
}

export function formatTime(value: string): string {
  return TIME_FORMAT.format(new Date(value));
}

export function parseCoordinates(value: string, label: string): Coordinates {
  const parts = value.split(",").map((part) => part.trim());
  const [latitude, longitude] = parts.map(Number);
  if (parts.length !== 2 || !parts.every((part) => DECIMAL.test(part)) ||
      Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
    throw new Error(`${label} must be latitude, longitude in decimal degrees.`);
  }
  return { latitude, longitude };
}

function localDateTime(timestamp: number): string {
  const offset = new Date(timestamp).getTimezoneOffset();
  return new Date(timestamp - offset * MINUTE_MS).toISOString().slice(0, 16);
}

function parseDeparture(value: string): Date {
  const departure = new Date(value);
  const timestamp = departure.getTime();
  if (!Number.isFinite(timestamp) || localDateTime(timestamp) !== value) {
    throw new Error("Choose a valid local departure time.");
  }

  const offset = departure.getTimezoneOffset();
  for (const nearby of [timestamp - DAY_MS, timestamp + DAY_MS]) {
    const offsetDifference = new Date(nearby).getTimezoneOffset() - offset;
    if (offsetDifference && localDateTime(timestamp + offsetDifference * MINUTE_MS) === value) {
      throw new Error("That local time occurs twice when the clocks move back. Choose a different departure time.");
    }
  }
  const now = Date.now();
  if (timestamp < now - MINUTE_MS || timestamp > now + WEEK_MS) {
    throw new Error("Choose a departure within the next seven days.");
  }
  return departure;
}

export function tripQuery(fields: TripFields): URLSearchParams {
  const origin = parseCoordinates(fields.origin, "Origin");
  const destination = parseCoordinates(fields.destination, "Destination");
  if (origin.latitude === destination.latitude && origin.longitude === destination.longitude) {
    throw new Error("Origin and destination must be different.");
  }
  const cargo = Number(fields.cargo);
  if (!fields.cargo.trim() || !Number.isFinite(cargo) || cargo < 0) {
    throw new Error("Enter a cargo weight of 0 lb or more.");
  }

  const query = new URLSearchParams({
    origin: `${origin.latitude},${origin.longitude}`,
    destination: `${destination.latitude},${destination.longitude}`,
    cargo_weight_kg: String(cargo * KG_PER_POUND),
    interval_miles: fields.interval,
  });
  if (fields.departure) {
    query.set("departure_at", parseDeparture(fields.departure).toISOString());
  }
  return query;
}

export async function fetchRecommendations(query: URLSearchParams, signal: AbortSignal): Promise<Recommendations> {
  const response = await fetch(`/api/recommendations/?${query}`, { signal });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = typeof body?.error === "string" ? body.error : "Route comparison is temporarily unavailable.";
    throw new Error(message);
  }
  if (!body || !Array.isArray(body.routes) || !body.weather_attribution) {
    throw new Error("The API returned an invalid route response.");
  }
  return body;
}
