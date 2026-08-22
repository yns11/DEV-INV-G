/**
 * Shared UI primitives.
 *
 * Deliberately small and dependency-free: every component maps onto the tokens
 * in `design/tokens.css`, so the whole interface stays coherent and the bundle
 * stays under the size the platform is happy to serve.
 *
 * The non-negotiable contract for any data view — loading, empty, error and
 * partial states — is implemented once here (`Skeleton`, `EmptyState`,
 * `ErrorState`, `AsyncBoundary`) so no screen can forget one.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { ApiError, download } from '../lib/api'
import { useCollapsed } from '../lib/collapse'
import { DASH } from '../lib/format'

/**
 * Download a file, reporting a server refusal as a toast.
 *
 * Every download goes through this: a refusal that leaves the screen unchanged
 * reads as "the button is broken", when the server has in fact explained
 * itself (an empty counting sheet, a report the campaign is not ready for).
 */
export function useDownload() {
  const showError = useErrorToast()
  return useCallback(
    (path: string, fallback = 'Téléchargement impossible') => {
      void download(path).catch((error: unknown) => showError(error, fallback))
    },
    [showError],
  )
}

// --------------------------------------------------------------------------- //
// Icons — inline SVG, no icon-font dependency
// --------------------------------------------------------------------------- //

type IconProps = { size?: number; className?: string }

const svg = (path: ReactNode, viewBox = '0 0 24 24') =>
  function Icon({ size = 16, className }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox={viewBox}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        aria-hidden="true"
        focusable="false"
      >
        {path}
      </svg>
    )
  }

