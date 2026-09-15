
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
import { DriftsTab, LabelsTab } from './earlyCounts.observations'

/** One per navigation entry; `causes` still carries three related views. */
export type AnalysisView = 'controls' | 'variances' | 'causes' | 'adjustments'

type CausesTab = 'causes' | 'analytics' | 'summary'

const CAUSES_TABS: CausesTab[] = ['causes', 'analytics', 'summary']

/**
 * Les volets des Contrôles.
 *
 * Les constats du dossier, puis les deux listes que laisse un précomptage. Ces
 * deux-là étaient des vues du comptage avancé, où elles portaient des issues à
 * trancher ; elles n'en portent plus, et c'est pour cela qu'elles sont ici : ce
 * qui se regarde sans se décider appartient aux constats.
 */
type ControlsTabId = 'constats' | 'derives' | 'etiquettes'

const CONTROLS_TABS: ControlsTabId[] = ['constats', 'derives', 'etiquettes']

export function Analysis({ view }: { view: AnalysisView }) {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [causesTab, setCausesTab] = useSubSection<CausesTab>('causes', CAUSES_TABS)
  const [controlsTab, setControlsTab] = useSubSection<ControlsTabId>(
    'constats',
    CONTROLS_TABS,
  )

  const frozen = Boolean(overview.campaign.book_stock_frozen_at)
  // Un écart a besoin d'une référence, et il n'y en a qu'une : le stock ERP du
  // jour J, gelé. Tant qu'il ne l'est pas, il n'y a rien à afficher — pas même
  // partiellement.
  //
  // L'écran montrait auparavant les emplacements précomptés et scellés, dont il
  // tenait la référence pour figée. Elle ne l'était pas : un précomptage est
  // posté dans l'ERP *avant* la photo du jour J, donc la photo l'a déjà
  // intégré, et l'écart affiché comptait deux fois la même correction.
  //
  // Les contrôles, eux, passent depuis toujours : ils ne calculent aucun écart.
  if (view !== 'controls' && !frozen) {
    return (
      <Card>
        <EmptyState title="Analyse indisponible" icon={<Icons.lock size={20} />}>
          Les écarts se calculent contre le stock ERP du jour J. Chargez-le puis
          gelez-le dans l’onglet Stock ERP : c’est la seule référence de la
          campagne, et un précomptage n’en tient pas lieu.
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
      {view === 'controls' && (
        <SubSectionTabs
          section="controles"
          overview={overview}
          value={controlsTab}
          onChange={setControlsTab}
        />
      )}
      {view === 'variances' && <VariancesTab campaignId={campaignId} overview={overview} />}
      {view === 'controls' && controlsTab === 'constats' && (
        <ControlsTab campaignId={campaignId} overview={overview} />
      )}
      {view === 'controls' && controlsTab === 'derives' && (
        <DriftsTab campaignId={campaignId} />
      )}
      {view === 'controls' && controlsTab === 'etiquettes' && (
        <LabelsTab campaignId={campaignId} />
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
