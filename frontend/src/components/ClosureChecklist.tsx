/**
 * L'état des lieux du dossier avant le seul geste irréversible.
 *
 * Le même composant sert à deux endroits, et c'est le point : dans la fenêtre
 * qui clôture, où il remplace la liste de reproches d'avant, et sur l'écran
 * d'analyse, où il se lit des jours plus tôt. Deux rendus séparés auraient
 * dérivé — celui qu'on ouvre rarement en premier.
 *
 * Trois tons, dans l'ordre où on les lit. Ce qui **arrête** vient de la même
 * fonction que le refus du serveur : l'écran ne peut donc pas annoncer « prêt »
 * sur une campagne que la clôture refusera. Ce qui **mérite un regard**
 * n'empêche rien — le taire reviendrait à faire comme si cela n'existait pas.
 * Ce qui est **fait** figure aussi : une liste qui ne montre que les reproches
 * se lit comme une machine à empêcher, là où l'on vient chercher un état des
 * lieux.
 */

import { Link } from 'react-router-dom'
import type { ChecklistItem, ChecklistState, ClosureChecklist } from '../lib/types'
import { Badge, Icons, Skeleton } from './ui'

/** Ce que chaque ton porte : sa pastille, son icône, son mot. */
const TONES: Record<
  ChecklistState,
  { tone: 'danger' | 'warning' | 'success'; label: string }
> = {
  BLOCKING: { tone: 'danger', label: 'Bloquant' },
  ATTENTION: { tone: 'warning', label: 'À regarder' },
  DONE: { tone: 'success', label: 'Fait' },
}

function Row({ item, campaignId }: { item: ChecklistItem; campaignId: string }) {
  const { tone, label } = TONES[item.state]
  return (
    <li className="stack" style={{ gap: 'var(--space-1)' }}>
      <div className="row" style={{ gap: 'var(--space-2)', alignItems: 'baseline' }}>
        {item.state === 'DONE' ? (
          <Icons.check size={14} aria-hidden="true" />
        ) : (
          <Icons.alert size={14} aria-hidden="true" />
        )}
        <strong>{item.label}</strong>
        <Badge tone={tone}>{label}</Badge>
      </div>
      <span className="subtle">{item.detail}</span>
      {/* Le lien n'apparaît que là où il y a quelque chose à faire : proposer
          d'aller « corriger » un point déjà vert est une invitation à défaire. */}
      {item.where && item.state !== 'DONE' && (
        <Link to={`/campagnes/${campaignId}/${item.where}`} className="link">
          Aller à l’écran concerné
        </Link>
      )}
    </li>
  )
}

export function ClosureChecklistView({
  campaignId,
  data,
  pending,
}: {
  campaignId: string
  data: ClosureChecklist | undefined
  pending?: boolean
}) {
  if (pending || !data) return <Skeleton count={4} />

  const { blocking, attention, done } = data.counts
  return (
    <div className="stack">
      <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
        {/* Le résumé d'abord : « 2 bloquants, 1 à regarder, 6 faits » se lit en
            une seconde, là où huit lignes demandent une lecture. */}
        <Badge tone={blocking > 0 ? 'danger' : 'neutral'}>
          {blocking} bloquant(s)
        </Badge>
        <Badge tone={attention > 0 ? 'warning' : 'neutral'}>
          {attention} à regarder
        </Badge>
        <Badge tone="success">{done} fait(s)</Badge>
      </div>
      <ul
        className="stack"
        style={{ gap: 'var(--space-3)', margin: 0, paddingLeft: '1.1rem' }}
      >
        {data.items.map((item) => (
          <Row key={item.code} item={item} campaignId={campaignId} />
        ))}
      </ul>
    </div>
  )
}