export const Icons = {
  dashboard: svg(<><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></>),
  layers: svg(<><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 17 9 5 9-5" /></>),
  clipboard: svg(<><rect x="8" y="2" width="8" height="4" rx="1" /><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><path d="m9 14 2 2 4-4" /></>),
  grid: svg(<><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18M15 3v18" /></>),
  chart: svg(<><path d="M3 3v18h18" /><path d="m19 9-5 5-4-4-3 3" /></>),
  scale: svg(<><path d="M12 3v18" /><path d="m5 8 7-5 7 5" /><path d="M3 14h6l-3-6-3 6Z" /><path d="M15 14h6l-3-6-3 6Z" /></>),
  history: svg(<><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /><path d="M12 7v5l3 2" /></>),
  plus: svg(<><path d="M12 5v14M5 12h14" /></>),
  /** Ouvrir une feuille pour la saisir : le geste, pas l'état. */
  pencil: svg(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></>),
  /** Rouvrir ce qu'on vient de déclarer fini. */
  undo: svg(<><path d="M3 7v6h6" /><path d="M3.5 13a9 9 0 1 0 2.1-5.9L3 10" /></>),
  check: svg(<><path d="m5 13 4 4L19 7" /></>),
  x: svg(<><path d="M18 6 6 18M6 6l12 12" /></>),
  search: svg(<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>),
  download: svg(<><path d="M12 3v12" /><path d="m7 11 5 5 5-5" /><path d="M4 20h16" /></>),
  upload: svg(<><path d="M12 20V8" /><path d="m7 12 5-5 5 5" /><path d="M4 4h16" /></>),
  alert: svg(<><path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.3 3.9 2.4 17.1A2 2 0 0 0 4.1 20h15.8a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0Z" /></>),
  info: svg(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>),
  lock: svg(<><rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>),
  refresh: svg(<><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 3v6h-6" /></>),
  chevronRight: svg(<><path d="m9 6 6 6-6 6" /></>),
  chevronLeft: svg(<><path d="m15 6-6 6 6 6" /></>),
  chevronDown: svg(<><path d="m6 9 6 6 6-6" /></>),
  sparkles: svg(<><path d="m12 3 1.8 4.9L19 9.6l-4.4 2.6L13.4 17 12 13.2 10.6 17 9.4 12.2 5 9.6l5.2-1.7L12 3Z" /><path d="M18 16.5 18.7 18l1.5.7-1.5.7L18 21l-.7-1.6-1.5-.7 1.5-.7L18 16.5Z" /></>),
  printer: svg(<><path d="M6 9V3h12v6" /><rect x="3" y="9" width="18" height="8" rx="2" /><path d="M7 17h10v4H7z" /></>),
  trash: svg(<><path d="M4 7h16" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /><path d="M6 7v13a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7" /></>),
  sun: svg(<><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>),
  moon: svg(<><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></>),
  inbox: svg(<><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1Z" /></>),
  filter: svg(<><path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" /></>),
  copy: svg(<><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  box: svg(<><path d="m21 8-9-5-9 5v8l9 5 9-5V8Z" /><path d="m3 8 9 5 9-5" /><path d="M12 13v8" /></>),
  tree: svg(<><rect x="9" y="2" width="6" height="5" rx="1" /><rect x="2" y="17" width="6" height="5" rx="1" /><rect x="16" y="17" width="6" height="5" rx="1" /><path d="M12 7v5M5 17v-3h14v3" /></>),
  sliders: svg(<><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="9" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="8" cy="18" r="2" /></>),
  database: svg(<><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>),
}

// --------------------------------------------------------------------------- //
// Basic elements
// --------------------------------------------------------------------------- //

export function Button({
  variant = 'secondary',
  size,
  icon,
  children,
  className = '',
  ...rest
}: {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'lg'
  icon?: ReactNode
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const classes = [
    'btn',
    `btn--${variant}`,
    size ? `btn--${size}` : '',
    !children && icon ? 'btn--icon' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <button className={classes} {...rest}>
      {icon}
      {children}
    </button>
  )
}

export function Badge({
  tone = 'neutral',
  dot = false,
  children,
  className = '',
  title,
}: {
  tone?: 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info' | string
  dot?: boolean
  children: ReactNode
  className?: string
  /** Native tooltip — used to explain a code without lengthening the label. */
  title?: string
}) {
  return (
    <span className={`badge badge--${tone} ${className}`} title={title}>
      {dot && <span className="badge__dot" />}
      {children}
    </span>
  )
}

/**
 * A full-width block, foldable by its title.
 *
 * Every screen stacks several of these and none of them serves everybody on the
 * same day, so the title doubles as a fold handle: what you are not using goes
 * away and what you are lifts above the fold. The state is remembered per block
 * — a block that springs back open on every navigation is a block you fold ten
 * times a day.
 *
 * Folding needs a stable key, which a plain-string `title` provides; a rich node
 * does not, so those cards stay open unless a `collapseKey` names them. And a
 * card with no header has no handle to offer, so it is never foldable.
 */
export function Card({
  title,
  message,
  actions,
  footer,
  flush = false,
  collapseKey,
  collapsible,
  children,
  className = '',
}: {
  title?: ReactNode
  /** IBCS: the finding goes here, above the data — not buried below it. */
  message?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  flush?: boolean
  /** Identity under which the folded state is remembered. */
  collapseKey?: string
  /** Force folding off on a block that must stay whole. */
  collapsible?: boolean
  children: ReactNode
  className?: string
}) {
  const key = collapseKey ?? (typeof title === 'string' ? title : '')
  const foldable = (collapsible ?? true) && key !== ''
  const [collapsed, setCollapsed] = useCollapsed(key)
  const folded = foldable && collapsed

  return (
    <section className={`card${folded ? ' card--collapsed' : ''} ${className}`}>
      {(title || actions) && (
        <header className="card__head">
          <div className="row" style={{ minWidth: 0, gap: 'var(--space-2)' }}>
            {foldable && (
              <button
                className="card__fold"
                aria-expanded={!folded}
                title={folded ? 'Déplier ce bloc' : 'Replier ce bloc'}
                onClick={() => setCollapsed(!folded)}
              >
                {folded ? (
                  <Icons.chevronRight size={14} />
                ) : (
                  <Icons.chevronDown size={14} />
                )}
              </button>
            )}
            <div style={{ minWidth: 0 }}>
              {title && <h2 className="card__title">{title}</h2>}
              {message && !folded && <p className="card__message">{message}</p>}
            </div>
          </div>
          {actions && !folded && <div className="row-wrap">{actions}</div>}
        </header>
      )}
      {!folded && (
        <div className={flush ? 'card__body card__body--flush' : 'card__body'}>
          {children}
        </div>
      )}
      {footer && !folded && <footer className="card__foot">{footer}</footer>}
    </section>
  )
}

export function Alert({
  tone = 'info',
  title,
  children,
  actions,
}: {
  tone?: 'info' | 'success' | 'warning' | 'danger'
  title?: ReactNode
  children?: ReactNode
  actions?: ReactNode
}) {
  const Icon = tone === 'info' ? Icons.info : Icons.alert
  return (
    <div className={`alert alert--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="alert__icon">
        <Icon size={16} />
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        {title && <div className="alert__title">{title}</div>}
        {children && <div className="alert__body">{children}</div>}
      </div>
      {actions}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Required states
// --------------------------------------------------------------------------- //

export function Skeleton({
  height = 16,
  width = '100%',
  count = 1,
}: {
  height?: number | string
  width?: number | string
  count?: number
}) {
  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }} aria-busy="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton" style={{ height, width }} />
      ))}
    </div>
  )
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div style={{ padding: 'var(--space-4)' }} aria-busy="true" aria-label="Chargement">
      <div className="stack" style={{ gap: 'var(--space-3)' }}>
        {Array.from({ length: rows }, (_, r) => (
          <div key={r} className="row" style={{ gap: 'var(--space-3)' }}>
            {Array.from({ length: cols }, (_, c) => (
              <div
                key={c}
                className="skeleton"
                style={{ height: 14, flex: c === 0 ? 2 : 1 }}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export function EmptyState({
  title,
  children,
  action,
  icon,
}: {
  title: string
  children?: ReactNode
  action?: ReactNode
  icon?: ReactNode
}) {
  return (
    <div className="state">
      <div className="state__icon">{icon ?? <Icons.inbox size={20} />}</div>
      <div className="state__title">{title}</div>
      {children && <p className="state__body">{children}</p>}
      {action}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  const apiError = error instanceof ApiError ? error : null
  const message =
    apiError?.message ??
    (error instanceof Error ? error.message : 'Une erreur inattendue est survenue.')
  const findings = apiError?.findings ?? []
  const requestId = apiError?.details?.requestId as string | undefined

  return (
    <div className="state">
      <div className="state__icon" style={{ color: 'var(--danger)' }}>
        <Icons.alert size={20} />
      </div>
      <div className="state__title">Impossible d’afficher ces données</div>
      <p className="state__body">{message}</p>
      {findings.length > 0 && (
        <ul
          className="state__body"
          style={{ textAlign: 'left', paddingLeft: '1.1rem', margin: 0 }}
        >
          {findings.slice(0, 6).map((f, i) => (
            <li key={i}>{f.message}</li>
          ))}
        </ul>
      )}
      {requestId && (
        <p className="subtle mono">
          Identifiant de requête : {requestId}
        </p>
      )}
      {onRetry && (
        <Button variant="secondary" icon={<Icons.refresh size={14} />} onClick={onRetry}>
          Réessayer
        </Button>
      )}
    </div>
  )
}

/**
 * Renders exactly one of loading / error / empty / content.
 *
 * Every data surface goes through this, which is how the four required states
 * become structurally impossible to forget.
 */
export function AsyncBoundary<T>({
  query,
  skeleton,
  empty,
  isEmpty,
  children,
}: {
  query: {
    isPending: boolean
    isError: boolean
    error: unknown
    data: T | undefined
    refetch?: () => void
  }
  skeleton?: ReactNode
  empty?: ReactNode
  isEmpty?: (data: T) => boolean
  children: (data: T) => ReactNode
}) {
  if (query.isPending) return <>{skeleton ?? <TableSkeleton />}</>
  if (query.isError) return <ErrorState error={query.error} onRetry={query.refetch} />
  if (query.data === undefined) return <>{empty ?? <EmptyState title="Aucune donnée" />}</>
  if (isEmpty?.(query.data)) {
    return <>{empty ?? <EmptyState title="Aucune donnée" />}</>
  }
  return <>{children(query.data)}</>
}

// --------------------------------------------------------------------------- //
// Progress
// --------------------------------------------------------------------------- //

export function Progress({
  segments,
  total,
  caption,
}: {
  segments: Array<{ label: string; value: number; color: string }>
  total: number
  caption?: ReactNode
}) {
  const safeTotal = Math.max(total, 1)
  return (
    <div className="progress">
      <div
        className="progress__track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={segments[0]?.value ?? 0}
      >
        {segments.map((s) => (
          <div
            key={s.label}
            className="progress__fill"
            style={{ width: `${(s.value / safeTotal) * 100}%`, background: s.color }}
            title={`${s.label} : ${s.value}`}
          />
        ))}
      </div>
      <div className="progress__legend">
        {segments.map((s) => (
          <span key={s.label} className="progress__legend-item">
            <span className="progress__swatch" style={{ background: s.color }} />
            {s.label} <strong className="num">{s.value}</strong>
          </span>
        ))}
        {caption && <span className="spacer" />}
        {caption}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// KPI tile
// --------------------------------------------------------------------------- //

export function Kpi({
  label,
  value,
  unit,
  compare,
  tone,
  source,
  hero = false,
  hint,
}: {
  label: ReactNode
  value: ReactNode
  unit?: string
  /** Comparison line: prior period, target, or the complementary measure. */
  compare?: ReactNode
  tone?: 'pos' | 'neg' | 'neutral'
  /**
   * Provenance and freshness — a KPI without them is not trustworthy.
   *
   * Inside a carousel, use it on every board or none: it adds a third line, and
   * a board one line taller than its neighbours makes the whole strip change
   * size at each arrow press.
   */
  source?: ReactNode
  hero?: boolean
  hint?: string
}) {
  return (
    <div className={`kpi${hero ? ' kpi--hero' : ''}`}>
      <div className="kpi__label">
        {label}
        {hint && (
          <span title={hint} style={{ color: 'var(--fg-subtle)', display: 'grid' }}>
            <Icons.info size={13} />
          </span>
        )}
      </div>
      <div className={`kpi__value${tone ? ` ${tone}` : ''}`}>
        {value}
        {unit && <span className="kpi__unit">{unit}</span>}
      </div>
      {compare && <div className="kpi__compare">{compare}</div>}
      {source && <div className="kpi__source">{source}</div>}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Sous-navigation d'écran
// --------------------------------------------------------------------------- //

/**
 * La sous-navigation d'un écran : quel volet on regarde.
 *
 * Distincte des pilules à dessein. Une pilule filtre des lignes dans un
 * tableau ; un onglet change ce que l'écran montre. Les deux étaient des
 * rectangles arrondis empilés l'un sous l'autre, et rien ne disait lequel
 * faisait quoi — celui-ci est souligné et posé sur une règle pleine largeur,
 * juste sous le titre, là où le regard cherche « où suis-je ».
 *
 * Un compte à zéro n'est pas affiché : sur un onglet il se lirait comme une
 * anomalie, alors qu'il ne dit rien de plus que l'onglet lui-même.
 */
export function ViewTabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: Array<{ id: T; label: string; count?: number | null; hint?: string }>
  value: T
  onChange: (id: T) => void
}) {
  if (tabs.length <= 1) return null
  return (
    <div className="viewtabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={value === tab.id}
          title={tab.hint}
          className={`viewtabs__item${value === tab.id ? ' viewtabs__item--active' : ''}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count ? <span className="viewtabs__count">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Modal
// --------------------------------------------------------------------------- //

/**
 * Ce qui prend le focus à la tabulation, à l'intérieur d'une fenêtre modale.
 *
 * `:not([disabled])` et `tabindex="-1"` sont exclus : les inclure ferait
 * atterrir le focus sur un bouton grisé, ce qui ressemble à une fenêtre figée.
 */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({
  title,
  onClose,
  footer,
  width,
  children,
}: {
  title: ReactNode
  onClose: () => void
  footer?: ReactNode
  width?: number
  children: ReactNode
}) {
  const titleId = useId()
  const dialog = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Ce qui avait le focus avant l'ouverture — le bouton qui a ouvert la
    // fenêtre, neuf fois sur dix. À la fermeture, le focus y retourne : sans
    // cela il repart au début du document, et un utilisateur au clavier
    // retraverse toute la navigation pour revenir là où il en était.
    const opener = document.activeElement as HTMLElement | null

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      // Le piège de focus. `aria-modal` dit aux lecteurs d'écran que le reste
      // de la page n'existe plus ; la tabulation, elle, continuait d'en sortir
      // et de parcourir la grille, la navigation latérale et la barre du
      // navigateur — le tout invisible derrière l'overlay. On revient donc au
      // premier élément après le dernier, et inversement.
      const focusable = dialog.current?.querySelectorAll<HTMLElement>(FOCUSABLE)
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) return
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog.current?.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // Le focus entre dans la fenêtre à l'ouverture. Il restait sur le bouton
    // d'origine : un lecteur d'écran annonçait la fenêtre sans y être, et la
    // première tabulation partait dans la page du dessous.
    const initial =
      dialog.current?.querySelector<HTMLElement>(FOCUSABLE) ?? dialog.current
    initial?.focus()

    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
      opener?.focus?.()
    }
  }, [onClose])

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="modal"
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        // La fenêtre elle-même reçoit le focus quand elle ne contient rien de
        // focalisable — une fenêtre de lecture seule. `-1` la rend atteignable
        // par script sans l'insérer dans l'ordre de tabulation.
        tabIndex={-1}
        style={width ? ({ '--modal-width': `${width}px` } as React.CSSProperties) : undefined}
      >
        <header className="modal__head">
          <h2 className="modal__title" id={titleId}>
            {title}
          </h2>
          <Button variant="ghost" icon={<Icons.x size={16} />} onClick={onClose} aria-label="Fermer" />
        </header>
        <div className="modal__body">{children}</div>
        {footer && <footer className="modal__foot">{footer}</footer>}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Toasts
// --------------------------------------------------------------------------- //

type Toast = { id: number; tone: 'info' | 'success' | 'warning' | 'danger'; title: string; body?: string }
type ToastApi = {
  push: (toast: Omit<Toast, 'id'>) => void
  success: (title: string, body?: string) => void
  /**
   * L'opération a abouti, mais pas à ce qu'on attendait.
   *
   * « Zéro ligne lue » n'est ni un succès ni une erreur : la requête a
   * fonctionné et n'a rien ramené. Faute de ce ton, le message partait en vert
   * — un petit mensonge, et celui qui fait qu'on cherche l'explication ailleurs.
   */
  warning: (title: string, body?: string) => void
  error: (title: string, body?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)

  const push = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = nextId.current++
    setToasts((current) => [...current, { ...toast, id }])
    // Errors and warnings stay longer: they carry something to act on, and a
    // sentence naming a table and a period is not read in four seconds.
    const ttl = toast.tone === 'danger' || toast.tone === 'warning' ? 9000 : 4500
    window.setTimeout(() => setToasts((c) => c.filter((t) => t.id !== id)), ttl)
  }, [])

  const value = useMemo<ToastApi>(
    () => ({
      push,
      success: (title, body) => push({ tone: 'success', title, body }),
      warning: (title, body) => push({ tone: 'warning', title, body }),
      error: (title, body) => push({ tone: 'danger', title, body }),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts" role="region" aria-live="polite" aria-label="Notifications">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="toast__title">{toast.title}</div>
              {toast.body && <div className="toast__body">{toast.body}</div>}
            </div>
            <Button
              variant="ghost"
              icon={<Icons.x size={13} />}
              onClick={() => setToasts((c) => c.filter((t) => t.id !== toast.id))}
              aria-label="Fermer"
            />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}

/** Turn any thrown value into a toast, using the backend's own wording. */
export function useErrorToast() {
  const toast = useToast()
  return useCallback(
    (error: unknown, fallback = 'Opération impossible') => {
      const apiError = error instanceof ApiError ? error : null
      const findings = apiError?.findings ?? []
      toast.error(
        apiError?.message ?? (error instanceof Error ? error.message : fallback),
        findings.length ? findings.slice(0, 3).map((f) => f.message).join(' · ') : undefined,
      )
    },
    [toast],
  )
}

// --------------------------------------------------------------------------- //
// Misc
// --------------------------------------------------------------------------- //

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  children: ReactNode
}) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
      {hint && !error && <span className="field__hint">{hint}</span>}
      {error && <span className="field__error">{error}</span>}
    </label>
  )
}

/**
 * A slide deck for figures that do not all fit above the fold.
 *
 * The campaign header used to show three progress cards and nothing else, which
 * meant the money — book value, net and gross variance, reliability — lived
 * three clicks away in a tab. Stacking every board would push the actual work
 * off the screen instead. So they take turns.
 *
 * Arrows and dots, no auto-play: a panel that moves on its own while somebody
 * is reading it is a panel nobody trusts. The slide is remembered per key, so
 * navigating away and back does not reset the board you were on.
 */
export function Carousel({
  slides,
  storageKey,
  alignColumns = false,
}: {
  slides: Array<{ id: string; label: string; content: ReactNode }>
  storageKey?: string
  /**
   * Lay every board on the widest board's grid.
   *
   * Left to itself each board sizes its cards to fill the row, so a board of
   * four gets visibly wider cards than a board of five: switching slides then
   * changes the shape of the strip, which reads as the *page* moving rather
   * than the numbers changing. The widest board is measured rather than
   * declared — a count written down here would be wrong the first time somebody
   * adds a KPI and never notices.
   */
  alignColumns?: boolean
}) {
  const [index, setIndex] = useState(() => {
    if (!storageKey) return 0
    try {
      const saved = Number(window.localStorage.getItem(`carousel.${storageKey}`))
      return Number.isInteger(saved) && saved >= 0 ? saved : 0
    } catch {
      return 0
    }
  })

  const count = slides.length
  const current = count ? Math.min(index, count - 1) : 0

  // The boards are not the same height — three progress cards are taller than
  // one row of figures. Sizing the viewport to the tallest would leave a band
  // of empty page under the short ones; sizing it to the active slide keeps the
  // page tight and still never jumps mid-read, because it only changes when the
  // user moves.
  const slideRefs = useRef<Array<HTMLDivElement | null>>([])
  const [height, setHeight] = useState<number | undefined>(undefined)
  // Cards on the widest board. Counted from what is actually rendered, because
  // a board can arrive late — a skeleton first, then five figures — and a
  // number written down in the source would be right until somebody adds a KPI.
  const [columns, setColumns] = useState(0)
  useEffect(() => {
    const node = slideRefs.current[current]
    if (!node) return
    const measure = () => {
      setHeight(node.offsetHeight)
      setColumns(
        Math.max(
          0,
          ...slideRefs.current.map(
            (slide) => slide?.querySelectorAll('.grid--kpi > .kpi').length ?? 0,
          ),
        ),
      )
    }
    measure()
    const observer = new ResizeObserver(measure)
    // Every slide, not just the visible one: the widest board is often one the
    // reader has not opened yet, and its cards land while another is on screen.
    for (const slide of slideRefs.current) if (slide) observer.observe(slide)
    return () => observer.disconnect()
  }, [current, slides.length])

  const go = (next: number) => {
    const clamped = Math.max(0, Math.min(next, count - 1))
    setIndex(clamped)
    if (storageKey) {
      try {
        window.localStorage.setItem(`carousel.${storageKey}`, String(clamped))
      } catch {
        /* the carousel still works for this session */
      }
    }
  }

  if (count === 0) return null
  if (count === 1) return <>{slides[0]!.content}</>

  return (
    <section
      className={`carousel${alignColumns && columns ? ' carousel--fixed-columns' : ''}`}
      style={
        alignColumns && columns
          ? ({ '--kpi-columns': columns } as React.CSSProperties)
          : undefined
      }
      aria-roledescription="carrousel"
      aria-label="Indicateurs de la campagne"
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') go(current - 1)
        if (event.key === 'ArrowRight') go(current + 1)
      }}
    >
      <div className="carousel__viewport" style={{ height }}>
        <div
          className="carousel__track"
          style={{ transform: `translateX(-${current * 100}%)` }}
        >
          {slides.map((slide, i) => (
            <div
              key={slide.id}
              ref={(node) => {
                slideRefs.current[i] = node
              }}
              className="carousel__slide"
              role="group"
              aria-roledescription="diapositive"
              aria-label={`${slide.label} — ${i + 1} sur ${count}`}
              aria-hidden={i !== current}
              // A hidden slide keeps its layout but leaves the tab order: a
              // focus ring on something off-screen is a trap.
              {...(i !== current ? { inert: '' } : {})}
            >
              {slide.content}
            </div>
          ))}
        </div>
      </div>

      <div className="carousel__nav">
        <button
          className="carousel__arrow"
          onClick={() => go(current - 1)}
          disabled={current === 0}
          aria-label="Indicateurs précédents"
        >
          <Icons.chevronLeft size={15} />
        </button>
        <span className="carousel__label">{slides[current]!.label}</span>
        <div className="carousel__dots" role="tablist">
          {slides.map((slide, i) => (
            <button
              key={slide.id}
              role="tab"
              className={`carousel__dot${i === current ? ' carousel__dot--active' : ''}`}
              aria-selected={i === current}
              aria-label={slide.label}
              onClick={() => go(i)}
            />
          ))}
        </div>
        <button
          className="carousel__arrow"
          onClick={() => go(current + 1)}
          disabled={current === count - 1}
          aria-label="Indicateurs suivants"
        >
          <Icons.chevronRight size={15} />
        </button>
      </div>
    </section>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  title,
  disabled = false,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label: ReactNode
  title?: string
  disabled?: boolean
}) {
  return (
    <label className="switch" title={title}>
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="switch__track" aria-hidden="true" />
      <span className="switch__label">{label}</span>
    </label>
  )
}

export function SearchInput({
  value,
  onChange,
  placeholder = 'Rechercher…',
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
}) {
  return (
    <div className="search">
      <span className="search__icon">
        <Icons.search size={14} />
      </span>
      <input
        className="input"
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

/** A read-only cell that shows "—" rather than an empty gap. */
export function Cell({ children }: { children: ReactNode }) {
  return <>{children === null || children === undefined || children === '' ? DASH : children}</>
}

/** A horizontal magnitude bar inside a table cell (IBCS: density, not clutter). */
export function CellBar({
  value,
  max,
  tone,
}: {
  value: number
  max: number
  tone?: 'pos' | 'neg' | 'accent'
}) {
  if (!max) return null
  const ratio = Math.min(Math.abs(value) / max, 1)
  const color =
    tone === 'pos'
      ? 'var(--variance-positive)'
      : tone === 'neg'
        ? 'var(--variance-negative)'
        : 'var(--accent)'
  return (
    <span className="cell-bar" aria-hidden="true">
      <span
        className="cell-bar__fill"
        style={{ width: `${ratio * 100}%`, background: color, left: 0 }}
      />
    </span>
  )
}
