/**
 * Écart backflush — ce que la production explique, et ce qui reste.
 *
 * En production, la sortie de stock des composants n'est pas saisie ligne à
 * ligne : elle est déduite de la déclaration de production, selon la
 * nomenclature. L'écart backflush mesure exactement l'hypothèse que fait cette
 * déduction, et son signe dit dans quel sens le stock système a dérivé — un
 * écart backflush positif prédit un écart d'inventaire négatif du même ordre.
 *
 * L'écran est donc construit autour d'une soustraction et d'une seule :
 *
 *     écart inexpliqué = écart d'inventaire − part expliquée par le backflush
 *
 * Deux choix d'affichage en découlent.
 *
 * **Le tri se fait sur l'inexpliqué.** Un gros backflush que le comptage
 * confirme est une bonne nouvelle : il a été mesuré et il tombe juste. Ce qui
 * mérite le haut de la liste, c'est ce dont personne ne rend compte.
 *
 * **Les bornes sont dans l'en-tête, toujours.** Un chiffre de backflush sans sa
 * période ne veut rien dire, et « 42 » ne se défend pas six mois plus tard si
 * l'on ne sait plus sur quelles semaines il a été lu.
 */

import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type { BackflushRow, GridContract, Overview } from '../lib/types'
import {
  DASH,
  ITEM_TYPE_LABELS,
  date as formatDate,
  moneyShort,
  percent,
  qty,
  relativeTime,
  signClass,
  signedMoney,
  signedNum,
} from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import { ImportPanel } from '../components/ImportPanel'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Card,
  EmptyState,
  Field,
  Icons,
  Kpi,
  Skeleton,
} from '../components/ui'

/**
 * Les trois lectures d'un écart backflush, et leur pilule.
 *
 * Le seuil de 0,5 unité vient du guide : en deçà, les deux consommations
 * s'accordent autant qu'on puisse le dire, et étiqueter un arrondi
 * « surconsommation » mettrait une page de bruit devant les cas qui comptent.
 */
const TYPE_TONES: Record<string, 'warning' | 'danger' | 'neutral'> = {
  'Non-consommation': 'warning',
  Surconsommation: 'danger',
  Conforme: 'neutral',
}

export function Backflush() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const queryClient = useQueryClient()

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract: GridContract | undefined = contracts.data?.find(
    (c) => c.key === 'backflush',
  )
  const view = useQuery({
    queryKey: ['backflush', campaignId],
    queryFn: () => api.backflush(campaignId),
  })
  const suggestion = useQuery({
    queryKey: ['backflush-period', campaignId],
    queryFn: () => api.backflushPeriod(campaignId),
  })

  // Les bornes vivent dans l'écran, pas dans l'URL : elles sont un paramètre de
  // *lecture*, pas une adresse. Pré-remplies par le serveur, elles restent sur
  // celles de la lecture déjà figée quand il y en a une — sinon rafraîchir
  // rechargerait silencieusement une autre période que celle affichée.
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  useEffect(() => {
    const period = view.data?.period
    if (period) {
      setStart(period.periodStart)
      setEnd(period.periodEnd)
    } else if (suggestion.data) {
      setStart(suggestion.data.periodStart)
      setEnd(suggestion.data.periodEnd)
    }
  }, [view.data?.period, suggestion.data])

  const editable = overview.permissions.backflush
  const period = view.data?.period ?? null

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <PeriodHeader
        start={start}
        end={end}
        onStart={setStart}
        onEnd={setEnd}
        editable={editable}
        loaded={period}
      />

      {contract && (
        <ImportPanel
          campaignId={campaignId}
          contract={contract}
          target="backflush"
          params={{ borneDebut: start, borneFin: end }}
          disabled={!editable || !start || !end}
          disabledReason={
            editable
              ? 'Choisissez d’abord les deux bornes de la période.'
              : 'L’écart backflush est figé une fois la campagne clôturée.'
          }
          onImported={() => void queryClient.invalidateQueries()}
        />
      )}

      <AsyncBoundary query={view} skeleton={<Skeleton height={320} />}>
        {(data) =>
          data.rows.length === 0 ? (
            <Card>
              <EmptyState title="Aucun écart backflush chargé" icon={<Icons.layers size={20} />}>
                Lisez la période dans l’ERP, ou chargez un export.
                L’absence de donnée vaut écart nul : un composant que la
                production n’a pas touché n’a pas d’écart à expliquer.
              </EmptyState>
            </Card>
          ) : (
            <>
              <BackflushKpis kpis={data.kpis} rows={data.rows} />
              <Card title="Écart par article" flush>
                <DataGrid
                  columns={COLUMNS}
                  rows={data.rows}
                  exportTitle="Écart backflush"
                  campaignId={campaignId}
                  getRowId={(row) => row.itemNumber}
                  searchPlaceholder="Filtrer par article, désignation, programme…"
                  maxHeight={620}
                  footer={
                    <span>
                      {data.rows.length.toLocaleString('fr-FR')} article(s) — trié
                      par écart inexpliqué décroissant
                    </span>
                  }
                />
              </Card>
            </>
          )
        }
      </AsyncBoundary>
    </div>
  )
}

