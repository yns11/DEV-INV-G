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
 * **Le scellement ensuite.** Déclarer le périmètre d'un journal scelle ses
 * emplacements, et le scellement dit *lesquels sont comptés par ce journal-là*.
 * Il ne pose aucune référence : la référence de la campagne est unique, et
 * c'est le stock ERP du jour J.
 *
 * **Et c'est tout.** L'écran portait aussi les dérives, les étiquettes et les
 * emplacements à rescanner. Rien ne se calcule plus à partir d'un comptage
 * avancé : un journal de précomptage est posté dans l'ERP avant que la photo du
 * jour J ne soit prise, et cette photo l'a donc déjà intégré. Les dérives et
 * les étiquettes ne sont plus que deux listes à regarder, sans action requise
 * ni constat bloquant — elles sont passées dans les Contrôles, avec les autres
 * constats. « À rescanner » n'existait que par l'issue « signaler », qui n'est
 * plus proposée.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type { ErpJournal, Overview, ScopeCandidate } from '../lib/types'
import { DASH, date as formatDate, qty, relativeTime } from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import { ImportPanel } from '../components/ImportPanel'
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
import { ErpJournalLinesModal } from './earlyCounts.journalLines'

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

  return (
    <div className="stack">
      <LastImport overview={overview} />
      <Journals
        campaignId={campaignId}
        canWrite={overview.permissions.earlyCounts}
        // Le gel ferme la fenêtre du précomptage, et c'en est la définition :
        // précompter veut dire *avant* la référence générale. Après, il n'y a
        // plus rien à déclarer ni à sceller — l'écran cessait pourtant de le
        // dire et le geste finissait en 500.
        frozen={overview.campaign.book_stock_frozen_at !== null}
      />
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
  canWrite,
  frozen,
}: {
  campaignId: string
  canWrite: boolean
  frozen: boolean
}) {
  const client = useQueryClient()
  const toast = useToast()
  const onError = useErrorToast()
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
  const [lines, setLines] = useState<ErpJournal | null>(null)

  // Le geste inverse de « Déclarer et sceller », au même endroit que lui. Il
  // n'existait que dans l'onglet « À rescanner », c'est-à-dire là où une
  // étiquette avait signalé l'emplacement : hors de ce cas — un périmètre coché
  // de travers, un journal chargé par erreur — l'écran ne proposait aucun
  // retour en arrière, alors que la route et le service l'assuraient déjà.
  const unseal = useMutation({
    mutationFn: ({ journalId, reason }: { journalId: string; reason: string }) =>
      api.unsealJournal(campaignId, journalId, reason),
    onSuccess: (result) => {
      toast.success(
        `Journal descellé : ${result.locations} emplacement(s) rendus au comptage général.`,
      )
      client.invalidateQueries({ queryKey: ['erp-journals', campaignId] })
      client.invalidateQueries({ queryKey: ['overview', campaignId] })
      client.invalidateQueries({ queryKey: ['drifts', campaignId] })
    },
    onError: (error: unknown) => onError(error),
  })

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
          ) : frozen ? (
            // « À déclarer » réclamerait un geste qui n'a plus de sens et que
            // le serveur refuse : ce journal est celui du jour J, il n'a rien
            // à sceller.
            <Badge tone="neutral">Comptage du jour J</Badge>
          ) : (
            <Badge tone="warning">À déclarer</Badge>
          )}{' '}
          {!frozen && (
            <Button size="sm" variant="ghost" onClick={() => setOpen(row.id)}>
              {row.scopeDeclared ? 'Modifier' : 'Déclarer et sceller'}
            </Button>
          )}
          {row.isSealed && canWrite && (
            <>
              {' '}
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  const reason = window.prompt(
                    'Desceller annule une preuve datée. Motif :',
                  )
                  if (reason?.trim()) {
                    unseal.mutate({ journalId: row.id, reason })
                  }
                }}
              >
                Desceller
              </Button>
            </>
          )}
        </span>
      ),
    },
    {
      key: 'open',
      label: '',
      width: 110,
      sortable: false,
      filter: false,
      sticky: 'right',
      // Ce que l'agrégat ne dit pas : d'où il vient. Un journal de six cents
      // lignes n'en compte parfois que quatre cents, et c'est ici qu'on lit
      // pourquoi.
      render: (row) => (
        <Button size="sm" variant="ghost" onClick={() => setLines(row)}>
          Ouvrir
        </Button>
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
          disabled={!canWrite}
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
          {frozen && (
            <Alert tone="info" title="Le stock ERP est gelé">
              La fenêtre du précomptage est fermée : précompter veut dire
              <em> avant</em> la référence générale. Les journaux importés
              maintenant sont ceux du jour J — leur référence est le stock ERP
              gelé, leur comptage entre par l’import, et il n’y a rien à
              déclarer ni à sceller. Un emplacement scellé se reprend en
              descellant son journal.
            </Alert>
          )}
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
          {lines && (
            <ErpJournalLinesModal
              campaignId={campaignId}
              journal={lines}
              onClose={() => setLines(null)}
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
