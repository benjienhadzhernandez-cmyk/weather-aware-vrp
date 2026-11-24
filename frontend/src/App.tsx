import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import CheckpointDetails from "./CheckpointDetails";
import RouteMap from "./RouteMap";
import type { RouteMapHandle } from "./RouteMap";
import { fetchRecommendations, formatDistance, formatDuration, formatNumber, formatTime, parseCoordinates, routeStyle, tripQuery } from "./routes";
import type { Coordinates, Recommendations, Route, TripFields } from "./routes";

type ConnectionState = "checking" | "connected" | "unavailable";

const EMPTY_ROUTES: Route[] = [];
const INITIAL_FIELDS: TripFields = { origin: "", destination: "", cargo: "", departure: "" };

function endpointCoordinates(value: string): Coordinates | null {
  try {
    return parseCoordinates(value, "Location");
  } catch {
    return null;
  }
}

function ForecastPanel({ route, index, onChange }: { route: Route | undefined; index: number; onChange: (value: number) => void }) {
  const checkpoint = route?.checkpoints[index];

  return (
    <section className="panel forecast-panel" aria-labelledby="forecast-title">
      <div className="panel-heading">
        <div>
          <h2 id="forecast-title">Checkpoint forecast</h2>
          <p>Move along the selected route to see weather at each estimated arrival.</p>
        </div>
      </div>
      {route && checkpoint ? (
        <div className="forecast-content">
          <div className="slider-meta">
            <label htmlFor="checkpoint-slider">Checkpoint {index + 1} of {route.checkpoints.length}</label>
            <span>{formatDistance(checkpoint.distance_from_origin_m)} from origin</span>
          </div>
          <input id="checkpoint-slider" type="range" min="0" max={route.checkpoints.length - 1} value={index}
            disabled={route.checkpoints.length < 2} onChange={(event) => onChange(event.currentTarget.valueAsNumber)}
            aria-valuetext={`Checkpoint ${index + 1} of ${route.checkpoints.length}, ${formatTime(checkpoint.arrival_at)}`} />
          <div className="slider-ends"><span>Origin</span><span>Destination</span></div>
          <CheckpointDetails checkpoint={checkpoint} />
        </div>
      ) : <p className="empty-state">Select a route to explore its forecast.</p>}
    </section>
  );
}

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>("checking");
  const [fields, setFields] = useState<TripFields>(INITIAL_FIELDS);
  const [results, setResults] = useState<Recommendations | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [checkpointIndex, setCheckpointIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const routeMap = useRef<RouteMapHandle>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function checkApi() {
      try {
        const response = await fetch("/api/health/", { signal: controller.signal });
        const body = response.ok ? await response.json() : null;
        if (!controller.signal.aborted) setConnection(body?.status === "ok" ? "connected" : "unavailable");
      } catch {
        if (!controller.signal.aborted) setConnection("unavailable");
      }
    }
    void checkApi();
    return () => { controller.abort(); request.current?.abort(); };
  }, []);

  const routes = results?.routes ?? EMPTY_ROUTES;
  const selectedRoute = routes.find((route) => route.route_id === selectedId);
  const origin = useMemo(() => endpointCoordinates(fields.origin), [fields.origin]);
  const destination = useMemo(() => endpointCoordinates(fields.destination), [fields.destination]);
  const connectionText = { checking: "Checking API", connected: "API connected", unavailable: "API unavailable" }[connection];

  function setField(field: keyof TripFields, value: string) {
    resetComparison();
    setFields((current) => ({ ...current, [field]: value }));
  }

  function resetComparison() {
    request.current?.abort();
    request.current = null;
    setIsLoading(false);
    setError("");
    setResults(null);
    setSelectedId(null);
    setCheckpointIndex(0);
  }

  async function submitTrip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current) return;

    let query: URLSearchParams;
    try {
      query = tripQuery(fields);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Check the trip details.");
      return;
    }

    resetComparison();
    const controller = new AbortController();
    request.current = controller;
    setIsLoading(true);
    try {
      const data = await fetchRecommendations(query, controller.signal);
      if (controller.signal.aborted) return;
      setResults(data);
      setSelectedId(data.recommended_route_id ?? data.routes[0]?.route_id ?? null);
      setConnection("connected");
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error && !(cause instanceof TypeError) ? cause.message : "Cannot reach the route planner API. Try again.");
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (!controller.signal.aborted) setIsLoading(false);
      }
    }
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="RouteWise home">
          <span className="brand-mark" aria-hidden="true">W</span>RouteWise
        </a>
        <span className={`connection connection--${connection}`} role="status">
          <span className="connection-dot" aria-hidden="true" />{connectionText}
        </span>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <h1 id="page-title">Weather-aware truck routing</h1>
        <p className="hero-copy">Compare truck routes by forecast risk, cargo weight, and travel time.</p>
      </section>

      <div className="dashboard">
        <section className="panel trip-panel" aria-labelledby="trip-title">
          <div className="panel-heading">
            <h2 id="trip-title">Trip details</h2>
            <button className="quiet-button" type="button" onClick={() => {
              resetComparison();
              setFields({ origin: "34.05, -118.25", destination: "36.17, -115.14", cargo: "5000", departure: "" });
            }}>Use example</button>
          </div>
          <form className="trip-form" onSubmit={submitTrip} aria-busy={isLoading}>
            <p className="field-hint" id="coordinates-hint">Pick locations on the map or enter latitude and longitude in decimal degrees.</p>
            <label>Origin
              <input type="text" value={fields.origin} placeholder="34.05, -118.25" required
                aria-describedby="coordinates-hint" onChange={(event) => setField("origin", event.currentTarget.value)} />
            </label>
            <label>Destination
              <input type="text" value={fields.destination} placeholder="36.17, -115.14" required
                aria-describedby="coordinates-hint" onChange={(event) => setField("destination", event.currentTarget.value)} />
            </label>
            <label>Cargo weight (kg)
              <input type="number" min="0" step="any" value={fields.cargo} inputMode="decimal" required
                placeholder="5000" onChange={(event) => setField("cargo", event.currentTarget.value)} />
            </label>
            <label>Departure time <span className="optional">Optional</span>
              <input type="datetime-local" value={fields.departure} aria-describedby="departure-hint"
                onChange={(event) => setField("departure", event.currentTarget.value)} />
            </label>
            <p className="field-hint" id="departure-hint">Uses your device’s time zone. Leave blank to depart now.</p>
            <button className="primary-button" type="submit" disabled={isLoading}>{isLoading ? "Comparing routes…" : "Compare routes"}</button>
            {error && <p className="form-error" role="alert">{error}</p>}
          </form>
        </section>

        <RouteMap ref={routeMap} routes={routes} selectedRoute={selectedRoute} checkpointIndex={checkpointIndex}
          onCheckpointSelect={setCheckpointIndex} origin={origin} destination={destination}
          onEndpointPick={(endpoint, position) => setField(endpoint, `${position.latitude.toFixed(6)}, ${position.longitude.toFixed(6)}`)} />

        <section className="panel results-panel" aria-labelledby="results-title">
          <div className="panel-heading"><h2 id="results-title">Route comparison</h2></div>
          {isLoading && <p className="empty-state" role="status">Finding truck routes and checking forecast coverage…</p>}
          {!isLoading && !results && <p className="empty-state">Compare a trip to see distance, travel time, and weather risk.</p>}
          {results?.recommendation_status === "no_routes" && <p className="empty-state" role="status">No eligible truck routes were found for this trip.</p>}
          {results && routes.length > 0 && (
            <div className="route-results">
              <p className="result-trip">{formatTime(results.departure_at)} · {formatNumber(results.cargo_weight_kg)} kg cargo</p>
              {results.recommendation_status === "insufficient_weather" ? (
                <p className="coverage-note" role="status">No recommendation is available because every route has incomplete forecast coverage.</p>
              ) : routes.some((route) => route.rank === null) ? (
                <p className="coverage-note">The recommendation compares routes with complete checkpoint coverage.</p>
              ) : null}
              <ul className="route-options">
                {routes.map((route, index) => (
                  <li key={route.route_id}>
                    <button type="button" className="route-option" aria-pressed={route.route_id === selectedId}
                      onClick={() => {
                        if (route.route_id !== selectedId) {
                          setSelectedId(route.route_id);
                          setCheckpointIndex(0);
                        }
                        routeMap.current?.fitRoute(route.route_id);
                      }}>
                      <span className="route-option-heading">
                        <span className="route-title"><span className="route-swatch" aria-hidden="true"
                          style={{ borderColor: routeStyle(index).color, borderTopStyle: index === 0 ? "solid" : index === 1 ? "dashed" : "dotted" }} />
                          Route {index + 1}</span>
                        {route.route_id === results.recommended_route_id && <span className="badge">Recommended</span>}
                      </span>
                      <span className="route-metrics">
                        <span>{formatDistance(route.distance_m)}</span><span>{formatDuration(route.duration_s)}</span>
                        <span>{route.peak_risk_score == null ? "Unranked" : `Peak risk ${formatNumber(route.peak_risk_score)}/100`}</span>
                      </span>
                      <span className="route-coverage">{route.scored_checkpoints}/{route.checkpoint_count} checkpoints scored
                        {route.mean_risk_score != null && ` · Mean risk ${formatNumber(route.mean_risk_score)}/100`}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <ForecastPanel route={selectedRoute} index={checkpointIndex} onChange={setCheckpointIndex} />
      </div>

      {results && routes.length > 0 && (
        <footer className="footer">
          {results.weather_attribution.description} Forecast data by <a href="https://open-meteo.com/">{results.weather_attribution.name}</a> under <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.
        </footer>
      )}
    </main>
  );
}