/**
 * Les bornes, et ce que la lecture figée en dit.
 *
 * Affichées côte à côte avec la fraîcheur de la source : la table gold est
 * reconstruite chaque nuit, donc l'écart d'une semaine passée peut bouger, et
 * savoir *quand* on l'a lu fait partie du chiffre.
 */
function PeriodHeader({
  start,
  end,
  onStart,
  onEnd,
  editable,
  loaded,
}: {
  start: string
  end: string
  onStart: (value: string) => void
  onEnd: (value: string) => void
  editable: boolean
  loaded: { weeks: number; sourceLoadedAt: string | null; refreshedAt: string | null } | null
}) {
  const monday = (value: string) =>
    value ? new Date(`${value}T00:00:00`).getDay() === 1 : true
  const weeks =
    start && end
      ? Math.round(
          (new Date(`${end}T00:00:00`).getTime() -
            new Date(`${start}T00:00:00`).getTime()) /
            (7 * 24 * 3600 * 1000),
        )
      : null

  return (
    <Card
      title="Période de lecture"
      message="Du lundi au lundi, fin exclue : l’écart se calcule sur des semaines entières."
    >
      <div className="row-wrap" style={{ gap: 'var(--space-4)', alignItems: 'flex-end' }}>
        <Field
          label="Début (inclus)"
          error={monday(start) ? undefined : 'Ce n’est pas un lundi.'}
        >
          <input
            className="input"
            type="date"
            value={start}
            disabled={!editable}
            onChange={(event) => onStart(event.target.value)}
          />
        </Field>
        <Field
          label="Fin (exclue)"
          error={monday(end) ? undefined : 'Ce n’est pas un lundi.'}
        >
          <input
            className="input"
            type="date"
            value={end}
            disabled={!editable}
            onChange={(event) => onEnd(event.target.value)}
          />
        </Field>
        {weeks !== null && weeks > 0 && (
          <Badge tone="neutral">{weeks} semaine(s)</Badge>
        )}
      </div>

      {!editable && (
        <span className="subtle">
          La campagne est clôturée : la période et l’écart sont figés. C’est ce
          qui rend le chiffre défendable — il entre dans l’écart d’inventaire, et
          ne peut donc plus bouger après la signature.
        </span>
      )}
      {loaded && (
        <div className="row-wrap" style={{ gap: 'var(--space-3)', marginTop: 'var(--space-3)' }}>
          <span className="subtle">
            Lu {relativeTime(loaded.refreshedAt)}
            {loaded.sourceLoadedAt
              ? ` — source construite le ${formatDate(loaded.sourceLoadedAt)}`
              : ''}
          </span>
        </div>
      )}
      {!loaded && (
        <Alert tone="info" title="Rien n’est encore figé">
          Les données ERP sont recalculées chaque nuit : l’écart d’une semaine
          passée peut changer. Il est donc lu une fois puis enregistré, pour que
          la même campagne donne toujours le même chiffre.
        </Alert>
      )}
    </Card>
  )
}

function BackflushKpis({
  kpis,
  rows,
}: {
  kpis: {
    backflushShareValue: number | null
    unexplainedValue: number | null
    grossUnexplainedValue: number | null
    backflushExplanationRate: number | null
    backflushLineCount: number
    backflushVarianceValue: number | null
  }
  rows: BackflushRow[]
}) {
  const compared = rows.filter((row) => row.compared).length
  const under = rows.filter((row) => row.typeEcart === 'Non-consommation').length
  const over = rows.filter((row) => row.typeEcart === 'Surconsommation').length
  const rate = kpis.backflushExplanationRate

  return (
    <div className="grid grid--kpi">
      {/* Les trois premières cartes portent sur la *même* population — les
          articles que le backflush a mesurés — pour que la soustraction se lise
          d'une carte à l'autre : écart − part = inexpliqué. Prendre l'écart de
          toute la campagne mettrait un total sur un ensemble à côté de deux
          totaux sur un autre, et l'arithmétique à l'écran ne tomberait pas. */}
      <Kpi
        label="Écart d’inventaire"
        value={signedMoney(kpis.backflushVarianceValue ?? 0)}
        tone={signClass(kpis.backflushVarianceValue ?? 0) as 'pos' | 'neg' | undefined}
        compare={`sur ${compared} article(s) mesuré(s) et comptés`}
      />
      <Kpi
        label="Part expliquée"
        value={signedMoney(kpis.backflushShareValue ?? 0)}
        compare={`${under} non-consommation · ${over} surconsommation`}
        hint="La part de l’écart d’inventaire que la production explique, dans la convention d’inventaire."
      />
      <Kpi
        label="Écart inexpliqué"
        value={signedMoney(kpis.unexplainedValue ?? 0)}
        tone={signClass(kpis.unexplainedValue ?? 0) as 'pos' | 'neg' | undefined}
        compare={`${moneyShort(kpis.grossUnexplainedValue ?? 0)} en valeur absolue`}
        hint="Ce qui reste après avoir retiré la part backflush. C’est lui, et non l’écart brut, qui doit déclencher une investigation."
        hero
      />
      <Kpi
        label="Taux d’explication"
        value={rate === null ? DASH : percent(rate)}
        tone={rate === null ? undefined : rate >= 0 ? 'pos' : 'neg'}
        compare={
          rate !== null && rate < 0
            ? 'négatif : la prise en compte creuse l’écart'
            : `sur ${kpis.backflushLineCount} article(s) mesuré(s)`
        }
        hint="1 − |inexpliqué| / |écart|. Vaut 1 quand le backflush explique exactement l’écart, 0 quand il n’apporte rien, et devient négatif quand il l’aggrave."
      />
    </div>
  )
}

