/**
 * Comparaison de deux campagnes à travers les flux de la période.
 *
 * Deux inventaires encadrent une période. Entre les deux, le stock d'un article
 * n'a pas bougé au hasard : il a reçu, produit, expédié, consommé et rebuté des
 * quantités qu'on sait chiffrer. La question posée est donc fermée — en partant
 * du stock du premier inventaire et en appliquant les flux, retombe-t-on sur le
 * stock du second ?
 *
 *     attendu = stock initial + réceptions + production
 *                             − expéditions − conso. théorique − rebuts
 *
 * L'écran suit cet ordre et pas un autre : c'est une chaîne, et une chaîne se lit
 * dans le sens où elle se parcourt. D'où quatre partis pris.
 *
 * **Les étapes se présentent comme une liste de courses.** Chargé / à charger,
 * avec le nombre d'articles. Une étape oubliée fausse tout le rapport sans rien
 * casser, donc l'état d'avancement doit être lisible d'un coup d'œil.
 *
 * **Le rebut se saute explicitement.** « Pas de rebut » et « rebut non
 * renseigné » sont le même zéro et deux lectures très différentes du rapport.
 *
 * **Les articles présents d'un seul côté sont montrés à part.** Les lire comme
 * des zéros fabriquerait un écart de la taille du stock entier, et une seule
 * référence de ce genre suffit à dominer le total.
 *
 * **Le couple de stocks comparés se choisit ici, pas à l'ouverture.** Physique
 * ou ERP à chaque extrémité : quatre lectures d'une même comparaison, puisque
 * ni les quantités chargées ni l'instantané ERP gelé n'en dépendent.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type {
  GridContract,
  Overview,
  StockBasis,
  StockFlowBasis,
  StockFlowReport,
  StockFlowRow,
  StockFlowStep,
} from '../lib/types'
import {
  DASH,
  date as formatDate,
  moneyShort,
  percent,
  qty,
  relativeTime,
  signClass,
  signedMoney,
  signedNum,
} from '../lib/format'
import { Waterfall } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { ImportPanel } from '../components/ImportPanel'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  EmptyState,
  Icons,
  Kpi,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

/** Les trois chargements, dans l'ordre où la chaîne les consomme. */
const STEPS = [
  {
    kind: 'RECEIPT',
    label: 'Réceptions',
    hint: 'Ce qui est entré en stock sur la période. S’ajoute au stock initial.',
  },
  {
    kind: 'SHIPMENT',
    label: 'Expéditions',
    hint: 'Ce qui est sorti vers le client. Se retranche.',
  },
  {
    kind: 'SCRAP',
    label: 'Rebuts',
    hint: 'Étape facultative. Se retranche si elle est renseignée.',
  },
] as const

