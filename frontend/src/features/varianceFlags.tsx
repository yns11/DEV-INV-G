/**
 * Les signalements d'une ligne d'écart : les badges, et les valeurs du filtre.
 *
 * Sorti de `analysis.variances.tsx` — sans le préfixe `analysis.`, qui est
 * réservé aux onglets — parce que c'est ici que se tient la propriété qui
 * n'arrêtait pas de se perdre : **ce qui s'affiche et ce qui se filtre sont la
 * même liste**.
 *
 * Elles étaient deux écritures séparées, et rien ne les tenait ensemble : la
 * colonne affichait quatre badges quand le filtre, lui, ne lisait aucune valeur
 * et n'offrait que « (vide) » sur les quatre cent huit lignes de l'écran. La
 * question que cette colonne existe pour poser — « montre-moi les hors ERP » —
 * n'était pas posable.
 *
 * Dans un fichier à part, la colonne se monte dans un contrôle sans monter
 * l'écran entier. C'est ce qui manquait : une facette retirée de la colonne
 * réelle ne faisait tomber aucun contrôle, tant que ceux-ci définissaient leur
 * propre colonne.
 */

import { Badge } from '../components/ui'
import type { Column } from '../components/DataGrid'
import type { VarianceRow } from '../lib/types'

/** Un signalement porté par une ligne d'écart. */
export interface VarianceFlag {
  label: string
  tone: 'danger' | 'warning' | 'info' | 'success' | 'accent'
  title?: string
}

/** Ce que la ligne d'écart porte comme signalements, dans l'ordre de lecture.
 *
 * **Une seule liste pour les badges et pour le filtre.** Les deux étaient deux
 * écritures séparées de la même chose, et rien ne les tenait ensemble : la
 * colonne affichait quatre badges quand le filtre, lui, ne lisait aucune valeur
 * et n'offrait que « (vide) » sur les quatre cent huit lignes de l'écran. Les
 * faire dériver d'ici rend cette divergence impossible plutôt qu'improbable —
 * un signalement ajouté apparaît des deux côtés, ou d'aucun.
 *
 * L'ordre est celui de l'urgence : ce qui dépasse les seuils d'abord, ce qui
 * explique ensuite.
 */
export function varianceFlags(row: {
  isMaterial?: boolean
  bookOnly?: boolean
  countedOnly?: boolean
  causeCode?: string | null
  aiSuggestedCause?: string | null
  aiRationale?: string | null
}): VarianceFlag[] {
  const flags: VarianceFlag[] = []
  if (row.isMaterial) flags.push({ label: 'au-delà des seuils', tone: 'danger' })
  if (row.bookOnly) flags.push({ label: 'non compté', tone: 'warning' })
  if (row.countedOnly) flags.push({ label: 'hors ERP', tone: 'info' })
  if (row.causeCode) {
    flags.push({ label: `cause ${row.causeCode}`, tone: 'success' })
  } else if (row.aiSuggestedCause) {
    // La proposition n'apparaît que tant qu'aucune cause n'est retenue : une
    // fois la décision prise, la rappeler à côté d'elle laisse croire qu'il
    // reste quelque chose à trancher.
    flags.push({
      label: `IA : ${row.aiSuggestedCause}`,
      tone: 'accent',
      title: row.aiRationale ?? undefined,
    })
  }
  return flags
}

/**
 * La colonne « Signalements », badges et filtre compris.
 *
 * Sortie du corps du composant — les autres colonnes y restent, car elles
 * dépendent de l'état de l'écran (la granularité, l'échelle des barres, la
 * fenêtre de décomposition) — parce que celle-ci n'en dépend pas, et surtout
 * parce qu'elle porte la propriété qui n'arrêtait pas de se perdre : ce qui
 * s'affiche et ce qui se filtre sont **la même liste**. Une colonne définie au
 * milieu de six cents lignes de composant ne se vérifie pas ; celle-ci se
 * vérifie, et c'est ce qui a manqué quand `tags` n'existait pas et que le
 * filtre n'offrait que « (vide) ».
 */
export const FLAGS_COLUMN: Column<VarianceRow> = {
  key: 'flags',
  label: 'Signalements',
  width: 200,
  sortable: false,
  tags: (row) => varianceFlags(row).map((flag) => flag.label),
  render: (row) => (
    <span className="row-wrap" style={{ gap: 'var(--space-1)' }}>
      {varianceFlags(row).map((flag) => (
        <Badge key={flag.label} tone={flag.tone} title={flag.title}>
          {flag.label}
        </Badge>
      ))}
    </span>
  ),
}
