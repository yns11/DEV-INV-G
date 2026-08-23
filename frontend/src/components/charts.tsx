/**
 * Charts — hand-rolled SVG, no charting dependency.
 *
 * The vocabulary is deliberately narrow (bars, lines, variance bars, Pareto)
 * because those are the four comparisons this product actually needs, and IBCS
 * discourages the rest. Building them directly gives exact control over the
 * notation rules that matter:
 *
 *  - a variance bar is coloured by **sign**, and the "higher is good?" question
 *    is answered per metric by the caller, never assumed;
 *  - axes are not truncated: a bar chart always includes zero;
 *  - series that must be compared share a scale;
 *  - values are labelled directly rather than forcing an axis read.
 */

import { useId, type ReactNode } from 'react'
import { moneyShort, qty, percent } from '../lib/format'

type Formatter = (value: number) => string

// --------------------------------------------------------------------------- //
// Scale helpers
// --------------------------------------------------------------------------- //

/** A "nice" upper bound so gridlines land on readable numbers. */
function niceMax(value: number): number {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  const normalised = value / magnitude
  const step = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10
  return step * magnitude
}

// --------------------------------------------------------------------------- //
// Horizontal variance bars
// --------------------------------------------------------------------------- //

export interface BarDatum {
  label: string
  value: number
  /** Optional secondary value shown as a lighter reference bar (prior period). */
  reference?: number
  meta?: ReactNode
}

/**
 * Horizontal bars diverging from a zero line.
 *
 * Structure runs vertically (categories stacked), which is the IBCS convention
 * for a structural comparison — time is the only thing laid out horizontally.
 */
