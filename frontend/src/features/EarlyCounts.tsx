/**
 * Comptages avancés : compter certains emplacements avant le jour J.
 *
 * L'écran suit l'ordre dans lequel le travail se fait, et pas un autre.
 *
 * **Les journaux ERP d'abord, parce que rien n'est calculable sans leur
 * périmètre.** Un journal tient à un entrepôt mais couvre plusieurs
 * emplacements, et ceux de ses lignes ne suffisent pas à dire lesquels : une
 * ligne peut n'être là que pour matérialiser un déplacement. L'application
 * propose — emplacements des lignes, moins le tampon, moins ceux déjà pris, le
 * plus probable en tête — et l'utilisateur tranche.
 *
 * **Les lots ensuite.** Un lot s'ouvre sur des journaux dont le périmètre est
 * déclaré, se clôt, puis se scelle. Le scellement pose la référence de ses
 * emplacements — lue dans la colonne « Stock ERP » du journal, sans chargement
 * séparé — et refuse tant qu'un journal n'est pas posté dans l'ERP.
 *
 * **Les dérives enfin, le jour J.** `ERP@J − physique@T0`, attendue nulle. Quand
 * elle ne l'est pas, une seule question : quelle quantité fait foi ? Deux
 * réponses, conserver ou recompter, et le passage en analyse attend qu'on ait
 * répondu.
 *
 * **Les alertes d'étiquette sont à part, et le méritent.** Elles rattrapent ce
 * que la dérive ne voit pas : une pièce sortie d'un emplacement scellé sans
 * transaction ERP laisse une dérive nulle, mais si elle est re-scannée
 * ailleurs, son étiquette apparaît dans un second journal.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type {
  Drift,
  DriftResolution,
  EarlyBatch,
  ErpJournal,
  LabelAlert,
  Overview,
  ScopeCandidate,
} from '../lib/types'
import { DASH, date as formatDate, qty, relativeTime, signedMoney, signedNum } from '../lib/format'
import { useSubSection } from '../lib/subsection'
import { DataGrid, type Column } from '../components/DataGrid'
import { SubSectionTabs } from '../components/SubSectionTabs'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  EmptyState,
  Kpi,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

type View = 'journaux' | 'lots' | 'derives' | 'etiquettes'

const VIEWS: Array<{ id: View; label: string }> = [
  { id: 'journaux', label: 'Journaux ERP' },
  { id: 'lots', label: 'Lots avancés' },
  { id: 'derives', label: 'Dérives' },
  { id: 'etiquettes', label: 'Étiquettes' },
]

/** Les deux issues d'une dérive, et ce que chacune engage. */
const RESOLUTIONS: Array<{
  id: DriftResolution
  label: string
  hint: string
}> = [
  {
    id: 'KEEP_EARLY',
    label: 'Conserver le comptage avancé',
    hint:
      'Le physique relevé à T0 fait foi. La campagne et l’ERP resteront en ' +
      'désaccord de la valeur de la dérive : la cause est obligatoire.',
  },
  {
    id: 'RECOUNT',
    label: 'Recompter le jour J',
    hint:
      'L’emplacement est descellé et rejoint le comptage général ; sa ' +
      'référence redevient le stock ERP du jour J.',
  },
]

/**
 * La clé d'un emplacement dans une sélection.
 *
 * Encodée en JSON plutôt que concaténée : un identifiant d'emplacement peut
 * contenir n'importe quel caractère, et choisir un séparateur revient à parier
 * qu'il n'y figurera jamais. Le pari se perd en silence, sur une ligne qui se
 * coche à la place d'une autre.
 */
function keyOf(warehouseId: string, locationId: string): string {
  return JSON.stringify([warehouseId, locationId])
}

function parseKey(key: string): { warehouseId: string; locationId: string } {
  const [warehouseId, locationId] = JSON.parse(key) as [string, string]
  return { warehouseId, locationId }
}

