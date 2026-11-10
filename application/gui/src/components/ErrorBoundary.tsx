import { Component, type ReactNode } from "react";

type Props = {
  children: ReactNode;
  /** When this value changes, a caught error is cleared and children render again. */
  resetKey?: string;
};

type State = { error: Error | null };

/**
 * Catches render errors below it and shows a message with a Reload button.
 *
 * React logs the caught error to the console; this component does not log.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.error !== null && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <div className="page-body">
        <p className="flash-error mb-4" role="alert">
          This page failed to display. Reload to try again.
        </p>
        <button className="btn" type="button" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
