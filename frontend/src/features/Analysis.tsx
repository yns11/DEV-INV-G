
import { useOutletContext } from 'react-router-dom'
import type { Overview } from '../lib/types'
import { SubSectionTabs } from '../components/SubSectionTabs'
import { useSubSection } from '../lib/subsection'
import { Alert, Card, EmptyState, Icons } from '../components/ui'
import { CausesTab } from './analysis.causes'
import { VariancesTab } from './analysis.variances'
import { ControlsTab, SummaryTab } from './analysis.controls'
import { AdjustmentsTab } from './analysis.adjustments'
import { AnalyticsTab } from './analysis.analytics'

/** One per navigation entry; `causes` still carries three related views. */
export type AnalysisView = 'controls' | 'variances' | 'causes' | 'adjustments'

type CausesTab = 'causes' | 'analytics' | 'summary'

const CAUSES_TABS: CausesTab[] = ['causes', 'analytics', 'summary']

export function Analysis({ view }: { view: AnalysisView }) {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [causesTab, setCausesTab] = useSubSection<CausesTab>('causes', CAUSES_TABS)

  const frozen = Boolean(overview.campaign.book_stock_frozen_at)
  const sealed = overview.counts.sealedLocations ?? 0
  // Un écart a besoin d'une référence figée — pas nécessairement de *toute* la
  // référence. Le gel du stock ERP est global et arrive au jour J ; le
  // scellement d'un précomptage est un gel **par emplacement**, et pour ceux-là
  // référence et comptage sont déjà posés et ne bougeront plus. Leur écart est
  // définitif dès la déclaration : attendre le gel général le cacherait pendant
  // les jours où l'on peut encore aller voir sur le terrain.
  //
  // Les contrôles, eux, passent depuis toujours : ils ne calculent aucun écart.
  if (view !== 'controls' && !frozen && sealed === 0) {
    return (
      <Card>
        <EmptyState title="Analyse indisponible" icon={<Icons.lock size={20} />}>
          Les écarts se calculent à partir d’une référence figée. Scellez un
          précomptage, ou chargez puis gelez le stock ERP dans l’onglet
          Référentiels.
        </EmptyState>
      </Card>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      {view !== 'controls' && !frozen && (
        <Alert tone="info" title="Écarts partiels — stock ERP pas encore gelé">
          Les chiffres portent sur les {sealed} emplacement(s) précomptés et
          scellés, et ils sont définitifs : leur référence ne bougera plus. Le
          reste de la campagne apparaîtra au chargement du stock ERP général.
        </Alert>
      )}
      {view === 'causes' && (
        <SubSectionTabs
          section="causes"
          overview={overview}
          value={causesTab}
          onChange={setCausesTab}
        />
      )}
      {view === 'variances' && <VariancesTab campaignId={campaignId} overview={overview} />}
      {view === 'controls' && (
        <ControlsTab campaignId={campaignId} overview={overview} />
      )}
      {view === 'adjustments' && (
        <AdjustmentsTab campaignId={campaignId} overview={overview} />
      )}
      {view === 'causes' && causesTab === 'causes' && (
        <CausesTab campaignId={campaignId} overview={overview} />
      )}
      {view === 'causes' && causesTab === 'analytics' && (
        <AnalyticsTab campaignId={campaignId} />
      )}
      {view === 'causes' && causesTab === 'summary' && (
        <SummaryTab campaignId={campaignId} />
      )}
    </div>
  )
}
