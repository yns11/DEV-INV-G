/**
 * Le tableau de commandement du jour d'inventaire.
 *
 * L'écran de campagne donnait des barres de progression. « 62 % des zones »
 * répond à « où en est-on », jamais à « que faire maintenant » — et c'est la
 * seconde question qu'on se pose à huit heures du matin, un carnet à la main.
 *
 * Trois choix de présentation, et chacun vient d'un défaut de l'écran d'avant.
 *
 * **Une file vide disparaît.** Six cases dont quatre à zéro, c'est un tableau
 * de bord ; les deux qui appellent quelqu'un, c'est un tableau de commandement.
 *
 * **Les noms, pas seulement le nombre.** « 3 zones à arbitrer » oblige à ouvrir
 * un écran pour savoir lesquelles ; « Z04, Z07, Z12 » permet d'y aller.
 *
 * **L'ordre est celui de l'action.** Ce qui attend une décision passe avant ce
 * qu'on peut fermer, qui passe avant ce qui n'a pas commencé — la file sur
 * laquelle le responsable ne peut rien faire lui-même arrive en dernier.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import { useFocusMode } from '../lib/focus'
import type { Overview, WorkQueue } from '../lib/types'
import { Badge, Card, EmptyState, Icons, Skeleton } from '../components/ui'

/** Le ton de chaque file : ce qui attend une décision se voit en premier. */
const TONES: Record<string, 'danger' | 'warning' | 'accent' | 'neutral'> = {
  ZONES_TO_ARBITRATE: 'danger',
  ZONES_READY_TO_CLOSE: 'accent',
  ZONES_IN_PROGRESS: 'warning',
  JOURNALS_IN_PROGRESS: 'warning',
  ZONES_NOT_STARTED: 'neutral',
  JOURNALS_NOT_STARTED: 'neutral',
}

function QueueCard({ queue, campaignId }: { queue: WorkQueue; campaignId: string }) {
  return (
    <Card
      title={queue.label}
      actions={<Badge tone={TONES[queue.code] ?? 'neutral'}>{queue.count}</Badge>}
    >
      <div className="stack" style={{ gap: 'var(--space-2)' }}>
        <span className="subtle">{queue.action}</span>
        <div className="row-wrap" style={{ gap: 'var(--space-1)' }}>
          {queue.names.map((name) => (
            <span key={name} className="chip mono">
              {name}
            </span>
          ))}
          {/* Ce que la liste ne montre pas. Sans ce nombre, douze noms sur
              quarante se lisent comme quarante. */}
          {queue.hidden > 0 && (
            <span className="subtle">et {queue.hidden} autre(s)</span>
          )}
        </div>
        <Link to={`/campagnes/${campaignId}/${queue.where}`} className="link">
          Ouvrir l’écran
        </Link>
      </div>
    </Card>
  )
}

export function Board() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [focus] = useFocusMode()
  const query = useQuery({
    queryKey: ['work-queues', campaignId, focus],
    queryFn: () => api.workQueues(campaignId, focus),
    // Le jour J, plusieurs personnes saisissent en même temps : un tableau
    // figé depuis vingt minutes envoie quelqu'un sur une zone déjà finie.
    refetchInterval: 60_000,
  })

  if (query.isPending) return <Skeleton count={3} />
  const data = query.data
  if (!data || data.queues.length === 0) {
    return (
      <Card>
        <EmptyState title="Rien n’attend personne" icon={<Icons.check size={20} />}>
          {data?.focus
            ? 'Toutes les zones et tous les journaux de votre périmètre sont traités.'
            : 'Toutes les zones sont fermées et tous les journaux sont postés.'}
        </EmptyState>
      </Card>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
        <Badge tone={data.waiting > 0 ? 'accent' : 'neutral'}>
          {data.waiting} en attente
        </Badge>
        {/* Dire que le tableau est filtré : un « 3 » sur une campagne de
            quarante zones se lit comme une erreur si on ignore le périmètre. */}
        {data.focus && <Badge tone="neutral">Votre périmètre seulement</Badge>}
      </div>
      <div className="grid grid--2">
        {data.queues.map((queue) => (
          <QueueCard key={queue.code} queue={queue} campaignId={campaignId} />
        ))}
      </div>
    </div>
  )
}
