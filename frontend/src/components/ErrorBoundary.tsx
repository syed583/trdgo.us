import { Component, type ErrorInfo, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';

// A render-time exception anywhere below this boundary is caught here instead
// of unmounting the whole React tree to a blank white screen. Keyed by route in
// App.tsx so navigating to another page clears a crashed one automatically.
interface Props { children: ReactNode; }
interface State { error: Error | null; }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface it in the console for debugging; no telemetry endpoint wired.
    console.error('Render error caught by ErrorBoundary:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="page">
          <div className="state" style={{ minHeight: 320 }}>
            <AlertTriangle size={20} />
            <span className="state-title">Something went wrong on this page</span>
            <span style={{ maxWidth: 440 }}>
              An unexpected error stopped this screen from rendering. The rest of
              the app is fine — try again, or go back to the dashboard.
            </span>
            <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
              <button className="btn" onClick={() => this.setState({ error: null })}>
                Try again
              </button>
              <button className="btn" onClick={() => { window.location.href = '/'; }}>
                Back to dashboard
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
