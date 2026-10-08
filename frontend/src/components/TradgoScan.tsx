import scanVideo from '../assets/scan-globe.mp4';

/** Full-screen globe video shown while the single Tradgo Call computes.
 *  Mirrors the Analysis scan and names no parameter, so the model stays
 *  private. Decorative — the indeterminate bar marks that it is working. */
export default function TradgoScan({ symbol }: { symbol: string }) {
  return (
    <div className="an-scan an-scan-full">
      <video className="an-stage-vid" src={scanVideo}
        autoPlay loop muted playsInline aria-hidden="true" />
      <div className="an-stage-scrim" />

      <div className="an-scan-head">
        <h2>Analyzing <span>{symbol}</span>…</h2>
        <p>Our agents are gathering and processing the market data</p>
      </div>

      <div className="an-progress">
        <div className="ap-track ts-indeterminate"><i /></div>
        <div className="an-progress-foot">
          <span>Analyzing {symbol} — gathering and processing data</span>
        </div>
      </div>
    </div>
  );
}