export function Reconciliation() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [runId, setRunId] = useState<string | null>(null)

  const candidates = useQuery({
    queryKey: ['stock-flow-candidates', campaignId],
    queryFn: () => api.stockFlowCandidates(campaignId),
  })
  const runs = useQuery({
    queryKey: ['stock-flow-runs', campaignId],
    queryFn: () => api.stockFlowRuns(campaignId),
  })

  // La série ouverte : celle qu'on vient de choisir, sinon la plus récente. Un
  // écran qui redemanderait la campagne initiale à chaque visite ferait
  // retaper le même choix pour retrouver le même rapport.
  const current = runId ?? runs.data?.[0]?.id ?? null

  const open = useMutation({
    mutationFn: (baselineId: string) => api.openStockFlow(campaignId, baselineId),
    onSuccess: (run) => {
      setRunId(run.id)
      void queryClient.invalidateQueries()
    },
    onError: (error) => showError(error, 'Comparaison impossible'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteStockFlow(campaignId, id),
    onSuccess: () => {
      setRunId(null)
      void queryClient.invalidateQueries()
      toast.success('Comparaison supprimée')
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  // La clôture ne ferme *pas* cet écran : la comparaison n'écrit rien qui entre
  // dans les chiffres de la campagne, et c'est une fois les deux inventaires
  // terminés qu'on la fait. Le drapeau reste lu plutôt que supposé — si la
  // matrice de gel change un jour, l'écran suivra sans mentir entre-temps, et
  // il dira pourquoi au lieu de refuser le clic en silence.
  const editable = overview.permissions.stockFlow
  const locked =
    'La phase actuelle de la campagne fige la comparaison : le rapport reste ' +
    'consultable et exportable, mais rien ne peut y être chargé.'

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <AsyncBoundary query={candidates} skeleton={<Skeleton height={140} />}>
        {(list) =>
          list.length === 0 ? (
            <Card>
              <EmptyState title="Aucune campagne antérieure" icon={<Icons.history size={20} />}>
                La comparaison part du stock d’un inventaire précédent. Elle sera
                disponible dès qu’une campagne aura été comptée avant celle-ci —
                c’est la date d’inventaire qui compte, pas la date de création.
              </EmptyState>
            </Card>
          ) : (
            <Card
              title="Campagne de départ"
              message={
                editable
                  ? 'Son stock sert de stock initial — physique ou ERP, au choix, une fois la comparaison ouverte. La période va d’un lundi d’inventaire à l’autre, fin exclue.'
                  : undefined
              }
              actions={
                current && editable ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    icon={<Icons.trash size={13} />}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(current)}
                  >
                    Supprimer
                  </Button>
                ) : undefined
              }
            >
              {/* Un contrôle désactivé doit dire pourquoi. Sans cette phrase,
                  la pilule refusait le clic avec un curseur barré et rien
                  d'autre — ce qui ne laisse aucune piste pour savoir quoi
                  faire. */}
              {!editable && (
                <Alert tone="info" title="Comparaison en lecture seule">
                  {locked}
                </Alert>
              )}
              <div className="row-wrap">
                {list.map((candidate) => {
                  const existing = runs.data?.find(
                    (run) => run.baselineCampaignId === candidate.id,
                  )
                  const active = existing?.id === current
                  return (
                    <button
                      key={candidate.id}
                      className={`chip${active ? ' chip--active' : ''}`}
                      // Une comparaison déjà ouverte se rouvre même en lecture
                      // seule : la consulter ne modifie rien.
                      disabled={(!editable && !existing) || open.isPending}
                      title={
                        !editable && !existing
                          ? locked
                          : `Comptée le ${formatDate(candidate.countDate)} — ${candidate.weeks} semaine(s) avant celle-ci`
                      }
                      onClick={() =>
                        existing ? setRunId(existing.id) : open.mutate(candidate.id)
                      }
                    >
                      {candidate.code}
                      <span className="subtle"> · {candidate.weeks} sem.</span>
                    </button>
                  )
                })}
              </div>
            </Card>
          )
        }
      </AsyncBoundary>

      {current && (
        <RunReport
          campaignId={campaignId}
          runId={current}
          editable={editable}
          overview={overview}
        />
      )}
    </div>
  )
}

function RunReport({
  campaignId,
  runId,
  editable,
  overview,
}: {
  campaignId: string
  runId: string
  editable: boolean
  overview: Overview
}) {
  // Le couple de stocks comparés. Paramètre de lecture et non d'exécution : il
  // entre dans la clé de cache, si bien que basculer d'une paire à l'autre
  // rejoue la même comparaison au lieu d'en ouvrir une seconde.
  const [basis, setBasis] = useState<{ opening: StockBasis; closing: StockBasis }>({
    opening: 'PHYSICAL',
    closing: 'PHYSICAL',
  })
  const report = useQuery({
    queryKey: ['stock-flow-report', campaignId, runId, basis.opening, basis.closing],
    queryFn: () => api.stockFlowReport(campaignId, runId, basis),
  })

  return (
    <AsyncBoundary query={report} skeleton={<Skeleton height={420} />}>
      {(data) => (
        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          <PeriodBanner report={data} basis={basis} onBasisChange={setBasis} />
          <Steps
            campaignId={campaignId}
            runId={runId}
            steps={data.steps}
            editable={editable}
            overview={overview}
          />
          {data.rows.length > 0 && (
            <>
              <FlowKpis report={data} />
              <Card
                title="Chaîne des flux"
                message={`Chaque barre repart où la précédente s’arrête. La dernière paire oppose le stock attendu à ce qui est arrivé — ${data.basis.closingStockLabel}.`}
              >
                <Waterfall
                  data={data.chain.map((step) => ({
                    label: step.label,
                    value: step.qty,
                    terminal: step.terminal,
                  }))}
                  format={qty}
                />
              </Card>
              <ComparisonGrid
                campaignId={campaignId}
                rows={data.rows}
                basis={data.basis}
              />
            </>
          )}
        </div>
      )}
    </AsyncBoundary>
  )
}

