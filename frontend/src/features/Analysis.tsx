
import { useOutletContext } from 'react-router-dom'
import type { Overview } from '../lib/types'
import { SubSectionTabs } from '../components/SubSectionTabs'
import { useSubSection } from '../lib/subsection'
import { Card, EmptyState, Icons } from '../components/ui'
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

  if (!overview.campaign.book_stock_frozen_at) {
    return (
      <Card>
        <EmptyState title="Analyse indisponible" icon={<Icons.lock size={20} />}>
          Les écarts se calculent à partir du stock ERP gelé. Chargez puis gelez-le
          dans l’onglet Référentiels.
        </EmptyState>
      </Card>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
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
