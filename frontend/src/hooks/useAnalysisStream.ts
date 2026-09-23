import { useCallback, useEffect, useRef, useState } from 'react';
import { API_BASE_URL } from '../api/client';

export interface Stage {
  key: string;
  label: string;
  sub: string;
  feeds: string[];
}

export interface StageState {
  status: 'pending' | 'scanning' | 'complete' | 'unavailable';
  elapsed?: number;
  detail?: string;
}

export interface Category {
  key: string;
  label: string;
  score: number | null;
  available: boolean;
  detail?: string;
  /** This category's share of the model's 100 points. */
  weight_possible?: number | null;
  /** What it actually contributed, signed: negative is a bearish vote. */
  points?: number | null;
  /**
   * Which of the model's two lists this belongs to: the eighty points of
   * market and price, or the twenty of company and ownership.
   */
  group?: string;
}

/** One parameter as the result payload carries it. */
export interface DirSignalLike {
  name: string;
  label: string;
  weight: number;
  available: boolean;
  points: number;
  points_label: string;
  directional: boolean;
  detail: string;
  rule: string;
  unavailable_reason: string;
}

export interface LogLine {
  time: string;
  text: string;
  status: string;
  elapsed?: number;
}

export interface AnalysisStream {
  stages: Stage[];
  state: Record<string, StageState>;
  log: LogLine[];
  result: any;
  categories: Category[];
  elapsed: number;
  running: boolean;
  error: string | null;
  done: number;
  total: number;
  percent: number;
  run: () => void;
  stop: () => void;
}

/**
 * Consumes the server-sent analysis stream.
 *
 * Shared by the insights page and the fullscreen orbit so both watch the same
 * run rather than each starting its own -- two concurrent analyses of one
 * symbol would double the provider load to show the same answer twice.
 *
 * ``autoStart`` is off for the overlay, which should only fetch when opened.
 */
export function useAnalysisStream(
  symbol: string, autoStart = true, horizon: string = 'SWING',
): AnalysisStream {
  const [stages, setStages] = useState<Stage[]>([]);
  const [state, setState] = useState<Record<string, StageState>>({});
  const [log, setLog] = useState<LogLine[]>([]);
  const [result, setResult] = useState<any>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abort.current?.abort();
    abort.current = null;
    setRunning(false);
  }, []);

  const run = useCallback(async () => {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;

    setRunning(true);
    setError(null);
    setResult(null);
    setCategories([]);
    setState({});
    setLog([]);
    setElapsed(0);

    try {
      const response = await fetch(
        // A deliberate run re-reads the providers rather than replaying what
        // is cached: the wait is the research, and without this the screen
        // would be showing a scan of figures fetched minutes ago.
        `${API_BASE_URL}/api/analyze/${encodeURIComponent(symbol)}/stream`
        + `?refresh=true&horizon=${encodeURIComponent(horizon)}`,
        { signal: controller.signal, credentials: 'same-origin' },
      );
      if (!response.ok || !response.body) {
        throw new Error(`Analysis failed (${response.status})`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // Events are separated by a blank line; a partial tail stays in the
        // buffer until the rest of it arrives.
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() || '';

        for (const chunk of chunks) {
          const line = chunk.replace(/^data: /, '').trim();
          if (!line) continue;
          let event: any;
          try { event = JSON.parse(line); } catch { continue; }

          if (event.type === 'start') {
            setStages(event.stages);
            const initial: Record<string, StageState> = {};
            event.stages.forEach((s: Stage) => {
              initial[s.key] = { status: 'scanning' };
            });
            setState(initial);
          } else if (event.type === 'stage') {
            setState((prev) => ({
              ...prev,
              [event.key]: {
                status: event.status,
                elapsed: event.elapsed,
                detail: event.detail,
              },
            }));
            setElapsed(event.elapsed);
            setLog((prev) => [...prev, {
              time: new Date().toLocaleTimeString(),
              text: event.key.replace(/_/g, ' '),
              status: event.status,
              elapsed: event.elapsed,
            }]);
          } else if (event.type === 'result') {
            setResult(event.result);
            setCategories(event.categories || []);
            setElapsed(event.elapsed);
          } else if (event.type === 'error') {
            setError(event.detail);
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError((err as Error).message);
      }
    } finally {
      setRunning(false);
    }
  }, [symbol, horizon]);

  useEffect(() => {
    if (autoStart) run();
    return () => abort.current?.abort();
  }, [run, autoStart]);

  const done = Object.values(state).filter(
    (s) => s.status === 'complete' || s.status === 'unavailable').length;
  const total = stages.length || 12;

  return {
    stages, state, log, result, categories, elapsed, running, error,
    done, total,
    percent: total ? Math.round(done / total * 100) : 0,
    run, stop,
  };
}