export default function EarlyCounts() {
  const { campaign, overview } = useOutletContext<{
    campaign: { id: string }
    overview: Overview
  }>()
  const [view, setView] = useSubSection<View>('journaux', VIEWS.map((v) => v.id))

  return (
    <div className="stack">
      <LastImport overview={overview} />
      <SubSectionTabs<View>
        section="comptages-avances"
        overview={overview}
        value={view}
        onChange={setView}
      />
      {view === 'journaux' && <Journals campaignId={campaign.id} />}
      {view === 'lots' && <Batches campaignId={campaign.id} />}
      {view === 'derives' && <Drifts campaignId={campaign.id} />}
      {view === 'etiquettes' && <Labels campaignId={campaign.id} />}
    </div>
  )
}

/**
 * De quand datent les chiffres qu'on regarde.
 *
 * Le notebook est rejoué toutes les quelques minutes le jour J. Ce n'est pas un
 * détail d'affichage : c'est ce qui dit s'il faut recharger avant de décider.
 */
function LastImport({ overview }: { overview: Overview }) {
  const at = (overview.campaign as { journalsImportedAt?: string | null })
    ?.journalsImportedAt
  if (!at) {
    return (
      <Alert tone="info" title="Aucun import de journaux">
        Exécutez le notebook de lecture des journaux ERP, puis chargez son
        export. Le journal porte sa propre référence : il n’y a pas de stock à
        charger séparément pour un comptage avancé.
      </Alert>
    )
  }
  return (
    <Card
      title="Dernier import de journaux"
      message="Chaque exécution du notebook remplace les journaux qu’elle rapporte, et laisse les autres intacts."
    >
      <Kpi label="Importé" value={relativeTime(at)} hint={formatDate(at)} />
    </Card>
  )
}

// --------------------------------------------------------------------------
// Journaux ERP et périmètres
// --------------------------------------------------------------------------

function Journals({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['erp-journals', campaignId],
    queryFn: () => api.erpJournals(campaignId),
  })
  const [open, setOpen] = useState<string | null>(null)

  const columns: Column<ErpJournal>[] = [
    { key: 'journalNumber', label: 'Journal ERP', width: 150 },
    { key: 'kind', label: 'Type', width: 90 },
    { key: 'description', label: 'Description', width: 220 },
    {
      key: 'lineCount',
      label: 'Lignes',
      width: 100,
      numeric: true,
      value: (row) => row.lineCount,
    },
    {
      key: 'erpPosted',
      label: 'Posté ERP',
      width: 110,
      render: (row) =>
        row.erpPosted ? (
          <Badge tone="success">Posté</Badge>
        ) : (
          <Badge tone="warning">Ouvert</Badge>
        ),
    },
    {
      key: 'scopeDeclared',
      label: 'Périmètre',
      width: 260,
      value: (row) =>
        row.scope.map((s) => `${s.warehouseId} / ${s.locationId}`).join(', '),
      render: (row) => (
        <span>
          {row.scopeDeclared ? (
            row.scope.map((s) => `${s.warehouseId} / ${s.locationId}`).join(', ')
          ) : (
            <Badge tone="warning">À déclarer</Badge>
          )}{' '}
          <Button size="sm" variant="ghost" onClick={() => setOpen(row.id)}>
            {row.scopeDeclared ? 'Modifier' : 'Déclarer'}
          </Button>
        </span>
      ),
    },
  ]

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={220} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Aucun journal ERP importé">
            Chargez l’export des lignes de journaux de comptage.
        </EmptyState>
      }
    >
      {(journals) => (
        <div className="stack">
          <Card
            title="Journaux ERP"
            message="Un journal tient à un entrepôt et couvre plusieurs emplacements. Ceux de ses lignes ne suffisent pas à dire lesquels : certaines ne sont là que pour matérialiser un déplacement."
          >
            <DataGrid<ErpJournal>
              rows={journals}
              columns={columns}
              getRowId={(row) => row.id}
              exportTitle="Journaux ERP"
              campaignId={campaignId}
            />
          </Card>
          {open && (
            <ScopePicker
              campaignId={campaignId}
              journal={journals.find((j) => j.id === open)!}
              onDone={() => setOpen(null)}
            />
          )}
        </div>
      )}
    </AsyncBoundary>
  )
}

