import { formatNumber, formatTime } from "./routes";
import type { Checkpoint } from "./routes";

const FACTORS: Record<string, string> = {
  wind: "Wind", precipitation: "Precipitation", low_visibility: "Low visibility", possible_icing: "Possible icing",
};

function forecastValue(value: number | null | undefined, unit: string): string {
  return value == null ? "Unavailable" : `${formatNumber(value)} ${unit}`;
}

export default function CheckpointDetails({ checkpoint }: { checkpoint: Checkpoint }) {
  const { arrival_at, forecast, risk } = checkpoint;
  return (
    <>
      <div className="checkpoint-times">
        <div><span>Estimated arrival</span><strong>{formatTime(arrival_at)}</strong></div>
        <div><span>Forecast time</span><strong>{forecast ? formatTime(forecast.valid_at) : "Unavailable"}</strong></div>
      </div>
      <dl className="forecast-grid">
        <div><dt>Temperature</dt><dd>{forecastValue(forecast?.temperature_c, "°C")}</dd></div>
        <div><dt>Precipitation</dt><dd>{forecastValue(forecast?.precipitation_mm_per_h, "mm/h")}</dd></div>
        <div><dt>Wind</dt><dd>{forecastValue(forecast?.wind_speed_m_per_s, "m/s")}</dd></div>
        <div><dt>Wind gusts</dt><dd>{forecastValue(forecast?.wind_gust_m_per_s, "m/s")}</dd></div>
        <div><dt>Visibility</dt><dd>{forecastValue(forecast?.visibility_m, "m")}</dd></div>
        <div><dt>Weather risk</dt><dd>{risk ? `${formatNumber(risk.score)} / 100` : "Unscored"}</dd></div>
      </dl>
      {risk && <p className="checkpoint-note">Risk factors · {risk.factors.length
        ? risk.factors.map((factor) => Object.hasOwn(FACTORS, factor) ? FACTORS[factor] : factor).join(", ")
        : "None"}</p>}
      {!forecast && <p className="checkpoint-note">Forecast data is unavailable for this arrival.</p>}
      {forecast && !risk && <p className="checkpoint-note">Some measurements are missing, so this checkpoint is unscored.</p>}
    </>
  );
}
