/**
 * Choisir la campagne dont on reprend une grille.
 *
 * Le besoin est banal et revenait à chaque campagne : le référentiel articles
 * d'un trimestre est celui du suivant à quelques lignes près, un stock ERP de
 * contrôle se rejoue, et les journaux de comptage avancés d'une campagne
 * annulée n'ont aucune raison d'être ressaisis. La duplication de campagne
 * couvre le cas où l'on repart de zéro ; elle ne couvre pas celui, bien plus
 * fréquent, où la campagne existe déjà et où il ne manque qu'une grille.
 *
 * **Ce que porte chaque campagne est écrit en gros.** C'est l'information qui
 * fait choisir : sans elle, l'écran offre une liste de codes et de dates, on
 * désigne au jugé, on découvre que la campagne ne portait rien sur cette
 * grille, on recommence. Une campagne à zéro se voit ici sans être ouverte, et
 * ne se choisit pas.
 *
 * Rien n'est écrit d'ici : le choix déclenche l'essai à blanc, et c'est le
 * panneau d'import qui montre ensuite ce qui passera — mêmes refus, mêmes
 * avertissements, même grille modifiable qu'un fichier.
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { CampaignSource } from '../lib/types'
import { CAMPAIGN_STATUS_LABELS, date as fmtDate, label as toLabel } from '../lib/format'
import {
  AsyncBoundary,
  Badge,
  Button,
  EmptyState,
  Icons,
  Modal,
  SearchInput,
  Skeleton,
} from './ui'

export function CampaignSourcePicker({
  campaignId,
  target,
  title,
  onPick,
  onClose,
}: {
  campaignId: string
  target: string
  /** Le nom de la grille, tel que le panneau d'import l'affiche. */
  title: string
  onPick: (source: CampaignSource) => void
  onClose: () => void
}) {
  const [needle, setNeedle] = useState('')

  const query = useQuery({
    queryKey: ['campaign-sources', campaignId, target],
    queryFn: () => api.campaignSources(campaignId, target),
  })

  return (
    <Modal
      title={`Reprendre « ${title} » d’une autre campagne`}
      onClose={onClose}
      width={860}
    >
      <AsyncBoundary query={query} skeleton={<Skeleton height={280} />}>
        {(sources) => (
          <Sources
            sources={sources}
            needle={needle}
            onNeedle={setNeedle}
            onPick={onPick}
          />
        )}
      </AsyncBoundary>
    </Modal>
  )
}

function Sources({
  sources,
  needle,
  onNeedle,
  onPick,
}: {
  sources: CampaignSource[]
  needle: string
  onNeedle: (value: string) => void
  onPick: (source: CampaignSource) => void
}) {
  // Celles qui portent quelque chose d'abord, et parmi elles la plus récente :
  // c'est presque toujours celle qu'on veut, et la faire chercher serait
  // demander un geste dont la réponse est connue.
  const rows = useMemo(() => {
    const text = needle.trim().toLowerCase()
    return [...sources]
      .filter(
        (source) =>
          !text ||
          source.code.toLowerCase().includes(text) ||
          (source.label ?? '').toLowerCase().includes(text),
      )
      .sort((a, b) => {
        if ((a.rows > 0) !== (b.rows > 0)) return a.rows > 0 ? -1 : 1
        return b.countDate.localeCompare(a.countDate)
      })
  }, [sources, needle])

  const usable = rows.filter((row) => row.rows > 0).length

  if (sources.length === 0) {
    return (
      <EmptyState title="Aucune autre campagne" icon={<Icons.copy size={20} />}>
        Il n’y a rien à reprendre tant qu’une seule campagne existe.
      </EmptyState>
    )
  }

  return (
    <div className="stack">
      <SearchInput
        value={needle}
        onChange={onNeedle}
        placeholder="Code ou libellé…"
      />

      {usable === 0 && (
        <EmptyState title="Aucune campagne ne porte cette grille">
          Les campagnes existantes n’ont rien à reprendre ici.
        </EmptyState>
      )}

      <div className="stack" style={{ gap: 'var(--space-2)' }}>
        {rows.map((source) => {
          const empty = source.rows === 0
          return (
            <div
              key={source.id}
              className="row"
              style={{
                gap: 'var(--space-3)',
                padding: 'var(--space-3)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)',
                opacity: empty ? 0.55 : 1,
              }}
            >
              <div className="stack" style={{ gap: 2, minWidth: 0, flex: 1 }}>
                <span className="row" style={{ gap: 'var(--space-2)' }}>
                  <strong className="mono">{source.code}</strong>
                  <Badge tone={source.status} dot>
                    {toLabel(CAMPAIGN_STATUS_LABELS, source.status)}
                  </Badge>
                </span>
                <span className="subtle truncate">
                  {source.label || '—'} · comptage du {fmtDate(source.countDate)}
                  {source.createdBy ? ` · ${source.createdBy}` : ''}
                </span>
              </div>

              {/* Le chiffre qui décide, à la même place sur chaque ligne. */}
              <div className="stack" style={{ gap: 0, textAlign: 'right' }}>
                <strong className="num">
                  {source.rows.toLocaleString('fr-FR')}
                </strong>
                <span className="subtle">ligne(s)</span>
              </div>

              <Button
                size="sm"
                variant={empty ? 'ghost' : 'primary'}
                disabled={empty}
                title={
                  empty
                    ? 'Cette campagne ne porte aucune ligne sur cette grille'
                    : `Reprendre les ${source.rows} lignes de ${source.code}`
                }
                onClick={() => onPick(source)}
              >
                Reprendre
              </Button>
            </div>
          )
        })}
      </div>

      <span className="subtle">
        Rien n’est écrit tout de suite : vous verrez d’abord ce qui passe et ce
        qui est refusé, exactement comme pour un fichier.
      </span>
    </div>
  )
}
