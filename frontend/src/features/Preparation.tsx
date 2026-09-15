
import type { ComponentType } from 'react'
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
import { JournalScopeTab, ManagersTab, SettingsTab, ZoneScopeTab } from './preparation.gestion'
import { PortfoliosTab } from './preparation.portfolios'
import { ProductsTab } from './preparation.products'

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

type GestionTab =
  | 'managers'
  | 'zone_scope'
  | 'journal_scope'
  | 'portfolios'
  | 'products'
  | 'settings'

const GESTION_TABS: GestionTab[] = [
  'managers', 'zone_scope', 'journal_scope', 'portfolios', 'products', 'settings',
]

/**
 * Les onglets, rangés par ce dont ils ont besoin pour se rendre.
 *
 * Deux tables plutôt que dix branches `{tab === … && <…>}` : chacune répétait
 * la même forme à un nom près, et l'aiguillage grossissait d'une demi-douzaine
 * de lignes à chaque onglet ajouté — ce que son plafond de taille est là pour
 * empêcher. Un onglet s'ajoute maintenant en écrivant son nom une fois.
 *
 * La coupure entre les deux est réelle et pas arbitraire : les grilles qui se
 * chargent par le panneau d'import ont besoin du contrat que le serveur décrit,
 * et attendent qu'il soit arrivé ; les formulaires n'en ont pas.
 */
type TabProps = { campaignId: string; overview: Overview }
type GridProps = TabProps & { contract: GridContract }

const WITH_CONTRACT: Partial<Record<string, ComponentType<GridProps>>> = {
  items: ItemsTab, boms: BomsTab, book_stock: BookStockTab,
  count_sheets: CountSheetsTab, portfolios: PortfoliosTab, products: ProductsTab,
}

const WITHOUT_CONTRACT: Partial<Record<string, ComponentType<TabProps>>> = {
  managers: ManagersTab, zone_scope: ZoneScopeTab,
  journal_scope: JournalScopeTab, settings: SettingsTab,
}

export function Preparation({ view }: { view: PreparationView }) {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [gestion, setGestion] = useSubSection<GestionTab>('managers', GESTION_TABS)
  const tab = view

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  // Un seul nom d'onglet : la vue, ou sa sous-vue quand la vue est Gestion.
  const key = tab === 'gestion' ? gestion : tab
  const Plain = WITHOUT_CONTRACT[key]
  const Grid = WITH_CONTRACT[key]
  const grid = contracts.data?.find((c) => c.key === key)

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

      {Plain && <Plain campaignId={campaignId} overview={overview} />}
      {Grid && grid && (
        <Grid campaignId={campaignId} contract={grid} overview={overview} />
      )}
    </div>
  )
}
