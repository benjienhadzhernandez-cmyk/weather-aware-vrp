import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import type { Ref } from "react";
import { createPortal } from "react-dom";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import CheckpointDetails from "./CheckpointDetails";
import { routeStyle } from "./routes";
import type { Coordinates, Endpoint, Route } from "./routes";

export type RouteMapHandle = { fitRoute: (routeId: string) => void };

type Props = {
  ref: Ref<RouteMapHandle>;
  routes: Route[];
  selectedRoute: Route | undefined;
  checkpointIndex: number;
  onCheckpointSelect: (index: number) => void;
  origin: Coordinates | null;
  destination: Coordinates | null;
  onEndpointPick: (endpoint: Endpoint, position: Coordinates) => void;
};

type CheckpointPopup = {
  route: Route;
  index: number;
  position: L.LatLng;
  content: HTMLElement;
  keyboardTrigger: SVGElement | null;
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

export default function RouteMap({ ref, routes, selectedRoute, checkpointIndex, onCheckpointSelect, origin, destination, onEndpointPick }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const bounds = useRef<L.LatLngBounds | null>(null);
  const markers = useRef<L.CircleMarker[]>([]);
  const routeLines = useRef(new Map<string, L.Polyline>());
  const [tilesUnavailable, setTilesUnavailable] = useState(false);
  const [picking, setPicking] = useState<Endpoint | null>(null);
  const [pickStatus, setPickStatus] = useState("");
  const [checkpointPopup, setCheckpointPopup] = useState<CheckpointPopup | null>(null);
  const activePopup = !picking && checkpointPopup && checkpointPopup.route === selectedRoute && checkpointPopup.index === checkpointIndex
    ? checkpointPopup : null;

  useImperativeHandle(ref, () => ({
    fitRoute(routeId) {
      const routeBounds = routeLines.current.get(routeId)?.getBounds();
      if (map.current && routeBounds?.isValid()) fitRoutes(map.current, routeBounds);
    },
  }), []);

  useEffect(() => {
    if (!container.current) return;
    const instance = L.map(container.current, { scrollWheelZoom: true });
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
    if (bounds.current.isValid()) fitRoutes(instance, bounds.current);
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
      const marker = L.circleMarker([latitude, previousLongitude], {
        weight: 2, fillOpacity: 1, bubblingMouseEvents: Boolean(picking),
      })
        .bindTooltip(`Checkpoint ${index + 1}`)
        .addTo(group);
      if (!picking) {
        const openPopup = (keyboardTrigger: SVGElement | null) => {
          onCheckpointSelect(index);
          setCheckpointPopup({ route: selectedRoute, index, position: marker.getLatLng(), content: document.createElement("div"), keyboardTrigger });
        };
        marker.on("click", () => openPopup(null));
        const element = marker.getElement();
        if (!(element instanceof SVGElement)) return marker;
        element.setAttribute("role", "button");
        element.setAttribute("tabindex", "0");
        element.setAttribute("aria-label", `Checkpoint ${index + 1} forecast`);
        element.addEventListener("keydown", (event) => {
          if (!(event instanceof KeyboardEvent) || (event.key !== "Enter" && event.key !== " ")) return;
          event.preventDefault();
          event.stopPropagation();
          openPopup(element);
        });
      }
      return marker;
    }) ?? [];

    return () => { group.remove(); };
  }, [routes, selectedRoute, onCheckpointSelect, picking]);

  useEffect(() => {
    markers.current.forEach((marker, index) => {
      const current = index === checkpointIndex;
      marker.setRadius(current ? 8 : 4);
      marker.setStyle({ color: current ? "#fff" : "#175444", fillColor: current ? "#143e33" : "#ecfff7" });
      if (current) marker.bringToFront();
    });
  }, [routes, selectedRoute, checkpointIndex, picking]);

  useLayoutEffect(() => {
    const instance = map.current;
    if (!instance || !activePopup) return;
    const popup = L.popup({
      className: "checkpoint-popup",
      maxWidth: Math.min(285, instance.getSize().x - 48),
      maxHeight: Math.min(250, instance.getSize().y - 70),
      autoPanPadding: [14, 14],
    }).setLatLng(activePopup.position).setContent(activePopup.content);
    const handleClose = (event: L.PopupEvent) => {
      if (event.popup !== popup) return;
      setCheckpointPopup((current) => current === activePopup ? null : current);
      if (activePopup.keyboardTrigger?.isConnected) activePopup.keyboardTrigger.focus({ preventScroll: true });
    };
    instance.on("popupclose", handleClose);
    popup.openOn(instance);
    const element = popup.getElement();
    const closeWithEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      popup.close();
    };
    element?.addEventListener("keydown", closeWithEscape);
    if (activePopup.keyboardTrigger) activePopup.content.querySelector("section")?.focus({ preventScroll: true });
    return () => {
      element?.removeEventListener("keydown", closeWithEscape);
      instance.off("popupclose", handleClose);
      popup.remove();
    };
  }, [activePopup]);

  useEffect(() => {
    if (checkpointPopup && !activePopup) setCheckpointPopup(null);
  }, [checkpointPopup, activePopup]);

  useEffect(() => {
    if (!map.current) return;
    const instance = map.current;
    const group = L.layerGroup().addTo(instance);
    const endpointMarkers: { marker: L.Marker; position: Coordinates }[] = [];
    for (const [endpoint, position] of [["origin", origin], ["destination", destination]] as const) {
      if (!position) continue;
      const icon = L.divIcon({
        className: `endpoint-pin endpoint-pin--${endpoint}`,
        html: endpoint === "origin" ? "O" : "D",
        iconSize: [24, 24],
        iconAnchor: [12, 12],
      });
      const marker = L.marker([position.latitude, longitudeNear(position.longitude, instance.getCenter().lng)], {
        icon,
        interactive: false,
        keyboard: false,
      }).addTo(group);
      endpointMarkers.push({ marker, position });
    }
    function alignPins() {
      const center = instance.getCenter().lng;
      for (const { marker, position } of endpointMarkers) {
        const longitude = longitudeNear(position.longitude, center);
        if (marker.getLatLng().lng !== longitude) marker.setLatLng([position.latitude, longitude]);
      }
    }
    instance.on("moveend", alignPins);
    return () => {
      instance.off("moveend", alignPins);
      group.remove();
    };
  }, [origin, destination]);

  const pickPosition = useCallback((position: L.LatLng) => {
    if (!picking) return;
    const { lat, lng } = position.wrap();
    onEndpointPick(picking, { latitude: lat, longitude: lng });
    setPickStatus(`${picking === "origin" ? "Origin" : "Destination"} set to ${lat.toFixed(6)}, ${lng.toFixed(6)}.`);
    setPicking(null);
  }, [onEndpointPick, picking]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !picking) return;
    const handleClick = (event: L.LeafletMouseEvent) => pickPosition(event.latlng);
    instance.on("click", handleClick);
    return () => { instance.off("click", handleClick); };
  }, [pickPosition, picking]);

  function togglePicking(endpoint: Endpoint) {
    setPickStatus("");
    if (endpoint === picking) {
      setPicking(null);
    } else {
      setPicking(endpoint);
      container.current?.focus({ preventScroll: true });
    }
  }

  return (
    <section className="panel map-panel" aria-labelledby="map-title">
      <div className="panel-heading">
        <div>
          <h2 id="map-title">Route map</h2>
          <p>{routes.length ? "Select a checkpoint to open its forecast." : "Routes and weather checkpoints will appear here."}</p>
        </div>
        <button className="quiet-button fit-button" type="button" disabled={!routes.length}
          onClick={() => { if (map.current) fitRoutes(map.current, bounds.current); }}>
          Fit routes
        </button>
      </div>
      <div className="map-pick-tools" aria-label="Trip locations" role="group">
        <button className="quiet-button" type="button" aria-pressed={picking === "origin"} onClick={() => togglePicking("origin")}>Pick origin</button>
        <button className="quiet-button" type="button" aria-pressed={picking === "destination"} onClick={() => togglePicking("destination")}>Pick destination</button>
        {picking && <button className="quiet-button" type="button" onClick={() => { if (map.current) pickPosition(map.current.getCenter()); }}>Use map center</button>}
        {picking && <button className="quiet-button" type="button" onClick={() => setPicking(null)}>Cancel</button>}
      </div>
      <p className="map-pick-status" id="map-pick-status" role="status">
        {picking ? `Click the map to set the ${picking}, or move the map with the arrow keys and press Enter to use its center.` : pickStatus}
      </p>
      <div className="map-frame">
        <div ref={container} className={`map${picking ? " map--picking" : ""}`} role="region" tabIndex={0}
          aria-label="Interactive map of truck routes" aria-describedby={picking ? "map-pick-status" : undefined}
          onKeyDown={(event) => {
            if (!picking || event.target !== event.currentTarget) return;
            if (event.key === "Escape") setPicking(null);
            if (event.key === "Enter" && map.current) {
              event.preventDefault();
              pickPosition(map.current.getCenter());
            }
          }} />
        {picking && <span className="map-crosshair" aria-hidden="true">+</span>}
        {tilesUnavailable && <p className="map-message" role="status">Base map unavailable. Route lines and checkpoints can still be displayed.</p>}
      </div>
      <div className="map-footer">
        {routes.length > 0 && <span>Routing by <a href="https://www.tomtom.com/">TomTom</a></span>}
        <a href="https://www.openstreetmap.org/fixthemap">Report a map issue</a>
      </div>
      {activePopup && createPortal(
        <section aria-labelledby="checkpoint-popup-title" tabIndex={-1}>
          <h3 id="checkpoint-popup-title">Checkpoint {activePopup.index + 1} forecast</h3>
          <CheckpointDetails checkpoint={activePopup.route.checkpoints[activePopup.index]} />
        </section>, activePopup.content,
      )}
    </section>
  );
}