function ScopePicker({
  campaignId,
  journal,
  onDone,
}: {
  campaignId: string
  journal: ErpJournal
  onDone: () => void
}) {
  const client = useQueryClient()
  const toast = useToast()
  const onError = useErrorToast()
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(journal.scope.map((s) => keyOf(s.warehouseId, s.locationId))),
  )
  const query = useQuery({
    queryKey: ['scope-proposal', campaignId, journal.id],
    queryFn: () => api.scopeProposal(campaignId, journal.id),
  })

  const save = useMutation({
    mutationFn: () =>
      api.declareScope(
        campaignId,
        journal.id,
        [...chosen].map(parseKey),
      ),
    onSuccess: (result) => {
      toast.success(`Périmètre déclaré : ${result.locations} emplacement(s).`)
      client.invalidateQueries({ queryKey: ['erp-journals', campaignId] })
      onDone()
    },
    onError: (error: unknown) => onError(error),
  })

  const toggle = (row: ScopeCandidate) => {
    const key = keyOf(row.warehouseId, row.locationId)
    setChosen((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <Card
      title={`Périmètre de ${journal.journalNumber}`}
      message="Le plus probable en tête. Le tampon et les emplacements déjà pris par un autre journal ne sont pas proposés."
      actions={
        <>
          <Button variant="ghost" onClick={onDone}>
            Annuler
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={chosen.size === 0 || save.isPending}
          >
            Déclarer
          </Button>
        </>
      }
    >
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={160} />}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="Aucun emplacement disponible">
              Toutes les lignes de ce journal visent le tampon ou des emplacements déjà alloués.
          </EmptyState>
        }
      >
        {(rows) => (
          <ul className="stack">
            {rows.map((row) => {
              const key = keyOf(row.warehouseId, row.locationId)
              return (
                <li key={key}>
                  <label>
                    <input
                      type="checkbox"
                      checked={chosen.has(key)}
                      onChange={() => toggle(row)}
                    />{' '}
                    <strong>
                      {row.warehouseId} / {row.locationId}
                    </strong>{' '}
                    — {row.lineCount} ligne(s), {row.itemCount} référence(s),
                    ERP {qty(row.qtyOnHand)}, compté {qty(row.qtyCounted)}
                  </label>
                </li>
              )
            })}
          </ul>
        )}
      </AsyncBoundary>
    </Card>
  )
}

// --------------------------------------------------------------------------
// Lots
// --------------------------------------------------------------------------