export function VarianceBars({
  data,
  format = moneyShort,
  height = 22,
  maxBars = 15,
  /** `true` when a positive value is good news for this metric. */
  positiveIsGood = true,
  labelWidth = 190,
}: {
  data: BarDatum[]
  format?: Formatter
  height?: number
  maxBars?: number
  positiveIsGood?: boolean
  labelWidth?: number
}) {
  const rows = data.slice(0, maxBars)
  if (rows.length === 0) return null

  // Domain always includes zero (IBCS: never truncate a bar axis) but is not
  // forced to be symmetric — a set of same-sign values would otherwise waste
  // half the canvas and halve the resolution of every bar.
  const values = rows.map((r) => r.value)
  const lower = Math.min(0, ...values)
  const upper = Math.max(0, ...values)
  const negativeSpan = niceMax(Math.abs(lower))
  const positiveSpan = niceMax(upper)
  const span = negativeSpan + positiveSpan || 1
  const chartWidth = 520
  const zeroX = labelWidth + (negativeSpan / span) * chartWidth
  const scale = (value: number) => (value / span) * chartWidth
  const total = rows.length * (height + 6) + 26
  // Room for the value label on whichever side the bars actually extend.
  const rightPad = positiveSpan > 0 ? 86 : 12

  const goodColor = 'var(--variance-positive)'
  const badColor = 'var(--variance-negative)'

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${labelWidth + chartWidth + rightPad} ${total}`}
      role="img"
      aria-label="Écarts par catégorie"
      style={{ maxWidth: '100%' }}
    >
      {/* zero line — never omitted, it is what makes the bars readable */}
      <line
        className="zero-line"
        x1={zeroX}
        y1={16}
        x2={zeroX}
        y2={total - 8}
      />
      <text x={zeroX} y={10} textAnchor="middle" fontSize={9}>
        0
      </text>

      {rows.map((row, index) => {
        const y = 20 + index * (height + 6)
        const length = scale(row.value)
        const isGood = positiveIsGood ? row.value >= 0 : row.value <= 0
        const color = row.value === 0 ? 'var(--variance-neutral)' : isGood ? goodColor : badColor
        const barX = row.value >= 0 ? zeroX : zeroX + length
        return (
          <g key={`${row.label}-${index}`}>
            <text x={labelWidth - 8} y={y + height / 2 + 4} textAnchor="end">
              {row.label.length > 26 ? `${row.label.slice(0, 25)}…` : row.label}
            </text>
            <rect
              x={barX}
              y={y}
              width={Math.max(Math.abs(length), 1)}
              height={height}
              rx={2}
              fill={color}
              opacity={0.88}
            >
              <title>{`${row.label} : ${format(row.value)}`}</title>
            </rect>
            <text
              className="value-label"
              x={row.value >= 0 ? zeroX + Math.abs(length) + 6 : zeroX - Math.abs(length) - 6}
              y={y + height / 2 + 4}
              textAnchor={row.value >= 0 ? 'start' : 'end'}
            >
              {format(row.value)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// --------------------------------------------------------------------------- //
// Column chart with an optional reference series
// --------------------------------------------------------------------------- //

export function Columns({
  data,
  format = qty,
  height = 200,
  referenceLabel,
  seriesLabel = 'Réel',
}: {
  data: BarDatum[]
  format?: Formatter
  height?: number
  /** IBCS: prior period is a lighter solid fill, never a different hue. */
  referenceLabel?: string
  seriesLabel?: string
}) {
  const gradientId = useId()
  if (data.length === 0) return null

  const values = data.flatMap((d) => [d.value, d.reference ?? 0])
  const max = niceMax(Math.max(...values.map(Math.abs), 1))
  const width = Math.max(data.length * 64, 320)
  const padTop = 18
  const padBottom = 40
  const plotHeight = height - padTop - padBottom
  const barWidth = Math.min(34, (width / data.length) * 0.5)
  const slot = width / data.length

  return (
    <>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Comparaison par catégorie"
        style={{ maxWidth: '100%' }}
      >
        <defs>
          <pattern id={gradientId} width="4" height="4" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <line x1="0" y1="0" x2="0" y2="4" stroke="var(--cat-1)" strokeWidth="2" />
          </pattern>
        </defs>

        {/* Gridlines at 0, 50 %, 100 % of the nice max */}
        {[0, 0.5, 1].map((ratio) => {
          const y = padTop + plotHeight * (1 - ratio)
          return (
            <g key={ratio}>
              <line className="grid-line" x1={0} y1={y} x2={width} y2={y} />
              <text x={2} y={y - 3} fontSize={9}>
                {format(max * ratio)}
              </text>
            </g>
          )
        })}

        {data.map((datum, index) => {
          const cx = index * slot + slot / 2
          const h = (Math.abs(datum.value) / max) * plotHeight
          const refH = datum.reference ? (Math.abs(datum.reference) / max) * plotHeight : 0
          return (
            <g key={`${datum.label}-${index}`}>
              {datum.reference !== undefined && (
                <rect
                  x={cx - barWidth / 2 - 5}
                  y={padTop + plotHeight - refH}
                  width={barWidth}
                  height={Math.max(refH, 1)}
                  rx={2}
                  fill="var(--fg-muted)"
                  opacity={0.32}
                />
              )}
              <rect
                x={cx - barWidth / 2 + (datum.reference !== undefined ? 5 : 0)}
                y={padTop + plotHeight - h}
                width={barWidth}
                height={Math.max(h, 1)}
                rx={2}
                fill="var(--cat-1)"
              >
                <title>{`${datum.label} : ${format(datum.value)}`}</title>
              </rect>
              <text
                x={cx}
                y={height - padBottom + 14}
                textAnchor="middle"
                fontSize={9.5}
              >
                {datum.label.length > 12 ? `${datum.label.slice(0, 11)}…` : datum.label}
              </text>
              <text
                className="value-label"
                x={cx}
                y={padTop + plotHeight - h - 4}
                textAnchor="middle"
              >
                {format(datum.value)}
              </text>
            </g>
          )
        })}
        <line className="axis-line" x1={0} y1={padTop + plotHeight} x2={width} y2={padTop + plotHeight} />
      </svg>
      {referenceLabel && (
        <div className="chart-legend">
          <span className="chart-legend__item">
            <span className="chart-legend__swatch" style={{ background: 'var(--cat-1)' }} />
            {seriesLabel}
          </span>
          <span className="chart-legend__item">
            <span
              className="chart-legend__swatch"
              style={{ background: 'var(--fg-muted)', opacity: 0.32 }}
            />
            {referenceLabel}
          </span>
        </div>
      )}
    </>
  )
}

// --------------------------------------------------------------------------- //
// Pareto: bars + cumulative line
// --------------------------------------------------------------------------- //

export function Pareto({
  data,
  format = moneyShort,
  height = 260,
  coverage = 0.8,
  maxBars = 25,
}: {
  /** The **whole** ranked population — not a pre-filtered top-N. */
  data: Array<{ label: string; value: number }>
  format?: Formatter
  height?: number
  coverage?: number
  maxBars?: number
}) {
  if (data.length === 0) return null

  // The cumulative share is computed over the entire population, then only the
  // head is drawn. Computing it over a pre-truncated list would put the 80 %
  // marker at a rank that does not exist — the chart would contradict the
  // number quoted next to it.
  const ranked = [...data].sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
  const grandTotal = ranked.reduce((sum, d) => sum + Math.abs(d.value), 0) || 1

  let running = 0
  const withCumulative = ranked.map((datum) => {
    running += Math.abs(datum.value) / grandTotal
    return { ...datum, cumulative: running }
  })
  const coverageRank = withCumulative.findIndex((d) => d.cumulative >= coverage) + 1

  const shown = withCumulative.slice(0, maxBars)
  const hidden = ranked.length - shown.length
  const max = niceMax(Math.max(...shown.map((d) => Math.abs(d.value))))

  const padLeft = 48
  const padRight = 46
  const padTop = 20
  const padBottom = 76 // rotated category labels need the room
  const width = Math.max(shown.length * 42 + padLeft + padRight, 380)
  const plotHeight = height - padTop - padBottom
  const plotWidth = width - padLeft - padRight
  const slot = plotWidth / shown.length
  const barWidth = Math.min(26, slot * 0.62)

  const points = shown.map((datum, index) => ({
    x: padLeft + index * slot + slot / 2,
    y: padTop + plotHeight * (1 - datum.cumulative),
  }))
  const markerVisible = coverageRank > 0 && coverageRank <= shown.length
  const marker = markerVisible ? points[coverageRank - 1] : undefined

  return (
    <>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Courbe de Pareto des écarts"
        style={{ maxWidth: '100%' }}
      >
        {[0, 0.5, 1].map((ratio) => {
          const y = padTop + plotHeight * (1 - ratio)
          return (
            <g key={ratio}>
              <line className="grid-line" x1={padLeft} y1={y} x2={width - padRight} y2={y} />
              <text x={2} y={y + 3} fontSize={9}>
                {format(max * ratio)}
              </text>
              <text x={width - 2} y={y + 3} fontSize={9} textAnchor="end" fill="var(--cat-4)">
                {percent(ratio, 1)}
              </text>
            </g>
          )
        })}

        {marker && (
          <>
            <line
              x1={marker.x}
              y1={padTop}
              x2={marker.x}
              y2={padTop + plotHeight}
              stroke="var(--cat-4)"
              strokeDasharray="4 3"
              strokeWidth={1.5}
            />
            <text x={marker.x + 5} y={padTop + 10} fontSize={9.5} fill="var(--cat-4)">
              {coverageRank} article{coverageRank > 1 ? 's' : ''} = {percent(coverage)}
            </text>
          </>
        )}

        {shown.map((datum, index) => {
          const h = (Math.abs(datum.value) / max) * plotHeight
          const cx = padLeft + index * slot + slot / 2
          const inCoverage = markerVisible ? index < coverageRank : false
          return (
            <g key={`${datum.label}-${index}`}>
              <rect
                x={cx - barWidth / 2}
                y={padTop + plotHeight - h}
                width={barWidth}
                height={Math.max(h, 1)}
                rx={2}
                fill={inCoverage ? 'var(--cat-1)' : 'var(--fg-subtle)'}
                opacity={inCoverage ? 0.9 : 0.4}
              >
                <title>{`${datum.label} : ${format(datum.value)} (cumul ${percent(datum.cumulative)})`}</title>
              </rect>
              <text
                x={cx}
                y={height - padBottom + 14}
                textAnchor="end"
                fontSize={9}
                transform={`rotate(-42 ${cx} ${height - padBottom + 14})`}
              >
                {datum.label.length > 14 ? `${datum.label.slice(0, 13)}…` : datum.label}
              </text>
            </g>
          )
        })}

        <polyline
          points={points.map((p) => `${p.x},${p.y}`).join(' ')}
          fill="none"
          stroke="var(--cat-4)"
          strokeWidth={2}
        />
        {points.map((p, index) => (
          <circle key={index} cx={p.x} cy={p.y} r={2.5} fill="var(--cat-4)" />
        ))}
        <line
          className="axis-line"
          x1={padLeft}
          y1={padTop + plotHeight}
          x2={width - padRight}
          y2={padTop + plotHeight}
        />
      </svg>
      <div className="chart-legend">
        <span className="chart-legend__item">
          <span className="chart-legend__swatch" style={{ background: 'var(--cat-1)' }} />
          Contribue aux {percent(coverage)} premiers
        </span>
        <span className="chart-legend__item">
          <span className="chart-legend__swatch" style={{ background: 'var(--cat-4)' }} />
          Part cumulée (sur {ranked.length} articles)
        </span>
        {hidden > 0 && (
          <span className="chart-legend__item subtle">
            {hidden} article(s) de moindre contribution non affichés
          </span>
        )}
      </div>
    </>
  )
}

// --------------------------------------------------------------------------- //
// Distribution (Benford, digit preference)
// --------------------------------------------------------------------------- //

export function DistributionChart({
  labels,
  observed,
  expected,
  observedLabel = 'Observé',
  expectedLabel = 'Attendu',
  height = 180,
}: {
  labels: Array<string | number>
  observed: number[]
  expected: number[]
  observedLabel?: string
  expectedLabel?: string
  height?: number
}) {
  if (observed.length === 0) return null
  const max = Math.max(...observed, ...expected, 0.01)
  const width = Math.max(labels.length * 46, 300)
  const padTop = 14
  const padBottom = 30
  const plotHeight = height - padTop - padBottom
  const slot = width / labels.length
  const barWidth = Math.min(20, slot * 0.42)

  return (
    <>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Distribution observée contre distribution attendue"
        style={{ maxWidth: '100%' }}
      >
        {labels.map((label, index) => {
          const cx = index * slot + slot / 2
          const ho = ((observed[index] ?? 0) / max) * plotHeight
          const he = ((expected[index] ?? 0) / max) * plotHeight
          return (
            <g key={String(label)}>
              <rect
                x={cx - barWidth - 1}
                y={padTop + plotHeight - ho}
                width={barWidth}
                height={Math.max(ho, 1)}
                rx={2}
                fill="var(--cat-1)"
              >
                <title>{`${observedLabel} ${label} : ${percent(observed[index] ?? 0)}`}</title>
              </rect>
              <rect
                x={cx + 1}
                y={padTop + plotHeight - he}
                width={barWidth}
                height={Math.max(he, 1)}
                rx={2}
                fill="var(--fg-muted)"
                opacity={0.35}
              >
                <title>{`${expectedLabel} ${label} : ${percent(expected[index] ?? 0)}`}</title>
              </rect>
              <text x={cx} y={height - padBottom + 13} textAnchor="middle" fontSize={10}>
                {label}
              </text>
            </g>
          )
        })}
        <line className="axis-line" x1={0} y1={padTop + plotHeight} x2={width} y2={padTop + plotHeight} />
      </svg>
      <div className="chart-legend">
        <span className="chart-legend__item">
          <span className="chart-legend__swatch" style={{ background: 'var(--cat-1)' }} />
          {observedLabel}
        </span>
        <span className="chart-legend__item">
          <span
            className="chart-legend__swatch"
            style={{ background: 'var(--fg-muted)', opacity: 0.35 }}
          />
          {expectedLabel}
        </span>
      </div>
    </>
  )
}

// --------------------------------------------------------------------------- //
// Composition bar (part-to-whole; a stacked bar, never a pie)
// --------------------------------------------------------------------------- //

export function CompositionBar({
  segments,
  format = moneyShort,
}: {
  segments: Array<{ label: string; value: number; color?: string }>
  format?: Formatter
}) {
  const total = segments.reduce((sum, s) => sum + Math.abs(s.value), 0)
  if (total === 0) return null
  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <div
        style={{
          display: 'flex',
          height: 26,
          borderRadius: 'var(--radius-sm)',
          overflow: 'hidden',
          border: '1px solid var(--border)',
        }}
      >
        {segments.map((segment, index) => (
          <div
            key={segment.label}
            title={`${segment.label} : ${format(segment.value)}`}
            style={{
              width: `${(Math.abs(segment.value) / total) * 100}%`,
              background: segment.color ?? `var(--cat-${(index % 8) + 1})`,
            }}
          />
        ))}
      </div>
      <div className="chart-legend">
        {segments.map((segment, index) => (
          <span key={segment.label} className="chart-legend__item">
            <span
              className="chart-legend__swatch"
              style={{ background: segment.color ?? `var(--cat-${(index % 8) + 1})` }}
            />
            {segment.label}
            <strong className="num">{format(segment.value)}</strong>
            <span className="subtle">{percent(Math.abs(segment.value) / total)}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Waterfall
// --------------------------------------------------------------------------- //

export interface WaterfallStep {
  label: string
  value: number
  /**
   * A total rather than a movement: drawn from the baseline, not stacked on the
   * previous bar. The two ends of a reconciliation are totals; the six terms
   * between them are movements.
   */
  terminal?: boolean
}

/**
 * A running balance, term by term.
 *
 * The only shape in which a chain of six additions and subtractions reads as one
 * story instead of seven separate numbers: each bar starts where the previous
 * one ended, so the eye follows the balance down the page rather than comparing
 * heights.
 *
 * Colour carries the *direction* of the movement and nothing else — an addition
 * is an addition whether or not the analyst is pleased about it. Judging them
 * would be a second meaning on the same channel, and the two would be read at
 * once.
 */
export function Waterfall({
  data,
  format = qty,
  height = 260,
}: {
  data: WaterfallStep[]
  format?: Formatter
  height?: number
}) {
  if (data.length === 0) return null

  // Walk the chain once to find where every bar starts and ends, then scale to
  // the full excursion — including the running balance, which can go higher
  // than any single bar.
  let running = 0
  const spans = data.map((step) => {
    if (step.terminal) return { start: 0, end: step.value, step }
    const start = running
    running += step.value
    return { start, end: running, step }
  })

  const bounds = spans.flatMap((s) => [s.start, s.end])
  const upper = niceMax(Math.max(0, ...bounds))
  const lower = -niceMax(Math.abs(Math.min(0, ...bounds)))
  const span = upper - lower || 1

  const width = Math.max(data.length * 96, 420)
  const padTop = 22
  const padBottom = 52
  const plot = height - padTop - padBottom
  const slot = width / data.length
  const barWidth = Math.min(46, slot * 0.5)
  const y = (value: number) => padTop + ((upper - value) / span) * plot

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Chaîne des flux de la période"
      style={{ maxWidth: '100%' }}
    >
      <line className="zero-line" x1={0} y1={y(0)} x2={width} y2={y(0)} />

      {spans.map(({ start, end, step }, index) => {
        const centre = index * slot + slot / 2
        const top = Math.min(y(start), y(end))
        const barHeight = Math.max(Math.abs(y(end) - y(start)), 2)
        const colour = step.terminal
          ? 'var(--cat-1)'
          : end >= start
            ? 'var(--variance-positive)'
            : 'var(--variance-negative)'
        return (
          <g key={`${step.label}-${index}`}>
            {/* Le trait de liaison, du bord droit de la barre précédente au
                bord gauche de celle-ci. C'est lui qui fait lire la chaîne comme
                une chaîne plutôt que comme huit barres côte à côte — sans lui,
                rien ne dit que chaque barre repart où l'autre s'arrête. */}
            {index > 0 && !step.terminal && (
              <line
                x1={(index - 1) * slot + slot / 2 + barWidth / 2}
                y1={y(start)}
                x2={centre - barWidth / 2}
                y2={y(start)}
                stroke="var(--fg-subtle)"
                strokeDasharray="3 3"
                opacity={0.6}
              />
            )}
            <rect
              x={centre - barWidth / 2}
              y={top}
              width={barWidth}
              height={barHeight}
              rx={2}
              fill={colour}
              opacity={step.terminal ? 0.95 : 0.85}
            >
              <title>{`${step.label} : ${format(step.value)}`}</title>
            </rect>
            <text
              className="value-label"
              x={centre}
              y={top - 5}
              textAnchor="middle"
            >
              {format(step.value)}
            </text>
            <text
              x={centre}
              y={height - padBottom + 16}
              textAnchor="middle"
              fontSize={10}
            >
              {step.label.length > 18 ? `${step.label.slice(0, 17)}…` : step.label}
            </text>
            {step.terminal && (
              <text
                x={centre}
                y={height - padBottom + 30}
                textAnchor="middle"
                fontSize={9}
                fill="var(--fg-subtle)"
              >
                total
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
