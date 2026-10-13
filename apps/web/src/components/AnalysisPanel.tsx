import type { AnalysisResponse, GateModuleRow, SetupCard } from "../api";

interface Props {
  analysis: AnalysisResponse | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

function fmt(price: number): string {
  if (!Number.isFinite(price)) return "-";
  const abs = Math.abs(price);
  if (abs >= 1000) return price.toFixed(2);
  if (abs >= 10) return price.toFixed(3);
  return price.toFixed(5);
}

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : `${(value * 100).toFixed(1)}%`;
}

function interval(pair: [number, number] | null | undefined): string {
  return pair ? `${pct(pair[0])} to ${pct(pair[1])}` : "-";
}

function Evidence({ card }: { card: SetupCard }) {
  const shownProbability = card.probability;
  return (
    <ul className="evidence">
      <li>
        <strong>{shownProbability === null ? "score" : "estimated probability"}:</strong>{" "}
        {shownProbability === null
          ? `${card.score === null ? "-" : card.score.toFixed(3)} (a model score, not a calibrated probability)`
          : `${pct(shownProbability)} (calibrated on a later unseen period)`}
      </li>
      <li>
        <strong>baseline for this reward:risk:</strong> {pct(card.baseline_p0)} - a coin with the
        same target distance would hit this often
      </li>
      <li>
        <strong>expected value:</strong>{" "}
        {card.expected_value === null ? "-" : `${card.expected_value.toFixed(3)} R per trade`}
      </li>
      <li>
        <strong>similar past setups:</strong> {card.similar.cases}
        {card.similar.cases > 0 && <> , hit {pct(card.similar.hit_rate)}, 95% interval {interval(card.similar.interval)}</>}
      </li>
      <li>
        <strong>known from:</strong> {new Date(card.known_time).toLocaleString()} (formed{" "}
        {new Date(card.formation_time).toLocaleString()})
      </li>
    </ul>
  );
}

function SetupBody({ card, shown }: { card: SetupCard; shown: boolean }) {
  return (
    <div className={shown ? "setup-card" : "setup-card muted"}>
      <div className="setup-head">
        <span className={`side ${card.direction}`}>{card.direction.toUpperCase()}</span>
        <span className="module">
          {card.module} v{card.module_version}
        </span>
        <span className="rr">reward:risk {card.reward_risk.toFixed(2)}</span>
      </div>
      <div className="levels-grid">
        <div>
          <span>entry</span>
          <strong>{fmt(card.entry)}</strong>
        </div>
        <div>
          <span>stop</span>
          <strong>{fmt(card.stop)}</strong>
        </div>
        <div>
          <span>target</span>
          <strong>{fmt(card.target)}</strong>
        </div>
      </div>
      <h4>Reasons</h4>
      <ul>
        {card.reasons.map((reason, i) => (
          <li key={i}>{reason}</li>
        ))}
      </ul>
      <h4>Invalidation</h4>
      <ul>
        {card.invalidation.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
      <h4>Evidence</h4>
      <Evidence card={card} />
      {card.notes.length > 0 && <p className="notes">{card.notes.join(" ")}</p>}
    </div>
  );
}

function ModuleTable({ modules }: { modules: Record<string, GateModuleRow> }) {
  const rows = Object.values(modules);
  if (rows.length === 0) return <p className="notes">No module produced resolved out-of-sample trades.</p>;
  return (
    <table className="modules">
      <thead>
        <tr>
          <th>module</th>
          <th>resolved</th>
          <th>hit rate</th>
          <th>95% interval</th>
          <th>its baseline</th>
          <th>beats baseline</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.module} className={row.enabled ? "ok" : ""}>
            <td>{row.module}</td>
            <td>{row.resolved}</td>
            <td>{pct(row.win_rate)}</td>
            <td>{interval(row.interval)}</td>
            <td>{pct(row.baseline_p0)}</td>
            <td>{row.enabled ? "yes" : "no"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function AnalysisPanel({ analysis, loading, error, onRefresh }: Props) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Analysis</h2>
        <button onClick={onRefresh} disabled={loading}>
          {loading ? "analysing..." : "re-run"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {!analysis && !loading && !error && (
        <p className="notes">Pick a symbol and a timeframe to run the analysis.</p>
      )}
      {analysis && (
        <>
          <p className="notes">
            {analysis.symbol} {analysis.timeframe} - {analysis.candles} closed candles, as of{" "}
            {new Date(analysis.as_of).toLocaleString()}
            {analysis.cached && " (cached)"}
            {analysis.stale && <span className="warn"> - feed looks stale</span>}
          </p>

          {analysis.status === "setup" && analysis.setup ? (
            <>
              {analysis.statement && <p className="statement">{analysis.statement}</p>}
              <SetupBody card={analysis.setup} shown={true} />
            </>
          ) : (
            <div className="no-setup">
              <strong>No setup.</strong> <span>{analysis.reason}</span>
              {analysis.setup && (
                <p className="notes">
                  The closest candidate was not shown, it is listed below for context only.
                </p>
              )}
            </div>
          )}
          {analysis.status !== "setup" && analysis.setup && (
            <SetupBody card={analysis.setup} shown={false} />
          )}

          <h3>Symbol gate</h3>
          <p className="notes">
            <strong>{analysis.gate.passed ? "passed" : "failed"}:</strong> {analysis.gate.verdict}
          </p>
          <ul className="gate">
            <li>variance ratio: {analysis.gate.variance_ratio.verdict}</li>
            <li>bounce probability: {analysis.gate.bounce.verdict}</li>
          </ul>
          <ModuleTable modules={analysis.gate.modules} />

          <h3>Model</h3>
          <p className="notes">
            {analysis.model.calibrated
              ? "Fitted on an earlier period and calibrated on a later period the model never saw."
              : "Not calibrated on this sample, so every number from it is a score, never a probability."}{" "}
            {analysis.candidates_considered} candidates came from the most recent period.
          </p>

          <h3>Notes</h3>
          <ul className="gate">
            {analysis.notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
          <p className="notes disclaimer">{analysis.disclaimer}</p>
        </>
      )}
    </section>
  );
}