const COLUMNS: Column<BackflushRow>[] = [
  {
    key: 'itemNumber',
    label: 'Article',
    width: 190,
    render: (row) => (
      <div>
        <div className="mono">{row.itemNumber}</div>
        <div className="subtle truncate" style={{ maxWidth: 180 }}>
          {row.name}
        </div>
      </div>
    ),
    value: (row) => row.itemNumber,
  },
  {
    key: 'itemType',
    label: 'Type',
    width: 120,
    render: (row) => (
      <Badge tone="neutral">{ITEM_TYPE_LABELS[row.itemType] ?? row.itemType}</Badge>
    ),
    value: (row) => row.itemType,
  },
  { key: 'program', label: 'Programme', width: 130 },
  {
    key: 'typeEcart',
    label: 'Nature',
    width: 160,
    render: (row) => (
      <Badge tone={TYPE_TONES[row.typeEcart] ?? 'neutral'}>{row.typeEcart}</Badge>
    ),
    value: (row) => row.typeEcart,
  },
  {
    key: 'netQty',
    label: 'Écart backflush',
    numeric: true,
    width: 150,
    render: (row) => (
      <span className={`num ${signClass(row.netQty)}`}>{signedNum(row.netQty)}</span>
    ),
    value: (row) => row.netQty,
  },
  {
    key: 'theoreticalQty',
    label: 'Théorique / réel',
    numeric: true,
    width: 170,
    render: (row) => (
      <div className="num">
        <div>{qty(row.theoreticalQty)}</div>
        <div className="subtle">{qty(row.actualQty)}</div>
      </div>
    ),
    value: (row) => row.theoreticalQty,
  },
  {
    key: 'backflushShareQty',
    label: 'Part backflush',
    numeric: true,
    width: 160,
    render: (row) => (
      <div className="num">
        <div className={signClass(row.backflushShareQty)}>
          {signedNum(row.backflushShareQty)}
        </div>
        <div className="subtle">{signedMoney(row.backflushShareValue)}</div>
      </div>
    ),
    value: (row) => row.backflushShareQty,
  },
  {
    key: 'varianceQty',
    label: 'Écart d’inventaire',
    numeric: true,
    width: 170,
    // `null` et non 0 tant que l'article n'a pas été compté : « non comparé »
    // et « comparé, et ça tombe juste » ne se lisent pas pareil.
    render: (row) =>
      row.compared ? (
        <div className="num">
          <div className={signClass(row.varianceQty ?? 0)}>
            {signedNum(row.varianceQty ?? 0)}
          </div>
          <div className="subtle">{signedMoney(row.varianceValue ?? 0)}</div>
        </div>
      ) : (
        <span className="subtle">non compté</span>
      ),
    value: (row) => row.varianceQty ?? 0,
  },
  {
    key: 'unexplainedQty',
    label: 'Écart inexpliqué',
    numeric: true,
    width: 170,
    render: (row) =>
      row.compared ? (
        <div className="num">
          <div className={signClass(row.unexplainedQty ?? 0)}>
            <strong>{signedNum(row.unexplainedQty ?? 0)}</strong>
          </div>
          <div className="subtle">{signedMoney(row.unexplainedValue ?? 0)}</div>
        </div>
      ) : (
        <span className="subtle">{DASH}</span>
      ),
    value: (row) => row.unexplainedQty ?? 0,
  },
  {
    key: 'explanationRate',
    label: 'Taux',
    numeric: true,
    width: 110,
    render: (row) =>
      row.explanationRate === null ? (
        <span className="subtle">{DASH}</span>
      ) : (
        <span className={`num ${row.explanationRate >= 0 ? 'pos' : 'neg'}`}>
          {percent(row.explanationRate)}
        </span>
      ),
    value: (row) => row.explanationRate ?? 0,
  },
  {
    key: 'weekCount',
    label: 'Semaines / parents',
    numeric: true,
    width: 150,
    render: (row) => (
      <span className="subtle num">
        {row.weekCount} / {row.parentCount}
      </span>
    ),
    value: (row) => row.weekCount,
  },
]
