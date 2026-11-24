import { useEffect, useState } from "react";

type ApiHealth = {
  status: string;
  service: string;
};

type ConnectionState = "checking" | "connected" | "unavailable";

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>("checking");

  useEffect(() => {
    const controller = new AbortController();

    async function checkApi() {
      try {
        const response = await fetch("/api/health/", { signal: controller.signal });
        if (!response.ok) throw new Error(`API returned ${response.status}`);

        const health: ApiHealth = await response.json();
        setConnection(health.status === "ok" ? "connected" : "unavailable");
      } catch {
        if (!controller.signal.aborted) setConnection("unavailable");
      }
    }

    void checkApi();
    return () => controller.abort();
  }, []);

  const connectionText = {
    checking: "Checking API",
    connected: "API connected",
    unavailable: "API unavailable",
  }[connection];

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Weather-Aware Truck Routing home">
          <span className="brand-mark" aria-hidden="true">W</span>
          <span>RouteWise</span>
        </a>
        <span className={`connection connection--${connection}`}>
          <span className="connection-dot" aria-hidden="true" />
          {connectionText}
        </span>
      </header>

      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Weather-aware planning</p>
        <h1 id="page-title">Plan the safer way forward.</h1>
        <p className="hero-copy">
          Compare truck routes by forecast conditions, cargo weight, and travel time.
        </p>
      </section>

      <section className="planning-card" aria-labelledby="planning-title">
        <div className="card-heading">
          <div>
            <p className="eyebrow">Trip planner</p>
            <h2 id="planning-title">Your route workspace is ready</h2>
          </div>
          <span className="step-label">Foundation</span>
        </div>
        <p className="card-copy">
          This first project step connects the React interface to the Django API. Route search,
          weather checkpoints, and recommendations will be added in the next steps.
        </p>
        <div className="setup-grid">
          <div className="setup-item">
            <span className="setup-number">01</span>
            <span className="setup-label">React + TypeScript</span>
            <span className="setup-status">Ready</span>
          </div>
          <div className="setup-item">
            <span className="setup-number">02</span>
            <span className="setup-label">Django API</span>
            <span className="setup-status">{connection === "connected" ? "Connected" : connectionText}</span>
          </div>
          <div className="setup-item">
            <span className="setup-number">03</span>
            <span className="setup-label">Route and weather logic</span>
            <span className="setup-status setup-status--pending">Next step</span>
          </div>
        </div>
      </section>

      <footer className="footer">Weather-aware decisions for every mile.</footer>
    </main>
  );
}
