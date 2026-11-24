import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { routeStyle } from "./routes";
import type { Coordinates, Route } from "./routes";

type Props = {
  routes: Route[];
  selectedRoute: Route | undefined;
  checkpointIndex: number;
  onCheckpointSelect: (index: number) => void;
};

function longitudeNear(longitude: number, reference: number): number {
  return longitude + 360 * Math.round((reference - longitude) / 360);
}

function routePositions(geometry: Coordinates[], reference: number): L.LatLngTuple[] {
  return geometry.map(({ latitude, longitude }) => {
    reference = longitudeNear(longitude, reference);
    return [latitude, reference];
  });
}

function fitRoutes(map: L.Map, bounds: L.LatLngBounds | null): void {
  if (bounds?.isValid()) {
    map.fitBounds(bounds, { padding: [32, 32], maxZoom: 13, animate: false });
  } else {
    map.setView([38, -101], 4, { animate: false });
  }
}

export default function RouteMap({ routes, selectedRoute, checkpointIndex, onCheckpointSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const bounds = useRef<L.LatLngBounds | null>(null);
  const markers = useRef<L.CircleMarker[]>([]);
  const routeLines = useRef(new Map<string, L.Polyline>());
  const [tilesUnavailable, setTilesUnavailable] = useState(false);

  useEffect(() => {
    if (!container.current) return;
    const instance = L.map(container.current, { scrollWheelZoom: false });
    map.current = instance;
    instance.attributionControl.setPrefix(false);
    fitRoutes(instance, null);

    let loadedTiles = 0;
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    })
      .on("loading", () => { loadedTiles = 0; })
      .on("tileload", () => { loadedTiles += 1; setTilesUnavailable(false); })
      .on("tileerror", () => { if (loadedTiles === 0) setTilesUnavailable(true); })
      .addTo(instance);
    L.control.scale({ imperial: false }).addTo(instance);
    const resizeObserver = new ResizeObserver(() => instance.invalidateSize({ pan: false }));
    resizeObserver.observe(container.current);

    return () => {
      resizeObserver.disconnect();
      instance.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    if (!map.current) return;
    const instance = map.current;
    const group = L.featureGroup().addTo(instance);
    const reference = routes[0]?.geometry[0]?.longitude ?? 0;
    const lines = new Map<string, L.Polyline>();
    for (const [index, route] of routes.entries()) {
      const positions = routePositions(route.geometry, reference);
      const style = routeStyle(index);
      L.polyline(positions, { color: "#fff", weight: 8, opacity: 0.92, interactive: false }).addTo(group);
      const line = L.polyline(positions, { color: style.color, dashArray: style.dashes, interactive: false }).addTo(group);
      lines.set(route.route_id, line);
    }
    bounds.current = group.getBounds();
    routeLines.current = lines;
    fitRoutes(instance, bounds.current);
    return () => { group.remove(); };
  }, [routes]);

  useEffect(() => {
    if (!map.current) return;
    const group = L.layerGroup().addTo(map.current);
    for (const [id, line] of routeLines.current) {
      const active = id === selectedRoute?.route_id;
      line.setStyle({ weight: active ? 5 : 3, opacity: active ? 1 : 0.75 });
      if (active) line.bringToFront();
    }

    const reference = routes[0]?.geometry[0]?.longitude ?? 0;
    let previousLongitude = reference;
    markers.current = selectedRoute?.checkpoints.map((checkpoint, index) => {
      const { latitude, longitude } = checkpoint.position;
      previousLongitude = longitudeNear(longitude, previousLongitude);
      return L.circleMarker([latitude, previousLongitude], { weight: 2, fillOpacity: 1 })
        .bindTooltip(`Checkpoint ${index + 1}`)
        .on("click", () => onCheckpointSelect(index))
        .addTo(group);
    }) ?? [];

    return () => { group.remove(); };
  }, [routes, selectedRoute, onCheckpointSelect]);

  useEffect(() => {
    markers.current.forEach((marker, index) => {
      const current = index === checkpointIndex;
      marker.setRadius(current ? 8 : 4);
      marker.setStyle({ color: current ? "#fff" : "#175444", fillColor: current ? "#143e33" : "#ecfff7" });
      if (current) marker.bringToFront();
    });
  }, [routes, selectedRoute, checkpointIndex]);

  return (
    <section className="panel map-panel" aria-labelledby="map-title">
      <div className="panel-heading">
        <div>
          <h2 id="map-title">Route map</h2>
          <p>{routes.length ? `${routes.length} route ${routes.length === 1 ? "option" : "options"}` : "Routes and weather checkpoints will appear here."}</p>
        </div>
        <button className="quiet-button fit-button" type="button" disabled={!routes.length}
          onClick={() => { if (map.current) fitRoutes(map.current, bounds.current); }}>
          Fit routes
        </button>
      </div>
      <div className="map-frame">
        <div ref={container} className="map" role="region" aria-label="Interactive map of truck routes" />
        {tilesUnavailable && <p className="map-message" role="status">Base map unavailable. Route lines and checkpoints can still be displayed.</p>}
      </div>
      <div className="map-footer">
        {routes.length > 0 && <span>Routing by <a href="https://www.tomtom.com/">TomTom</a></span>}
        <a href="https://www.openstreetmap.org/fixthemap">Report a map issue</a>
      </div>
    </section>
  );
}
