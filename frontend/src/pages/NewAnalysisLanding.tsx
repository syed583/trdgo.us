import React from 'react';
import { NavLink } from 'react-router-dom';
import { CalendarDays, Layers, Target, Zap } from 'lucide-react';
import type { PageContext } from '../App';
import analysisHero from '../assets/analysis-hero.webp';
import '../new-dashboard.css';

/**
 * The Analysis Intraday LANDING in the redesigned shell (Figma frame 2:2).
 *
 * THE FRAME IS NAMED "Market intelligence dashboard" AND IS NOT THE DASHBOARD. Its panels - Top
 * Movers, the index list, the earnings and news list - are the ones on THIS page's landing, not on
 * /dashboard. It was mapped by its name once; the owner corrected it, and the panels settle it.
 *
 * NOTHING HERE FETCHES ANYTHING. Both the controls that start an analysis and the three data panels
 * are PASSED IN, already built by InsightsPage from the components it has always used. This file is
 * layout and styling only. That is deliberate: a redesigned landing that re-implemented the panels
 * would be a second copy of the page's data logic, and the copy is the one that quietly stops
 * matching the original. Restyling is done in CSS, against the panels' own class names.
 *
 * WHAT THE FRAME HAS AND THIS DOES NOT. The frame also draws Options Flow, Market Sentiment and
 * Sector Performance panels. The live landing has none of them, and the rule is that the design's
 * extras are left out while nothing the live site shows is removed.
 *
 * Only the IDLE landing changes. The scanning view and the analysed result are untouched, so a
 * search started here behaves exactly as it does today.
 */
export default function NewAnalysisLanding({
  ctx, controls, panels,
}: { ctx: PageContext; controls: React.ReactNode; panels: React.ReactNode }) {
  return (
    <div className="nd nal">
      <section className="nd-hero nal-hero">
        <img className="nd-hero-img" src={analysisHero} alt="" aria-hidden="true" loading="eager" />

        <div className="nd-hero-left">
          <span className="nd-eyebrow">US Market Intelligence Platform</span>
          <h1>Trade Smarter with<br />Real Data &amp; <em>AI Insights</em></h1>
          <p>Earnings · Options flow · Technicals · Sentiment, read for one ticker at a time.</p>
          <div className="nal-controls">{controls}</div>
        </div>

        {/* Links to pages that exist. No figure is claimed on this artwork: the four cards the design
            painted into the image were replaced by real ones on the old landing, and a landing that
            states a number the server does not measure is the thing we removed, not a thing to add. */}
        <div className="nd-hero-right nal-feats">
          <NavLink to="/earnings-calendar" className="nd-hero-feat">
            <span className="nd-feat-ico violet"><CalendarDays size={18} /></span>
            <span><b>Earnings</b><i>Dates, estimates and surprises</i></span>
          </NavLink>
          <NavLink to="/unusual-activity" className="nd-hero-feat">
            <span className="nd-feat-ico amber"><Layers size={18} /></span>
            <span><b>Options Flow</b><i>Unusual contracts and disparity</i></span>
          </NavLink>
          <NavLink to="/strategy-trades" className="nd-hero-feat">
            <span className="nd-feat-ico green"><Target size={18} /></span>
            <span><b>Strategy Trades</b><i>Tracked entries, stops and exits</i></span>
          </NavLink>
          <NavLink to="/smart-move/engine20" className="nd-hero-feat">
            <span className="nd-feat-ico blue"><Zap size={18} /></span>
            <span><b>Smart Money Move</b><i>Calls before the window closes</i></span>
          </NavLink>
        </div>
      </section>

      {/* The live panels, unchanged in behaviour and in data; the new look is CSS on `.nal`. */}
      <div className="nal-panels">{panels}</div>

      {ctx.isAdmin && (
        <p className="nal-admin">Landing panels: Top Movers, index prices, earnings and news — the same sources as the classic landing.</p>
      )}
    </div>
  );
}
