/**
 * La barre de volets d'un écran, tirée de la déclaration de navigation.
 *
 * Les sous-sections vivaient dans la barre latérale, sous leur section. Trois
 * niveaux empilés verticalement, c'était un niveau de trop : la colonne
 * s'allongeait à mesure qu'on ouvrait des écrans, et il fallait la parcourir
 * des yeux pour retrouver l'onglet d'un écran déjà ouvert. Ils sont maintenant
 * là où on les cherche — en tête de l'écran auquel ils appartiennent.
 *
 * La liste et les compteurs restent déclarés une seule fois, dans
 * `lib/navigation` : cette barre les lit, elle ne les redit pas.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { subsOf, type Alerts } from '../lib/navigation'
import type { Overview } from '../lib/types'
import { ViewTabs } from './ui'

export function SubSectionTabs<T extends string>({
  section,
  overview,
  value,
  onChange,
}: {
  /** Route segment of the screen, e.g. `gestion`. */
  section: string
  overview: Overview
  value: T
  onChange: (id: T) => void
}) {
  // Même clé que la barre latérale : la requête est déjà en cache, donc les
  // compteurs d'alerte ne coûtent rien de plus ici.
  const alerts = useQuery({
    queryKey: ['alerts', overview.campaign.id],
    queryFn: () => api.alerts(overview.campaign.id),
    staleTime: 45_000,
  })
  const counts: Alerts = alerts.data ?? { controls: 0, consolidation: 0 }

  return (
    <ViewTabs<T>
      value={value}
      onChange={onChange}
      tabs={subsOf(section).map((sub) => ({
        id: sub.id as T,
        label: sub.label,
        count: sub.count?.(overview, counts) ?? null,
      }))}
    />
  )
}