function Batches({ campaignId }: { campaignId: string }) {
  const client = useQueryClient()
  const toast = useToast()
  const onError = useErrorToast()
  const query = useQuery({
    queryKey: ['early-batches', campaignId],
    queryFn: () => api.earlyBatches(campaignId),
  })
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['early-batches', campaignId] })
    client.invalidateQueries({ queryKey: ['erp-journals', campaignId] })
  }

  const close = useMutation({
    mutationFn: (batchId: string) => api.closeEarlyBatch(campaignId, batchId),
    onSuccess: () => {
      toast.success('Lot clos.')
      refresh()
    },
    onError: (error: unknown) => onError(error),
  })
  const seal = useMutation({
    mutationFn: (batchId: string) => api.sealEarlyBatch(campaignId, batchId),
    onSuccess: () => {
      toast.success('Lot scellé : la référence de ses emplacements est posée.')
      refresh()
    },
    onError: (error: unknown) => onError(error),
  })
  const unseal = useMutation({
    mutationFn: ({ batchId, reason }: { batchId: string; reason: string }) =>
      api.unsealEarlyBatch(campaignId, batchId, reason),
    onSuccess: () => {
      toast.success('Lot descellé.')
      refresh()
    },
    onError: (error: unknown) => onError(error),
  })

  const actions = (batch: EarlyBatch) => {
    if (batch.isSealed) {
      return (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            const reason = window.prompt(
              'Desceller annule une preuve datée. Motif :',
            )
            if (reason?.trim()) unseal.mutate({ batchId: batch.id, reason })
          }}
        >
          Desceller
        </Button>
      )
    }
    if (batch.isClosed) {
      return (
        <Button size="sm" onClick={() => seal.mutate(batch.id)}>
          Sceller
        </Button>
      )
    }
    return (
      <Button size="sm" variant="ghost" onClick={() => close.mutate(batch.id)}>
        Clore
      </Button>
    )
  }

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={200} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Aucun lot avancé">
            Déclarez le périmètre d’un journal ERP, puis ouvrez un lot dessus.
        </EmptyState>
      }
    >
      {(batches) => (
        <Card
          title="Lots de comptage avancé"
          message="Ouvrir, compter, poster dans l’ERP, clore, sceller. Le scellement pose la référence des emplacements et refuse tant qu’un journal n’est pas posté."
        >
          <ul className="stack">
            {batches.map((batch) => (
              <li key={batch.id}>
                <strong>{batch.code}</strong>{' '}
                {batch.isSealed ? (
                  <Badge tone="success">Scellé</Badge>
                ) : batch.isClosed ? (
                  <Badge tone="info">Clos</Badge>
                ) : (
                  <Badge tone="warning">Ouvert</Badge>
                )}{' '}
                — {batch.locations.length} emplacement(s)
                {batch.countedOn ? `, compté le ${formatDate(batch.countedOn)}` : ''}
                {'  '}
                {actions(batch)}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </AsyncBoundary>
  )
}

// --------------------------------------------------------------------------
// Dérives
// --------------------------------------------------------------------------

function Drifts({ campaignId }: { campaignId: string }) {
  const client = useQueryClient()
  const toast = useToast()
  const onError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [cause, setCause] = useState('MOUVEMENT_APRES_SCELLEMENT')
  const [comment, setComment] = useState('')

  const query = useQuery({
    queryKey: ['drifts', campaignId],
    queryFn: () => api.drifts(campaignId),
  })

  const resolve = useMutation({
    mutationFn: (resolution: DriftResolution) =>
      api.resolveDrifts(campaignId, {
        driftIds: [...selected],
        resolution,
        causeCode: resolution === 'KEEP_EARLY' ? cause : '',
        comment,
      }),
    onSuccess: (result) => {
      toast.success(`${result.resolved} dérive(s) tranchée(s).`)
      setSelected(new Set())
      client.invalidateQueries({ queryKey: ['drifts', campaignId] })
    },
    onError: (error: unknown) => onError(error),
  })

  const columns: Column<Drift>[] = [
    { key: 'warehouseId', label: 'Entrepôt', width: 110 },
    { key: 'locationId', label: 'Emplacement', width: 150 },
    { key: 'itemNumber', label: 'Référence', width: 170 },
    {
      key: 'qtyErpT0',
      label: 'ERP avant précomptage',
      width: 170,
      numeric: true,
      render: (row) => qty(row.qtyErpT0),
    },
    {
      key: 'qtyPhysicalT0',
      label: 'Physique T0',
      width: 140,
      numeric: true,
      render: (row) => qty(row.qtyPhysicalT0),
    },
    {
      key: 'qtyErpJ',
      label: 'ERP jour J',
      width: 130,
      numeric: true,
      render: (row) => qty(row.qtyErpJ),
    },
    {
      key: 'driftQty',
      label: 'Dérive',
      width: 130,
      numeric: true,
      render: (row) => signedNum(row.driftQty),
    },
    {
      key: 'driftValue',
      label: 'Valeur',
      width: 130,
      numeric: true,
      render: (row) => signedMoney(row.driftValue),
    },
    {
      key: 'resolution',
      label: 'Issue',
      width: 200,
      render: (row) =>
        row.isResolved ? (
          <Badge tone="success">
            {row.resolution === 'KEEP_EARLY' ? 'Comptage avancé' : 'Recompté'}
          </Badge>
        ) : row.isMaterial ? (
          <Badge tone="danger">À trancher</Badge>
        ) : (
          DASH
        ),
    },
  ]

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={240} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Aucune dérive">
            Aucun emplacement scellé, ou le stock ERP général n’a pas encore été chargé.
        </EmptyState>
      }
    >
      {(drifts) => {
        const blocking = drifts.filter((d) => d.blocksAnalysis)
        return (
          <div className="stack">
            {blocking.length > 0 && (
              <Alert tone="warning" title="Le passage en analyse attend">
                {blocking.length} dérive(s) matérielle(s) n’ont pas d’issue. Le
                stock ERP du jour J ne dit pas la même chose que le physique
                posté au précomptage : dites, pour chacune, laquelle fait foi.
              </Alert>
            )}
            <Card
              title="Dérives des emplacements scellés"
              message="ERP du jour J moins physique posté à T0. Attendue nulle : l’emplacement était balisé, et poster son journal a réaligné l’ERP sur le physique compté."
              actions={
                selected.size > 0 ? (
                  <>
                    {RESOLUTIONS.map((r) => (
                      <Button
                        key={r.id}
                        size="sm"
                        variant={r.id === 'RECOUNT' ? 'ghost' : 'primary'}
                        title={r.hint}
                        disabled={resolve.isPending}
                        onClick={() => resolve.mutate(r.id)}
                      >
                        {r.label}
                      </Button>
                    ))}
                  </>
                ) : null
              }
            >
              {selected.size > 0 && (
                <div className="stack">
                  <label>
                    Cause (obligatoire pour « conserver »)
                    <input
                      value={cause}
                      onChange={(e) => setCause(e.target.value)}
                    />
                  </label>
                  <label>
                    Commentaire
                    <input
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                    />
                  </label>
                </div>
              )}
              <DataGrid<Drift>
                rows={drifts}
                columns={columns}
                getRowId={(row) => row.id}
                selectable
                selected={selected}
                onSelectedChange={setSelected}
                exportTitle="Dérives"
                campaignId={campaignId}
              />
            </Card>
          </div>
        )
      }}
    </AsyncBoundary>
  )
}

// --------------------------------------------------------------------------
// Étiquettes
// --------------------------------------------------------------------------

function Labels({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['label-alerts', campaignId],
    queryFn: () => api.labelAlerts(campaignId),
  })

  const columns: Column<LabelAlert>[] = [
    { key: 'labelId', label: 'Étiquette', width: 150 },
    { key: 'itemNumber', label: 'Référence', width: 170 },
    {
      key: 'sealedLocationId',
      label: 'Emplacement scellé',
      width: 190,
      render: (row) => `${row.sealedWarehouseId} / ${row.sealedLocationId}`,
    },
    {
      key: 'otherLocationId',
      label: 'Comptée aussi en',
      width: 190,
      render: (row) => `${row.otherWarehouseId} / ${row.otherLocationId}`,
    },
    { key: 'otherJournalNumber', label: 'Dans le journal', width: 150 },
    {
      key: 'otherQtyCounted',
      label: 'Quantité',
      width: 110,
      numeric: true,
      render: (row) => qty(row.otherQtyCounted),
    },
  ]

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={200} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Aucune étiquette signalée">
            Aucune étiquette d’un emplacement scellé ne se retrouve comptée dans un autre journal.
        </EmptyState>
      }
    >
      {(alerts) => (
        <Card
          title="Étiquettes scellées comptées ailleurs"
          message="Ce que la dérive ne voit pas. Une pièce sortie d’un emplacement scellé sans transaction ERP laisse une dérive nulle ; si elle est re-scannée ailleurs, son étiquette apparaît dans un second journal."
        >
          <DataGrid<LabelAlert>
            rows={alerts}
            columns={columns}
            getRowId={(row) => `${row.labelId}-${row.otherJournalNumber}`}
            exportTitle="Étiquettes signalées"
            campaignId={campaignId}
          />
        </Card>
      )}
    </AsyncBoundary>
  )
}
