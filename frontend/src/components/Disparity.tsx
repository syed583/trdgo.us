import React from 'react';
import { Activity, AlertTriangle, MinusCircle } from 'lucide-react';
import { Panel } from './common';
import { api2 } from '../api/client';
import { useApi } from '../hooks/useApi';

/**
 * How far the options market sits from its own balance, reading by reading.
 *
 * Two numbers, kept apart on purpose. Stretch is how unusual the options
 * market looks, whichever way it leans; tilt is which side the readings that
 * carry a direction favour. Volume against open interest, IV rank, gamma and
 * concentration are loud rather than bullish, so they show as "no direction"
 * and raise the stretch alone -- presenting them as a lean would be inventing
 * a signal out of activity.
 */
interface Reading {
  name: string;
  label: string;
  value: number | null;
  stretch: number | null;
  directional: boolean;
  available: boolean;
  detail: string;
  missing_reason: string | null;
}

const BAR = 78;

function Bar({ reading }: { reading: Reading }) {
  if (!reading.available || reading.value == null) {
    return <span className="dp-bar dp-bar-empty" />;
  }
  const value = reading.value;
  if (!reading.directional) {
    return (
      <span className="dp-bar">
        <i className="dp-fill dp-loud" style={{ width: `${Math.abs(value) * 100}%` }} />
      </span>
    );
  }
  const width = Math.abs(value) * 50;
  return (
    <span className="dp-bar dp-bar-two">
      <i className="dp-mid" />
      <i className={`dp-fill ${value >= 0 ? 'dp-call' : 'dp-put'}`}
        style={{ width: `${width}%`, left: value >= 0 ? '50%' : `${50 - width}%` }} />
    </span>
  );
}

export default function Disparity({ symbol }: { symbol: string }) {
  const state = useApi<any>((s) => api2.disparity(symbol, s), [symbol]);
  const d = state.data;

  if (state.initialLoading) {
    return <Panel title="Options Disparity"><p className="ce-note">Reading the options tape…</p></Panel>;
  }
  if (!d || d.status !== 'OK') {
    return (
      <Panel title="Options Disparity">
        <p className="ce-note">{d?.detail || 'No options readings available.'}</p>
      </Panel>
    );
  }

  const readings: Reading[] = d.readings || [];
  const directional = readings.filter((r) => r.directional);
  const loud = readings.filter((r) => !r.directional);
  const tilt = d.tilt as number | null;

  return (
    <Panel title="Options Disparity" noBody
      right={<span className="ce-count">{d.readings_available} of {d.readings_total} readings</span>}>
      <div className="dp">
        <div className="fa-stats dp-stats">
          <div className="fa-stat">
            <b>{d.stretch}</b>
            <span>stretch (0–100)</span>
          </div>
          <div className={`fa-stat ${tilt == null ? '' : tilt > 0.15 ? 'fa-buy' : tilt < -0.15 ? 'fa-sell' : ''}`}>
            <b>{tilt == null ? '--' : `${tilt > 0 ? '+' : ''}${tilt}`}</b>
            <span>tilt · {d.leaning}</span>
          </div>
          <div className="fa-stat">
            <b>{d.readings_stretched}</b>
            <span>readings stretched</span>
          </div>
          <div className="fa-stat">
            <b>{d.coverage_pct}%</b>
            <span>coverage</span>
          </div>
        </div>

        <div className="dp-group">
          <h5><Activity size={12} /> Readings with a direction</h5>
          {directional.map((r) => (
            <div className={`dp-row ${r.available ? '' : 'dp-missing'}`} key={r.name}>
              <span className="dp-label">{r.label}</span>
              <Bar reading={r} />
              <b className="dp-value">
                {r.available && r.value != null
                  ? `${r.value > 0 ? '+' : ''}${r.value}` : '--'}
              </b>
              <p className="dp-detail">{r.detail}</p>
            </div>
          ))}
        </div>

        <div className="dp-group">
          <h5><AlertTriangle size={12} /> Loud, not directional</h5>
          <p className="dp-note">
            These say the options market is unusual, not which way it leans.
            They raise the stretch and never the tilt.
          </p>
          {loud.map((r) => (
            <div className={`dp-row ${r.available ? '' : 'dp-missing'}`} key={r.name}>
              <span className="dp-label">{r.label}</span>
              <Bar reading={r} />
              <b className="dp-value">{r.available && r.value != null ? r.value : '--'}</b>
              <p className="dp-detail">{r.detail}</p>
            </div>
          ))}
        </div>

        {readings.some((r) => !r.available) && (
          <p className="dp-note dp-missing-note">
            <MinusCircle size={11} />
            {readings.filter((r) => !r.available).length} reading(s) unavailable —
            counted as missing, never as balanced.
          </p>
        )}

        <p className="ce-foot">{d.detail} Source: {d.source}.</p>
      </div>
    </Panel>
  );
}