/**
 * Les quatre paires, et ce que chacune répond.
 *
 * Le mot compte : « physique » veut dire compté ajusté, comme partout ailleurs
 * dans l'application. Chaque paire pose une question différente, et c'est cette
 * question — pas le sigle — qui doit être lisible avant de cliquer.
 */
const BASIS_PAIRS: Array<{
  opening: StockBasis
  closing: StockBasis
  label: string
  hint: string
}> = [
  {
    opening: 'PHYSICAL',
    closing: 'PHYSICAL',
    label: 'Physique → Physique',
    hint: 'Ce que l’usine a réellement perdu ou gagné sur la période.',
  },
  {
    opening: 'BOOK',
    closing: 'BOOK',
    label: 'ERP → ERP',
    hint: 'Ce que le système croit avoir perdu : les flux confrontés à ses propres soldes.',
  },
  {
    opening: 'BOOK',
    closing: 'PHYSICAL',
    label: 'ERP → Physique',
    hint: 'Le terrain jugé contre le solde ERP d’origine : l’écart accumulé depuis.',
  },
  {
    opening: 'PHYSICAL',
    closing: 'BOOK',
    label: 'Physique → ERP',
    hint: 'Le solde ERP final jugé contre le terrain d’origine : ce que l’ERP n’a pas suivi.',
  },
]

function PeriodBanner({
  report,
  basis,
  onBasisChange,
}: {
  report: StockFlowReport
  basis: { opening: StockBasis; closing: StockBasis }
  onBasisChange: (basis: { opening: StockBasis; closing: StockBasis }) => void
}) {
  const { run } = report
  return (
    <Card>
      <div className="stack" style={{ gap: 'var(--space-3)' }}>
        <div className="row-wrap" style={{ gap: 'var(--space-4)' }}>
          <Badge tone="neutral">
            {run.baselineCode} · {formatDate(run.baselineCountDate ?? run.periodStart)}
          </Badge>
          <Icons.chevronRight size={14} />
          <Badge tone="accent">
            {run.campaignCode} · {formatDate(run.campaignCountDate ?? run.periodEnd)}
          </Badge>
          <span className="subtle">
            {run.weeks} semaine(s), du {formatDate(run.periodStart)} au{' '}
            {formatDate(run.periodEnd)} (exclu)
          </span>
          {run.erpRefreshedAt && (
            <span className="subtle">
              production lue {relativeTime(run.erpRefreshedAt)}
            </span>
          )}
        </div>
        <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
          <span className="subtle">Stocks comparés</span>
          {BASIS_PAIRS.map((pair) => {
            const active =
              pair.opening === basis.opening && pair.closing === basis.closing
            return (
              <button
                key={pair.label}
                className={`chip${active ? ' chip--active' : ''}`}
                title={pair.hint}
                onClick={() =>
                  onBasisChange({ opening: pair.opening, closing: pair.closing })
                }
              >
                {pair.label}
              </button>
            )
          })}
          <span className="subtle">
            {BASIS_PAIRS.find(
              (p) => p.opening === basis.opening && p.closing === basis.closing,
            )?.hint}
          </span>
        </div>
      </div>
    </Card>
  )
}

