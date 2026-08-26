/** Les contrôles du dossier et l'état des lieux avant clôture. */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ClosureChecklistView } from '../components/ClosureChecklist'
import { api } from '../lib/api'
import type { Overview } from '../lib/types'
import { Markdown } from '../lib/markdown'
import { DataGrid } from '../components/DataGrid'
import { FindingGroups } from '../components/Findings'
import { Alert, AsyncBoundary, Badge, Button, Card, EmptyState, Icons, Skeleton } from '../components/ui'

// --------------------------------------------------------------------------- //
// Controls & summary
// --------------------------------------------------------------------------- //

export function ControlsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const query = useQuery({
    queryKey: ['controls', campaignId],
    queryFn: () => api.controls(campaignId),
  })

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <ClosurePanel campaignId={campaignId} overview={overview} />
      <AsyncBoundary query={query} skeleton={<Skeleton height={280} />}>
        {(data) => (
        <div className="stack">
          <div className="grid grid--kpi">
            {(['BLOCKER', 'WARNING', 'INFO'] as const).map((severity) => (
              <div key={severity} className="kpi">
                <div className="kpi__label">
                  {severity === 'BLOCKER'
                    ? 'Bloquants'
                    : severity === 'WARNING'
                      ? 'Avertissements'
                      : 'Informations'}
                </div>
                <div
                  className={`kpi__value num ${severity === 'BLOCKER' && data.summary.bySeverity[severity] ? 'neg' : ''}`}
                >
                  {data.summary.bySeverity[severity] ?? 0}
                </div>
              </div>
            ))}
          </div>

          <Card
            title="Constats par contrôle"
            message="Un contrôle, une ligne. Le détail article par article s’ouvre à la demande."
          >
            <FindingGroups
              groups={data.groups}
              findings={data.findings}
              emptyLabel="Aucun constat : rien ne s’oppose à la clôture"
            />
          </Card>

          <Card
            title="Tous les constats"
            message="La même chose à plat, pour chercher une référence précise."
            flush
          >
            <DataGrid
              columns={[
                {
                  key: 'severity',
                  label: 'Sévérité',
                  width: 130,
                  render: (row) => (
                    <Badge
                      tone={
                        row.severity === 'BLOCKER'
                          ? 'danger'
                          : row.severity === 'WARNING'
                            ? 'warning'
                            : 'info'
                      }
                    >
                      {String(row.severity)}
                    </Badge>
                  ),
                  value: (row) => String(row.severity),
                },
                { key: 'code', label: 'Code', width: 220 },
                { key: 'item_number', label: 'Article', width: 160 },
                { key: 'message', label: 'Constat', width: 520 },
              ]}
              rows={data.findings as unknown as Array<Record<string, unknown>>}
              exportTitle="Constats de contrôle"
              campaignId={campaignId}
              getRowId={(_, index) => String(index)}
              searchPlaceholder="Filtrer les constats…"
              maxHeight={600}
            />
          </Card>
        </div>
        )}
      </AsyncBoundary>
    </div>
  )
}

/**
 * La liste de contrôle de clôture, sur l'écran des contrôles.
 *
 * En phase d'analyse seulement : plus tôt, les journaux et les zones y
 * figureraient comme bloquants alors que la phase ne les a pas encore exigés,
 * et une liste rouge sur une campagne parfaitement normale apprend à ignorer
 * la liste.
 */
function ClosurePanel({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const inAnalysis = overview.campaign.status === 'ANALYSIS'
  const query = useQuery({
    queryKey: ['closure-checklist', campaignId],
    queryFn: () => api.closureChecklist(campaignId),
    enabled: inAnalysis,
  })
  if (!inAnalysis) return null
  return (
    <Card
      title="Avant de clôturer"
      message="Clôturer est irréversible. Ce qui suit se prépare pendant l’analyse, pas au moment de cliquer."
    >
      <ClosureChecklistView
        campaignId={campaignId}
        data={query.data}
        pending={query.isPending}
      />
    </Card>
  )
}

export function SummaryTab({ campaignId }: { campaignId: string }) {
  const [enabled, setEnabled] = useState(false)
  const query = useQuery({
    queryKey: ['ai-summary', campaignId],
    queryFn: () => api.aiSummary(campaignId),
    enabled,
    staleTime: 15 * 60_000,
  })

  return (
    <Card
      title="Synthèse de campagne"
      message="Rédigée à partir des chiffres calculés — jamais inventés. À relire et à valider avant diffusion."
      actions={
        <Button
          variant="primary"
          icon={<Icons.sparkles size={14} />}
          disabled={query.isFetching}
          onClick={() => {
            setEnabled(true)
            void query.refetch()
          }}
        >
          {query.isFetching ? 'Génération…' : 'Générer la synthèse'}
        </Button>
      }
    >
      {!enabled ? (
        <EmptyState title="Aucune synthèse générée" icon={<Icons.sparkles size={20} />}>
          La synthèse reprend les indicateurs, les principaux contributeurs, les
          contrôles et propose des actions priorisées avec leur enjeu en euros.
        </EmptyState>
      ) : (
        <AsyncBoundary query={query} skeleton={<Skeleton count={10} height={16} />}>
          {(data) => (
            <div className="stack">
              <Alert tone="warning" title="Contenu généré par IA">
                Rédaction automatique à partir des données de la campagne. Vérifiez
                chaque chiffre avant diffusion.
              </Alert>
              {/* Le serveur demande au modèle une note structurée en sections
                  markdown ; l'afficher en texte brut mettait « ## Message clé »
                  sous les yeux du comité de direction. */}
              <div className="md">
                <Markdown text={data.markdown} />
              </div>
            </div>
          )}
        </AsyncBoundary>
      )}
    </Card>
  )
}
