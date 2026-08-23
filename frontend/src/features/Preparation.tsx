
import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type { GridContract, Overview } from '../lib/types'
import { SubSectionTabs } from '../components/SubSectionTabs'
import { useSubSection } from '../lib/subsection'
import { Skeleton } from '../components/ui'
import { ItemsTab } from './preparation.items'
import { BomsTab } from './preparation.boms'
import { BookStockTab } from './preparation.bookStock'
import { CountSheetsTab } from './preparation.sheets'
import { JournalScopeTab, ManagersTab, ThresholdsTab, ZoneScopeTab } from './preparation.gestion'

/**
 * The screens this file serves, one per navigation entry.
 *
 * `gestion` is the one that still holds several views: managers, the two
 * perimeter assignments and the thresholds are four short forms that belong to
 * the same decision — who counts what, and from which amount an variance
 * matters — and splitting them into four sidebar entries would have made the
 * tree longer without making anything easier to find.
 */
export type PreparationView =
  | 'items'
  | 'boms'
  | 'book_stock'
  | 'count_sheets'
  | 'gestion'

type GestionTab = 'managers' | 'zone_scope' | 'journal_scope' | 'thresholds'

const GESTION_TABS: GestionTab[] = [
  'managers', 'zone_scope', 'journal_scope', 'thresholds',
]

export function Preparation({ view }: { view: PreparationView }) {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [gestion, setGestion] = useSubSection<GestionTab>('managers', GESTION_TABS)
  const tab = view

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract = (key: string): GridContract | undefined =>
    contracts.data?.find((c) => c.key === key)

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      {contracts.isPending && <Skeleton height={240} />}

      {tab === 'gestion' && (
        <SubSectionTabs
          section="gestion"
          overview={overview}
          value={gestion}
          onChange={setGestion}
        />
      )}

      {tab === 'items' && contract('items') && (
        <ItemsTab campaignId={campaignId} contract={contract('items')!} overview={overview} />
      )}
      {tab === 'boms' && contract('boms') && (
        <BomsTab campaignId={campaignId} contract={contract('boms')!} overview={overview} />
      )}
      {tab === 'book_stock' && contract('book_stock') && (
        <BookStockTab
          campaignId={campaignId}
          contract={contract('book_stock')!}
          overview={overview}
        />
      )}
      {tab === 'count_sheets' && contract('count_sheets') && (
        <CountSheetsTab
          campaignId={campaignId}
          contract={contract('count_sheets')!}
          overview={overview}
        />
      )}
      {tab === 'gestion' && gestion === 'thresholds' && (
        <ThresholdsTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'gestion' && gestion === 'managers' && (
        <ManagersTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'gestion' && gestion === 'journal_scope' && (
        <JournalScopeTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'gestion' && gestion === 'zone_scope' && (
        <ZoneScopeTab campaignId={campaignId} overview={overview} />
      )}
    </div>
  )
}
