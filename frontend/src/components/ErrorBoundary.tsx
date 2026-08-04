/**
 * Containment for render-time crashes.
 *
 * React's default behaviour on an uncaught render error is to unmount the whole
 * tree, which paints a blank white page: no message, no navigation, no way back
 * except reloading, and nothing in the server log because the request that fed
 * the crash returned 200. That failure mode cost a full debugging session, so
 * it is contained here rather than trusted not to happen again.
 *
 * The boundary is mounted around the routed screen, not around the shell: the
 * sidebar keeps working, so a crash on one screen never blocks access to the
 * others. Remounting on navigation is what lets "go elsewhere and come back"
 * clear the error without a reload.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Alert, Button } from './ui'

type Props = { children: ReactNode; resetKey?: string }
type State = { error: Error | null; stack?: string }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(previous: Props): void {
    // A different screen gets a clean slate; without this the error would
    // survive navigation and look like the whole app is broken.
    if (previous.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null, stack: undefined })
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.setState({ stack: info.componentStack ?? undefined })
    // Reaches the browser console, the only place a user can copy it from.
    console.error('[inventaire] écran en erreur', error, info.componentStack)
  }

  render(): ReactNode {
    const { error, stack } = this.state
    if (!error) return this.props.children

    return (
      <div className="stack">
        <Alert tone="danger" title="Cet écran n’a pas pu s’afficher">
          <p style={{ margin: '0 0 var(--space-3)' }}>
            Une erreur d’affichage s’est produite. Vos données ne sont pas
            touchées&nbsp;: rien n’est enregistré par un écran qui n’a pas pu se
            dessiner. Les autres écrans restent accessibles depuis le menu.
          </p>
          <div className="row" style={{ gap: 'var(--space-2)' }}>
            <Button
              variant="primary"
              size="sm"
              onClick={() => this.setState({ error: null, stack: undefined })}
            >
              Réessayer
            </Button>
            <Button size="sm" onClick={() => window.location.reload()}>
              Recharger la page
            </Button>
          </div>
        </Alert>

        <details className="card">
          <summary style={{ cursor: 'pointer', fontSize: 'var(--text-sm)' }}>
            Détail technique — à joindre à un signalement
          </summary>
          <pre
            className="mono"
            style={{
              marginTop: 'var(--space-3)',
              fontSize: 'var(--text-xs)',
              whiteSpace: 'pre-wrap',
              overflowX: 'auto',
            }}
          >
            {error.name}: {error.message}
            {stack}
          </pre>
        </details>
      </div>
    )
  }
}
