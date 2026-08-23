/**
 * Les constats de contrôle, regroupés par contrôle.
 *
 * Cinquante lignes disant la même chose de cinquante articles différents ne
 * sont pas cinquante informations : c'est une seule, enterrée sous ses propres
 * répétitions, et elle chasse tout le reste de l'écran. La liste montre donc le
 * contrôle et son nombre d'occurrences ; les articles concernés s'ouvrent d'un
 * clic sur « voir plus ».
 *
 * Le regroupement est calculé côté serveur (`inventory.domain.controls.
 * group_findings`) : les libellés y vivent déjà, et un second regroupement ici
 * finirait par ne plus dire la même chose que le premier. Ce composant reçoit
 * les groupes et la liste plate, et retrouve les occurrences d'un groupe en
 * filtrant la seconde — deux dénombrements dérivés du même tableau ne peuvent
 * pas se contredire.
 */

import { useState } from 'react'
import type { Finding, FindingGroup } from '../lib/types'
import { DASH } from '../lib/format'
import { Alert, Badge, Button } from './ui'

const TONE: Record<string, string> = {
  BLOCKER: 'danger',
  WARNING: 'warning',
  INFO: 'info',
}

const SEVERITY_LABELS: Record<string, string> = {
  BLOCKER: 'Bloquant',
  WARNING: 'Avertissement',
  INFO: 'Information',
}

/** Combien d'occurrences on montre avant de s'arrêter, une fois déplié. */
const DETAIL_CEILING = 200

/**
 * Sur quoi porte un constat.
 *
 * Tous ne portent pas sur un article : un journal en double porte sur un
 * emplacement, un cycle de nomenclature sur un chemin d'assemblages. La colonne
 * affichait « article » et donc un tiret pour tous ceux-là — c'est-à-dire
 * exactement rien de ce qui permet d'aller voir. Elle affiche maintenant la
 * coordonnée la plus précise que le constat porte.
 */
function subjectOf(finding: Finding): string {
  const place = [finding.warehouse_id, finding.location_id].filter(Boolean).join(' / ')
  if (finding.item_number && place) return `${finding.item_number} · ${place}`
  if (finding.item_number) return finding.item_number
  if (place) return place
  const cycle = finding.context?.cycle
  if (Array.isArray(cycle)) return cycle.join(' → ')
  return DASH
}

export function FindingGroups({
  groups,
  findings,
  emptyLabel = 'Aucune anomalie détectée',
}: {
  groups: FindingGroup[]
  findings: Finding[]
  emptyLabel?: string
}) {
  const [open, setOpen] = useState<Set<string>>(new Set())

  if (groups.length === 0) return <Alert tone="success" title={emptyLabel} />

  const toggle = (code: string) => {
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }}>
      {groups.map((group) => {
        const shown = open.has(group.code)
        const occurrences = findings.filter((f) => f.code === group.code)
        // Un constat isolé n'a rien à replier : son message *est* le détail, et
        // le cacher derrière un lien ferait cliquer pour une seule ligne.
        const single = group.count === 1 ? occurrences[0] : undefined

        return (
          <div
            key={group.code}
            style={{
              padding: 'var(--space-3)',
              background: 'var(--bg-inset)',
              borderRadius: 'var(--radius-md)',
            }}
          >
            <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
              <Badge tone={TONE[group.severity] ?? 'neutral'}>
                {SEVERITY_LABELS[group.severity] ?? group.severity}
              </Badge>
              <strong style={{ fontSize: 'var(--text-sm)' }}>
                {single ? single.message : group.label}
              </strong>
              {!single && (
                <>
                  {/* « constat » et non « article » : tous ne portent pas sur
                      un article — un journal en double porte sur un
                      emplacement, un cycle sur un chemin d'assemblages. */}
                  <span className="subtle num">{group.count} constat(s)</span>
                  <span className="spacer" />
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-expanded={shown}
                    onClick={() => toggle(group.code)}
                  >
                    {shown ? 'Masquer le détail' : '… Voir plus'}
                  </Button>
                </>
              )}
            </div>

            {shown && !single && (
              <div
                className="table-wrap"
                style={{ maxHeight: 260, marginTop: 'var(--space-3)' }}
              >
                <table className="data">
                  <thead>
                    <tr>
                      <th style={{ width: 200 }}>Concerne</th>
                      <th>Constat</th>
                    </tr>
                  </thead>
                  <tbody>
                    {occurrences.slice(0, DETAIL_CEILING).map((finding, index) => (
                      <tr key={`${finding.item_number}-${index}`}>
                        <td className="mono">{subjectOf(finding)}</td>
                        <td>{finding.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {occurrences.length > DETAIL_CEILING && (
                  <div className="subtle" style={{ padding: 'var(--space-2)' }}>
                    {DETAIL_CEILING} premières occurrences sur {occurrences.length} —
                    l’export du dossier de campagne les porte toutes.
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
