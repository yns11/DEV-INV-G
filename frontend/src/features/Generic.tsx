
import { useOutletContext } from 'react-router-dom'
import { useSubSection } from '../lib/subsection'
import type { Overview } from '../lib/types'
import { SubSectionTabs } from '../components/SubSectionTabs'
import { ZonesTab } from './generic.zones'
import { ArbitrationTab } from './generic.arbitration'
import { ConsolidationTab } from './generic.consolidation'

type Tab = 'zones' | 'arbitration' | 'consolidation'

const TABS: Tab[] = ['zones', 'arbitration', 'consolidation']

export function Generic() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useSubSection<Tab>('zones', TABS)

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <SubSectionTabs
        section="compil"
        overview={overview}
        value={tab}
        onChange={setTab}
      />
      {tab === 'zones' && <ZonesTab campaignId={campaignId} overview={overview} />}
      {tab === 'arbitration' && <ArbitrationTab campaignId={campaignId} overview={overview} />}
      {tab === 'consolidation' && (
        <ConsolidationTab campaignId={campaignId} overview={overview} />
      )}
    </div>
  )
}