/**
 * Les quatre étapes, et où en est chacune.
 *
 * Trois se chargent, la quatrième se lit dans l'ERP. Elles sont présentées
 * ensemble parce que c'est leur *ensemble* qui rend le rapport valide : une
 * étape manquante ne casse rien, elle décale simplement le stock attendu, et
 * rien à l'écran ne le dirait si l'avancement n'était pas affiché.
 */
function Steps({
  campaignId,
  runId,
  steps,
  editable,
  overview,
}: {
  campaignId: string
  runId: string
  steps: StockFlowStep[]
  editable: boolean
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [active, setActive] = useState<string | null>(null)

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract: GridContract | undefined = contracts.data?.find(
    (c) => c.key === 'stock_flow',
  )

  const erp = useMutation({
    mutationFn: () => api.refreshStockFlowErp(campaignId, runId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      // « 0 article » recouvrait trois situations sans les distinguer : la
      // table de faits n'a rien sur la période, elle a répondu mais aucun de
      // ses articles n'est au référentiel de la campagne, ou la lecture s'est
      // bien passée. Un compteur seul ne dit pas laquelle, et sans la période
      // ni le nom de la table on ne peut même pas rejouer la requête.
      const period = `du ${formatDate(result.periodStart)} au ${formatDate(result.periodEnd)} (exclu)`
      if (result.rowsRead === 0) {
        toast.warning(
          'Aucune production sur cette période',
          `${result.source} ne renvoie aucune ligne ${period}. ` +
            (result.mirror
              ? 'La lecture passe par le miroir local : le job « Synchronisation du miroir ERP » a-t-il déjà copié la table de faits ?'
              : 'Vérifiez les dates d’inventaire des deux campagnes, et que la table de faits couvre bien cette période.'),
        )
        return
      }
      if (result.items === 0) {
        toast.warning(
          `${result.rowsRead} ligne(s) lues, aucune retenue`,
          `Aucun des articles renvoyés par ${result.source} ${period} n’est au référentiel de cette campagne. Chargez le référentiel articles, ou vérifiez que les deux sources parlent des mêmes références.`,
        )
        return
      }
      toast.success(
        `${result.items} article(s) retenus sur ${result.rowsRead} lus`,
        `Production ${qty(result.producedQty)} · consommation théorique ${qty(result.consumedQty)}.` +
          (result.outOfScope
            ? ` ${result.outOfScope} ligne(s) hors référentiel, ignorées.`
            : ''),
      )
    },
    onError: (error) => showError(error, 'Lecture ERP impossible'),
  })

  const skip = useMutation({
    mutationFn: () => api.skipStockFlowScrap(campaignId, runId),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success(
        'Étape rebuts ignorée',
        'Le rapport indiquera qu’elle a été écartée volontairement.',
      )
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const byKind = useMemo(
    () => Object.fromEntries(steps.map((step) => [step.kind, step])),
    [steps],
  )
  const erpStep = byKind.ERP

  return (
    <Card
      title="Quantités de la période"
      message="Les réceptions, expéditions et rebuts se chargent ; la production et la consommation théorique se lisent dans l’ERP."
    >
      <div className="stack">
        <div className="row-wrap">
          {STEPS.map((step) => {
            const state = byKind[step.kind]
            return (
              <button
                key={step.kind}
                className={`chip${active === step.kind ? ' chip--active' : ''}`}
                title={step.hint}
                disabled={!editable}
                onClick={() =>
                  setActive((current) => (current === step.kind ? null : step.kind))
                }
              >
                {state?.loaded ? (
                  <Icons.check size={12} />
                ) : (
                  <Icons.upload size={12} />
                )}
                {step.label}
                {state && state.items > 0 && (
                  <span className="subtle"> · {state.items}</span>
                )}
              </button>
            )
          })}

          <button
            className="chip"
            disabled={!editable || erp.isPending}
            title="Production du parent et consommation théorique, lues dans la table de faits sur la période."
            onClick={() => erp.mutate()}
          >
            {erpStep?.loaded ? <Icons.check size={12} /> : <Icons.database size={12} />}
            {erp.isPending ? 'Lecture…' : 'Production & conso. théorique'}
            {erpStep && erpStep.items > 0 && (
              <span className="subtle"> · {erpStep.items}</span>
            )}
          </button>
        </div>

        {active === 'SCRAP' && !byKind.SCRAP?.loaded && editable && (
          <Alert
            tone="info"
            title="Le rebut est facultatif"
            actions={
              <Button size="sm" disabled={skip.isPending} onClick={() => skip.mutate()}>
                Ignorer cette étape
              </Button>
            }
          >
            Si vous n’avez pas de quantité rebutée à déclarer, dites-le
            explicitement : « pas de rebut » et « rebut non renseigné » sont le
            même zéro et deux lectures différentes du rapport.
          </Alert>
        )}

        {active && contract && editable && (
          <ImportPanel
            campaignId={campaignId}
            contract={contract}
            target="stock_flow"
            transport={{
              file: (file, options) =>
                api.loadStockFlowFile(campaignId, runId, active, file, options),
              paste: (text, options) =>
                api.loadStockFlowPaste(campaignId, runId, active, text, options),
            }}
            onImported={() => void queryClient.invalidateQueries()}
          />
        )}

        {!editable && (
          <span className="subtle">
            Les quantités de la période ne sont plus modifiables ; le rapport
            reste consultable et exportable.
          </span>
        )}
        {overview.permissions.stockFlow && !erpStep?.loaded && (
          <span className="subtle">
            Sans la lecture ERP, la production et la consommation théorique
            comptent pour zéro : le stock attendu sera celui des seuls flux
            chargés.
          </span>
        )}
      </div>
    </Card>
  )
}

function FlowKpis({ report }: { report: StockFlowReport }) {
  const { kpis } = report
  return (
    <div className="grid grid--kpi">
      <Kpi
        label="Stock attendu"
        value={moneyShort(kpis.expectedValue)}
        compare={`${kpis.completeCount} article(s) comparable(s)`}
      />
      <Kpi
        label={`${report.basis.closingStockLabel} final`}
        value={moneyShort(kpis.closingValue)}
        compare={`${kpis.matchedCount} tombent juste`}
      />
      <Kpi
        label="Écart net"
        value={signedMoney(kpis.netVarianceValue)}
        tone={signClass(kpis.netVarianceValue) as 'pos' | 'neg' | undefined}
        compare={`${moneyShort(kpis.grossVarianceValue)} en valeur absolue`}
        hint="Ce qu’aucun des flux de la période n’explique."
        hero
      />
      <Kpi
        label="Fiabilité"
        value={kpis.grossReliability === null ? DASH : percent(kpis.grossReliability)}
        compare={
          kpis.incompleteCount
            ? `${kpis.incompleteCount} article(s) comptés d’un seul côté, exclus`
            : 'tous les articles sont comparables'
        }
        hint="1 − écart absolu / stock attendu. La lecture honnête : les erreurs ne se compensent pas."
      />
    </div>
  )
}

function ComparisonGrid({
  campaignId,
  rows,
  basis,
}: {
  campaignId: string
  rows: StockFlowRow[]
  basis: StockFlowBasis
}) {
  const columns = useMemo(() => columnsFor(basis), [basis])
  // Les articles comptés d'un seul côté ne sont pas un écart, ce sont des trous
  // dans la comparaison. Cachés par défaut — ils domineraient la liste — et
  // atteignables d'une pilule, parce qu'il faut quand même aller les voir.
  const [incomplete, setIncomplete] = useState(false)
  const shown = useMemo(
    () => rows.filter((row) => (incomplete ? !row.complete : row.complete)),
    [rows, incomplete],
  )
  const holes = rows.filter((row) => !row.complete).length

  return (
    <Card title="Comparaison article par article" flush>
      <DataGrid
        columns={columns}
        rows={shown}
        toolbar={
          holes > 0 ? (
            <button
              className={`chip${incomplete ? ' chip--active' : ''}`}
              title="Articles présents dans une seule des deux campagnes : la comparaison n’a pas de sens pour eux, mais leur absence en a une."
              onClick={() => setIncomplete((value) => !value)}
            >
              Présents d’un seul côté
              <span className="subtle"> · {holes}</span>
            </button>
          ) : undefined
        }
        exportTitle="Comparaison de campagnes"
        campaignId={campaignId}
        getRowId={(row) => row.itemNumber}
        searchPlaceholder="Filtrer par article, désignation…"
        maxHeight={620}
        initialSort={{ key: 'varianceValue', direction: 'asc' }}
        footer={
          <span>
            {shown.length.toLocaleString('fr-FR')} article(s)
            {incomplete
              ? ' présents dans une seule campagne'
              : holes > 0 && ` — ${holes} exclu(s) du total`}
          </span>
        }
      />
    </Card>
  )
}

/**
 * Les colonnes, avec les deux extrémités nommées d'après la paire choisie.
 *
 * Un en-tête figé sur « Stock compté » alors que la colonne montre le solde ERP
 * ferait mentir la grille — et c'est celle qu'on exporte pour la réunion.
 */
function columnsFor(basis: StockFlowBasis): Column<StockFlowRow>[] {
  return [
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
    key: 'openingQty',
    label: `Stock initial (${basis.openingLabel})`,
    numeric: true,
    width: 150,
    render: (row) =>
      row.hasOpening ? (
        <span className="num">{qty(row.openingQty)}</span>
      ) : (
        <span className="subtle">absent</span>
      ),
    value: (row) => row.openingQty,
  },
  {
    key: 'receivedQty',
    label: '+ Réceptions',
    numeric: true,
    width: 130,
    render: (row) => <span className="num">{qty(row.receivedQty)}</span>,
    value: (row) => row.receivedQty,
  },
  {
    key: 'producedQty',
    label: '+ Production',
    numeric: true,
    width: 130,
    render: (row) => <span className="num">{qty(row.producedQty)}</span>,
    value: (row) => row.producedQty,
  },
  {
    key: 'shippedQty',
    label: '− Expéditions',
    numeric: true,
    width: 130,
    render: (row) => <span className="num">{qty(row.shippedQty)}</span>,
    value: (row) => row.shippedQty,
  },
  {
    key: 'consumedQty',
    label: '− Conso. théorique',
    numeric: true,
    width: 160,
    render: (row) => <span className="num">{qty(row.consumedQty)}</span>,
    value: (row) => row.consumedQty,
  },
  {
    key: 'scrappedQty',
    label: '− Rebuts',
    numeric: true,
    width: 110,
    render: (row) => <span className="num">{qty(row.scrappedQty)}</span>,
    value: (row) => row.scrappedQty,
  },
  {
    key: 'expectedQty',
    label: 'Stock attendu',
    numeric: true,
    width: 140,
    render: (row) => <strong className="num">{qty(row.expectedQty)}</strong>,
    value: (row) => row.expectedQty,
  },
  {
    key: 'closingQty',
    label: `Stock final (${basis.closingLabel})`,
    numeric: true,
    width: 150,
    render: (row) =>
      row.hasClosing ? (
        <strong className="num">{qty(row.closingQty)}</strong>
      ) : (
        <span className="subtle">absent</span>
      ),
    value: (row) => row.closingQty,
  },
  {
    key: 'varianceValue',
    label: 'Écart',
    numeric: true,
    width: 160,
    render: (row) =>
      row.complete ? (
        <div className="num">
          <div className={signClass(row.varianceValue)}>
            <strong>{signedNum(row.varianceQty)}</strong>
          </div>
          <div className="subtle">{signedMoney(row.varianceValue)}</div>
        </div>
      ) : (
        <span className="subtle">{DASH}</span>
      ),
    value: (row) => row.varianceValue,
  },
  {
    key: 'varianceRatio',
    label: 'Écart relatif',
    numeric: true,
    width: 130,
    render: (row) =>
      row.varianceRatio === null || !row.complete ? (
        <span className="subtle">{DASH}</span>
      ) : (
        <span className={`num ${signClass(row.varianceRatio)}`}>
          {percent(row.varianceRatio)}
        </span>
      ),
    value: (row) => row.varianceRatio ?? 0,
  },
  ]
}
