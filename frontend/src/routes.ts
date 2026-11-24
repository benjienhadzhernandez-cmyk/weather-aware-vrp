export type Coordinates = { latitude: number; longitude: number };

export type Forecast = {
  valid_at: string;
  temperature_c: number | null;
  precipitation_mm_per_h: number | null;
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
  risk: { score: number; factors: string[] } | null;
};

export type Route = {
  route_id: string;
  distance_m: number;
  duration_s: number;
  geometry: Coordinates[];
  rank: number | null;
  weather_coverage: "complete" | "partial" | "unavailable";
  checkpoint_count: number;
  scored_checkpoints: number;
  peak_risk_score: number | null;
  mean_risk_score: number | null;
  checkpoints: Checkpoint[];
};

export type Recommendations = {
  departure_at: string;
  cargo_weight_kg: number;
  recommended_route_id: string | null;
  recommendation_status: "recommended" | "no_routes" | "insufficient_weather";
  weather_attribution: { name: string; url: string; license_url: string; description: string };
  routes: Route[];
};

export type TripFields = { origin: string; destination: string; cargo: string; departure: string };

const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
const MINUTE_MS = 60 * 1000;
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
  return `${formatNumber(meters / 1000)} km`;
}

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${minutes % 60}m` : `${minutes} min`;
}

export function formatTime(value: string): string {
  return TIME_FORMAT.format(new Date(value));
}

function parseCoordinates(value: string, label: string): Coordinates {
  const parts = value.split(",").map((part) => part.trim());
  const [latitude, longitude] = parts.map(Number);
  if (parts.length !== 2 || !parts.every((part) => DECIMAL.test(part)) ||
      Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
    throw new Error(`${label} must be latitude, longitude in decimal degrees.`);
  }
  return { latitude, longitude };
}

export function tripQuery(fields: TripFields): URLSearchParams {
  const origin = parseCoordinates(fields.origin, "Origin");
  const destination = parseCoordinates(fields.destination, "Destination");
  if (origin.latitude === destination.latitude && origin.longitude === destination.longitude) {
    throw new Error("Origin and destination must be different.");
  }
  const cargo = Number(fields.cargo);
  if (!fields.cargo.trim() || !Number.isFinite(cargo) || cargo < 0) {
    throw new Error("Enter a cargo weight of 0 kg or more.");
  }

  const query = new URLSearchParams({
    origin: `${origin.latitude},${origin.longitude}`,
    destination: `${destination.latitude},${destination.longitude}`,
    cargo_weight_kg: String(cargo),
  });
  if (fields.departure) {
    const departure = new Date(fields.departure);
    const timestamp = departure.getTime();
    const localIso = Number.isNaN(timestamp) ? "" :
      new Date(timestamp - departure.getTimezoneOffset() * MINUTE_MS).toISOString().slice(0, 16);
    if (localIso !== fields.departure) {
      throw new Error("Choose a valid local departure time.");
    }
    const now = Date.now();
    if (timestamp < now - MINUTE_MS || timestamp > now + WEEK_MS) {
      throw new Error("Choose a departure within the next seven days.");
    }
    query.set("departure_at", departure.toISOString());
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
