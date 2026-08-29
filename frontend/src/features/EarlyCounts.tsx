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
  LabelResolution,
  ErpJournal,
  LabelAlert,
  Overview,
  ScopeCandidate,
} from '../lib/types'
import { DASH, date as formatDate, qty, relativeTime, signedMoney, signedNum } from '../lib/format'
import { useSubSection } from '../lib/subsection'
import { DataGrid, type Column } from '../components/DataGrid'
import { ImportPanel } from '../components/ImportPanel'
import { SubSectionTabs } from '../components/SubSectionTabs'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Kpi,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

type View = 'journaux' | 'derives' | 'etiquettes' | 'rescanner'

const VIEWS: Array<{ id: View; label: string }> = [
  { id: 'journaux', label: 'Journaux ERP' },
  { id: 'derives', label: 'Dérives' },
  { id: 'etiquettes', label: 'Étiquettes' },
  { id: 'rescanner', label: 'À rescanner' },
]

/** Les trois issues d'une étiquette scellée recomptée ailleurs. */
const LABEL_ACTIONS: Array<{
  id: LabelResolution
  label: string
  hint: string
}> = [
  {
    id: 'KEEP_NEW',
    label: 'La mettre au nouvel emplacement',
    hint:
      'La pièce est bien là où elle a reparu. L’étiquette sort de ' +
      'l’emplacement scellé, qui perd la quantité correspondante.',
  },
  {
    id: 'KEEP_SEALED',
    label: 'L’enlever du nouvel emplacement',
    hint:
      'La pièce n’a pas bougé. C’est la ligne de l’autre journal qui est ' +
      'l’erreur, et c’est elle qui sort du comptage.',
  },
  {
    id: 'RECOUNT',
    label: 'Signaler : à rescanner',
    hint:
      'On ne tranche pas sur pièce. Rien n’est retiré, et l’emplacement ' +
      'scellé rejoint la liste de ceux à desceller et rescanner.',
  },
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

/** Le périmètre en toutes lettres — pour le filtre, l'export et l'infobulle. */
function scopeText(journal: ErpJournal): string {
  return journal.scope.map((s) => `${s.warehouseId} / ${s.locationId}`).join(', ')
}

/**
 * Le périmètre tel qu'une cellule peut le porter.
 *
 * Un journal réel en couvre cinquante-sept. Écrits bout à bout, ils poussaient
 * la ligne sur six hauteurs, chassaient les autres journaux hors de l'écran, et
 * n'apprenaient rien : personne ne lit cinquante-sept codes d'emplacement dans
 * une cellule. Le nombre, lui, se lit — c'est la grandeur du lot qu'on ouvrira.
 *
 * La liste entière ne disparaît pas pour autant : elle reste la valeur de la
 * colonne, donc filtrable et exportée, et l'infobulle la rend au survol.
 */
function scopeSummary(journal: ErpJournal): string {
  const all = journal.scope.map((s) => `${s.warehouseId} / ${s.locationId}`)
  if (all.length <= 3) return all.join(', ')
  return `${all.length} emplacements : ${all.slice(0, 2).join(', ')}, +${all.length - 2}`
}

export default function EarlyCounts() {
  // `CampaignShell` passe l'aperçu tel quel, comme à tous les autres écrans.
  // Y lire `{ campaign, overview }` compilait — `useOutletContext<T>()` est une
  // assertion, pas une vérification — et donnait `overview === undefined` :
  // l'écran se cassait au premier accès, sur une campagne où tout allait bien.
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
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
      {view === 'journaux' && (
        <Journals campaignId={campaignId} canImport={overview.permissions.earlyCounts} />
      )}
      {view === 'derives' && <Drifts campaignId={campaignId} />}
      {view === 'etiquettes' && (
        <Labels campaignId={campaignId} canWrite={overview.permissions.earlyCounts} />
      )}
      {view === 'rescanner' && (
        <ToRescan campaignId={campaignId} canWrite={overview.permissions.earlyCounts} />
      )}
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
  // Le nom du champ vient du modèle, pas d'un alias : la campagne est le seul
  // objet de l'aperçu qui voyage tel quel, en `snake_case`. Le lire en
  // `journalsImportedAt` — au travers d'un cast, qui éteignait justement le
  // contrôle qui l'aurait dit — donnait `undefined` pour toujours, donc la
  // bannière « aucun import » même l'heure d'après un import réussi.
  const at = overview.campaign.journals_imported_at
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

function Journals({
  campaignId,
  canImport,
}: {
  campaignId: string
  canImport: boolean
}) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['erp-journals', campaignId],
    queryFn: () => api.erpJournals(campaignId),
  })
  // L'import vit ici autant que sur l'écran des journaux de comptage, et pour
  // une raison de séquence : celui-là n'ouvre qu'une fois le stock ERP chargé,
  // c'est-à-dire le jour J. Le lot avancé s'importe des jours avant. Sans ce
  // panneau, l'état vide disait « chargez l'export » depuis le seul écran d'où
  // c'était impossible.
  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract = contracts.data?.find((c) => c.key === 'count_journal_lines')
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
      key: 'countedOn',
      label: 'Compté le',
      width: 120,
      // Lue dans les lignes du journal, jamais retapée : l'ERP la donne, et
      // c'est elle qui date la référence des emplacements scellés.
      render: (row) => (row.countedOn ? formatDate(row.countedOn) : DASH),
    },
    {
      key: 'isSealed',
      label: 'Scellé',
      width: 100,
      render: (row) =>
        row.isSealed ? <Badge tone="success">Scellé</Badge> : DASH,
    },
    {
      key: 'scopeDeclared',
      label: 'Périmètre',
      width: 260,
      // Le filtre et l'export gardent la liste entière : c'est là qu'on
      // cherche « l'emplacement X est-il dans un périmètre ? », et une
      // abréviation dans un fichier Excel serait une perte sèche.
      value: (row) => scopeText(row),
      render: (row) => (
        <span>
          {row.scopeDeclared ? (
            <span title={scopeText(row)}>{scopeSummary(row)}</span>
          ) : (
            <Badge tone="warning">À déclarer</Badge>
          )}{' '}
          <Button size="sm" variant="ghost" onClick={() => setOpen(row.id)}>
            {row.scopeDeclared ? 'Modifier' : 'Déclarer et sceller'}
          </Button>
        </span>
      ),
    },
  ]

  return (
    <div className="stack">
      {contract && (
        <ImportPanel
          campaignId={campaignId}
          contract={contract}
          target="count_journal_lines"
          disabled={!canImport}
          disabledReason="Les comptages avancés sont gelés hors de la phase de comptage."
          onImported={() => {
            client.invalidateQueries({ queryKey: ['erp-journals', campaignId] })
            client.invalidateQueries({ queryKey: ['overview', campaignId] })
          }}
          extraActions={
            <Badge tone="info">
              Chaque import remplace les journaux qu’il rapporte
            </Badge>
          }
        />
      )}
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={220} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Aucun journal ERP importé">
            Chargez l’export des lignes de journaux de comptage avec le panneau ci-dessus.
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
    </div>
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
      toast.success(
        `Périmètre déclaré et scellé : ${result.locations} emplacement(s).`,
      )
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
      message="Déclarer scelle : ces emplacements seront comptés par ce journal et ne bougeront plus. Le plus probable en tête ; le tampon et les emplacements déjà pris par un autre journal ne sont pas proposés."
      actions={
        <>
          <Button variant="ghost" onClick={onDone}>
            Annuler
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={chosen.size === 0 || save.isPending}
          >
            Déclarer et sceller
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

function Labels({
  campaignId,
  canWrite,
}: {
  campaignId: string
  canWrite: boolean
}) {
  const client = useQueryClient()
  const toast = useToast()
  const onError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [comment, setComment] = useState('')

  const query = useQuery({
    queryKey: ['label-alerts', campaignId],
    queryFn: () => api.labelAlerts(campaignId),
  })

  const decide = useMutation({
    mutationFn: async ({
      decision,
      alerts,
    }: {
      decision: LabelResolution
      alerts: LabelAlert[]
    }) => {
      for (const alert of alerts) {
        await api.decideLabel(campaignId, {
          labelId: alert.labelId,
          itemNumber: alert.itemNumber,
          decision,
          sealedWarehouseId: alert.sealedWarehouseId,
          sealedLocationId: alert.sealedLocationId,
          otherWarehouseId: alert.otherWarehouseId,
          otherLocationId: alert.otherLocationId,
          comment,
        })
      }
      return alerts.length
    },
    onSuccess: (count) => {
      toast.success(`${count} étiquette(s) tranchée(s).`)
      setSelected(new Set())
      client.invalidateQueries({ queryKey: ['label-alerts', campaignId] })
      client.invalidateQueries({ queryKey: ['to-rescan', campaignId] })
      client.invalidateQueries({ queryKey: ['drifts', campaignId] })
    },
    onError: (error: unknown) => onError(error),
  })

  const rowId = (row: LabelAlert) =>
    `${row.labelId}-${row.itemNumber}-${row.otherJournalNumber}`

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
    {
      key: 'decision',
      label: 'Issue',
      width: 230,
      value: (row) => row.decision ?? '',
      render: (row) =>
        row.decision ? (
          <Badge tone={row.decision === 'RECOUNT' ? 'warning' : 'success'}>
            {LABEL_ACTIONS.find((a) => a.id === row.decision)?.label ?? row.decision}
          </Badge>
        ) : (
          <Badge tone="danger">À trancher</Badge>
        ),
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
      {(alerts) => {
        const chosen = alerts.filter((row) => selected.has(rowId(row)))
        return (
          <Card
            title="Étiquettes scellées comptées ailleurs"
            message="Ce que la dérive ne voit pas. Une pièce sortie d’un emplacement scellé sans transaction ERP laisse une dérive nulle ; si elle est re-scannée ailleurs, son étiquette apparaît dans un second journal."
            actions={
              chosen.length > 0 && canWrite ? (
                <>
                  {LABEL_ACTIONS.map((action) => (
                    <Button
                      key={action.id}
                      size="sm"
                      variant={action.id === 'RECOUNT' ? 'ghost' : 'primary'}
                      title={action.hint}
                      disabled={decide.isPending}
                      onClick={() =>
                        decide.mutate({ decision: action.id, alerts: chosen })
                      }
                    >
                      {action.label}
                    </Button>
                  ))}
                </>
              ) : null
            }
          >
            {chosen.length > 0 && (
              <Field label="Commentaire" hint="Ce qu’on a vu en allant vérifier.">
                <input
                  className="input"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
              </Field>
            )}
            <DataGrid<LabelAlert>
              rows={alerts}
              columns={columns}
              getRowId={rowId}
              selectable
              selected={selected}
              onSelectedChange={setSelected}
              exportTitle="Étiquettes signalées"
              campaignId={campaignId}
            />
          </Card>
        )
      }}
    </AsyncBoundary>
  )
}

// --------------------------------------------------------------------------
// Emplacements à desceller et rescanner
// --------------------------------------------------------------------------

/**
 * Ce que l'issue « signaler » produit.
 *
 * On n'a pas voulu trancher sur pièce, et la façon d'en sortir est d'aller
 * recompter. La liste expose l'**ancien** emplacement — le scellé — parce que
 * c'est celui-là qu'il faut desceller pour que le comptage du jour J le
 * reprenne, et l'étiquette qui a soulevé la question.
 */
function ToRescan({
  campaignId,
  canWrite,
}: {
  campaignId: string
  canWrite: boolean
}) {
  const client = useQueryClient()
  const toast = useToast()
  const onError = useErrorToast()
  const query = useQuery({
    queryKey: ['to-rescan', campaignId],
    queryFn: () => api.toRescan(campaignId),
  })

  const unseal = useMutation({
    mutationFn: ({ journalId, reason }: { journalId: string; reason: string }) =>
      api.unsealJournal(campaignId, journalId, reason),
    onSuccess: (result) => {
      toast.success(
        `Journal descellé : ${result.locations} emplacement(s) rendus au comptage général.`,
      )
      client.invalidateQueries({ queryKey: ['to-rescan', campaignId] })
      client.invalidateQueries({ queryKey: ['erp-journals', campaignId] })
      client.invalidateQueries({ queryKey: ['drifts', campaignId] })
    },
    onError: (error: unknown) => onError(error),
  })

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={200} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Rien à rescanner">
            Aucune étiquette signalée ne met un emplacement scellé en question.
        </EmptyState>
      }
    >
      {(places) => (
        <Card
          title="Emplacements à desceller et rescanner"
          message="Une étiquette de ces emplacements a été comptée ailleurs, et personne n’a voulu trancher sur pièce. Desceller rend l’emplacement au comptage du jour J, qui le recomptera."
        >
          <ul className="stack">
            {places.map((place) => (
              <li key={`${place.warehouseId}-${place.locationId}`}>
                <strong>
                  {place.warehouseId} / {place.locationId}
                </strong>{' '}
                {place.isSealed ? (
                  <Badge tone="warning">Scellé</Badge>
                ) : (
                  <Badge tone="neutral">Déjà descellé</Badge>
                )}{' '}
                — journal {place.journalNumber || DASH}, {place.labels.length}{' '}
                étiquette(s) en question
                {place.isSealed && canWrite && place.erpJournalId && (
                  <>
                    {'  '}
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        const reason = window.prompt(
                          'Desceller annule une preuve datée. Motif :',
                        )
                        if (reason?.trim()) {
                          unseal.mutate({
                            journalId: place.erpJournalId!,
                            reason,
                          })
                        }
                      }}
                    >
                      Desceller le journal
                    </Button>
                  </>
                )}
                <ul>
                  {place.labels.map((label) => (
                    <li key={`${label.labelId}-${label.itemNumber}`}>
                      <span className="mono">{label.labelId}</span> —{' '}
                      {label.itemNumber}, revue en {label.otherWarehouseId} /{' '}
                      {label.otherLocationId}
                      {label.comment ? ` — ${label.comment}` : ''}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </AsyncBoundary>
  )
}
