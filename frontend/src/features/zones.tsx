/**
 * Zone administration, shared by Préparation and GENERIQUE.
 *
 * The same three actions are needed in both places for different reasons:
 * preparation builds the list of what will be counted, the counting day adds
 * the area nobody had listed. Keeping one implementation is what stops the two
 * screens from drifting into two different sets of defaults.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Manager, Zone } from '../lib/types'
import { SECTION_LABELS } from '../lib/format'
import { MAX_BLANK_ROWS, parseZonePaste } from '../lib/pasteZones'
import { DataGrid, type Column } from '../components/DataGrid'
import { PasteArea } from '../components/PasteArea'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  ConfirmDelete,
  EmptyState,
  Field,
  Icons,
  Modal,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

/** L'ordre des sections sur la feuille imprimée, donc celui du formulaire. */
const SECTIONS = ['LINE_SIDE', 'WIP', 'WIP_OK'] as const

/**
 * Les trois champs « nombre de lignes vierges », partagés par tous les écrans
 * qui les proposent.
 *
 * L'état est textuel et non numérique : un champ qu'on vide passe par la chaîne
 * vide, et la convertir tout de suite en zéro empêcherait d'effacer un nombre
 * pour en taper un autre. La conversion se fait à l'envoi, dans `blankRowsOf`.
 */
function BlankRowsFields({
  value,
  onChange,
}: {
  value: Record<string, string>
  onChange: (next: Record<string, string>) => void
}) {
  return (
    <div className="row-wrap" style={{ gap: 'var(--space-3)' }}>
      {SECTIONS.map((section) => (
        <Field key={section} label={SECTION_LABELS[section] ?? section}>
          <input
            className="input"
            type="number"
            min={0}
            max={MAX_BLANK_ROWS}
            style={{ width: 110 }}
            placeholder="0"
            value={value[section] ?? ''}
            onChange={(event) =>
              onChange({ ...value, [section]: event.target.value })
            }
          />
        </Field>
      ))}
    </div>
  )
}

/**
 * Ce que les trois champs valent une fois envoyés.
 *
 * Zéro et vide donnent la même chose — l'absence. « Cette section ne s'imprime
 * pas » et « cette section n'a rien de déclaré » sont le même état côté
 * serveur, et en garder deux écritures ferait diverger deux lectures.
 */
export function blankRowsOf(form: Record<string, string>): Record<string, number> {
  const rows: Record<string, number> = {}
  for (const section of SECTIONS) {
    const raw = (form[section] ?? '').trim()
    if (!raw) continue
    const number = Number(raw)
    if (Number.isFinite(number) && number > 0) rows[section] = Math.trunc(number)
  }
  return rows
}

/** Les trois champs pré-remplis depuis une zone existante. */
function blankRowsForm(zone: Zone): Record<string, string> {
  const form: Record<string, string> = {}
  for (const section of SECTIONS) {
    const rows = zone.blank_rows?.[section]
    if (rows) form[section] = String(rows)
  }
  return form
}

export function CreateZoneModal({
  campaignId,
  managers = [],
  onClose,
}: {
  campaignId: string
  managers?: Manager[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [form, setForm] = useState({
    code: '',
    label: '',
    sector: '',
    passes: 2 as 1 | 2,
    managerCode: '',
  })
  const [blankRows, setBlankRows] = useState<Record<string, string>>({})

  const mutation = useMutation({
    mutationFn: () =>
      api.createZone(campaignId, {
        code: form.code,
        label: form.label,
        sector: form.sector,
        passes: form.passes,
        managerCode: form.managerCode,
        blankRows: blankRowsOf(blankRows),
        // No article list comes with this call, so the sheet is a free-entry
        // one until somebody loads one. Saying so keeps the preparation
        // controls from reporting it as an oversight.
        freeEntry: true,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success(
        'Zone créée',
        form.passes === 2
          ? 'Ses deux feuilles de comptage sont prêtes, en saisie libre.'
          : 'Sa feuille de comptage est prête, en saisie libre.',
      )
      onClose()
    },
    onError: (error) => showError(error, 'Création impossible'),
  })

  return (
    <Modal
      title="Nouvelle zone GENERIQUE"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={!form.code.trim() || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            Créer
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Code de la zone" hint="Ex. FI ASSY M3.1, PICKING TRANSALLIANCE…">
          <input
            className="input"
            value={form.code}
            onChange={(event) => setForm({ ...form, code: event.target.value })}
          />
        </Field>
        <Field label="Libellé">
          <input
            className="input"
            value={form.label}
            onChange={(event) => setForm({ ...form, label: event.target.value })}
          />
        </Field>
        <Field label="Secteur" hint="Sert au dispatch des feuilles imprimées.">
          <input
            className="input"
            value={form.sector}
            onChange={(event) => setForm({ ...form, sector: event.target.value })}
          />
        </Field>
        <Field
          label="Nombre de comptages"
          hint="Le double comptage est la règle ; le comptage unique s’assume zone par zone."
        >
          <select
            className="input"
            value={form.passes}
            onChange={(event) =>
              setForm({ ...form, passes: Number(event.target.value) as 1 | 2 })
            }
          >
            <option value={2}>2 — deux équipes indépendantes, puis arbitrage</option>
            <option value={1}>1 — un seul comptage, sans arbitrage possible</option>
          </select>
        </Field>
        {managers.length > 0 && (
          <Field label="Gestionnaire" hint="Sert au filtre « Mon périmètre ».">
            <select
              className="input"
              value={form.managerCode}
              onChange={(event) =>
                setForm({ ...form, managerCode: event.target.value })
              }
            >
              <option value="">— aucun —</option>
              {managers.map((manager) => (
                <option key={manager.code} value={manager.code}>
                  {manager.label || manager.code}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field
          label="Lignes vierges à imprimer"
          hint={`Par section, de 0 à ${MAX_BLANK_ROWS}. Une section à 0 n’est pas imprimée du tout.`}
        >
          <BlankRowsFields value={blankRows} onChange={setBlankRows} />
        </Field>
        <Alert tone="info" title="Feuille de saisie libre">
          Cette zone est créée sans liste d’articles pré-imprimée : le compteur écrit
          ce qu’il trouve. Chargez une liste depuis l’onglet « Feuilles de comptage »
          si elle doit être pré-remplie.
        </Alert>
      </div>
    </Modal>
  )
}

/**
 * Créer d'un coup toutes les zones d'un bloc collé.
 *
 * Une campagne réelle compte quarante à soixante zones, et la liste existe
 * déjà : dans un tableur, dans le compte rendu de la campagne précédente, sur
 * le plan de l'atelier. Les recréer une par une, c'est quarante allers-retours
 * pour recopier ce qu'on a sous les yeux.
 *
 * Ce que l'écran montre avant d'écrire : combien de zones seront créées, quelles
 * lignes n'ont pas donné de code, et ce qui dépasse les bornes. Le serveur, lui,
 * crée tout ou rien — un lot à moitié créé laisserait un état que personne n'a
 * voulu.
 */
export function BulkZonesModal({
  campaignId,
  onClose,
}: {
  campaignId: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [text, setText] = useState('')
  const parsed = parseZonePaste(text)

  const create = useMutation({
    mutationFn: () => api.createZones(campaignId, parsed.zones),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.created} zone(s) créée(s)`,
        'Leurs feuilles de comptage sont prêtes, en saisie libre.',
      )
      onClose()
    },
    onError: (error) => showError(error, 'Création impossible'),
  })

  return (
    <Modal
      title="Créer un lot de zones"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={parsed.zones.length === 0 || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending
              ? 'Création…'
              : `Créer ${parsed.zones.length} zone(s)`}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field
          label="Un bloc collé depuis Excel"
          hint="Une zone par ligne. Seul le code est obligatoire ; les en-têtes sont reconnus."
        >
          <PasteArea
            value={text}
            autoFocus
            rows={10}
            aria-label="Coller les zones depuis Excel"
            onChange={setText}
            placeholder={
              'Code\tLibellé\tLignes BDL\tLignes WIP\tLignes WOP OK\n' +
              'FI ASSY M3.1\tAssemblage moteur 3.1\t40\t10\t5\n' +
              'PICKING TRANSALLIANCE\tPicking prestataire\t60'
            }
          />
        </Field>

        {text.trim() !== '' && (
          <Alert
            tone={parsed.zones.length === 0 ? 'warning' : 'info'}
            title={
              parsed.zones.length === 0
                ? 'Aucune zone lue dans ce bloc'
                : `${parsed.zones.length} zone(s) prête(s) à créer`
            }
          >
            {parsed.headerSkipped && (
              <div>La première ligne a été reconnue comme en-tête.</div>
            )}
            {parsed.rejected.length > 0 && (
              <div>
                Sans code, donc ignorée(s) : ligne(s){' '}
                {parsed.rejected.join(', ')}.
              </div>
            )}
            {parsed.refusals.map((refusal) => (
              <div key={refusal}>{refusal}</div>
            ))}
          </Alert>
        )}

        {parsed.zones.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Code</th>
                <th>Libellé</th>
                {SECTIONS.map((section) => (
                  <th key={section} className="num">
                    {SECTION_LABELS[section] ?? section}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {parsed.zones.map((zone, index) => (
                <tr key={`${zone.code}-${index}`}>
                  <td>{zone.code}</td>
                  <td>{zone.label || <span className="subtle">—</span>}</td>
                  {SECTIONS.map((section) => (
                    <td key={section} className="num">
                      {zone.blankRows[section] ?? (
                        <span className="subtle">—</span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <Alert tone="info" title="Ce que le lot crée">
          Chaque zone est créée en double comptage et en saisie libre, comme une
          zone créée à l’unité. Une section laissée vide ne s’imprime pas.
        </Alert>
      </div>
    </Modal>
  )
}

/**
 * Combien de lignes vierges chaque section de cette zone imprime.
 *
 * Le réglage appartient à la zone et non à l'impression : c'est une propriété
 * de ce qu'on va compter là-bas, pas une décision qu'on reprend à chaque sortie
 * d'imprimante.
 */
function BlankRowsModal({
  campaignId,
  zone,
  onClose,
}: {
  campaignId: string
  zone: Zone
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [form, setForm] = useState<Record<string, string>>(() =>
    blankRowsForm(zone),
  )

  const save = useMutation({
    mutationFn: () => api.setZoneBlankRows(campaignId, zone.id, blankRowsOf(form)),
    onSuccess: (saved) => {
      void queryClient.invalidateQueries()
      const printed = SECTIONS.filter((section) => saved.blank_rows?.[section])
      toast.success(
        `Lignes vierges de ${saved.code}`,
        printed.length === 0
          ? 'Aucune section déclarée : la feuille vierge revient au nombre demandé à l’impression, en bord de ligne.'
          : `Sections imprimées : ${printed
              .map((section) => SECTION_LABELS[section] ?? section)
              .join(', ')}.`,
      )
      onClose()
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  return (
    <Modal
      title={`Lignes vierges — ${zone.code}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Enregistrement…' : 'Enregistrer'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field
          label="Nombre de lignes par section"
          hint={`De 0 à ${MAX_BLANK_ROWS}. Une section à 0 n’est pas imprimée du tout.`}
        >
          <BlankRowsFields value={form} onChange={setForm} />
        </Field>
        <Alert tone="info" title="Ce que ce réglage change">
          Il ne concerne que l’impression <strong>vierge</strong> — la feuille sans
          références, sur laquelle le compteur écrit ce qu’il trouve. Les deux
          passages de la zone impriment le même document, donc le même nombre de
          lignes. Sans aucune section déclarée, le nombre demandé au moment
          d’imprimer va tout entier au bord de ligne, comme avant ce réglage.
        </Alert>
      </div>
    </Modal>
  )
}

/**
 * The zones grid with its bulk actions.
 *
 * Selection-wide rather than row-by-row because that is how the work arrives:
 * "all of metrology counts once", "these twelve zones are Marie's".
 */
export function ZonesAdminGrid({
  campaignId,
  editable,
  deletable = false,
  managers = [],
  onPrint,
  onOpen,
}: {
  campaignId: string
  editable: boolean
  /**
   * Si la suppression est offerte — c'est-à-dire en préparation.
   *
   * Plus étroit que `editable`, qui reste vrai au comptage : une zone y porte
   * des quantités relevées sur le terrain, et le serveur refuse de la
   * supprimer. Le bouton suit la règle plutôt que de la faire découvrir par un
   * refus.
   */
  deletable?: boolean
  managers?: Manager[]
  /** Print the selected zones. Absent on screens where printing has no place. */
  onPrint?: (zones: Zone[]) => void
  /** Open one zone's article list for editing. */
  onOpen?: (zone: Zone) => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)
  const [pasting, setPasting] = useState(false)
  const [blankRows, setBlankRows] = useState<Zone | null>(null)

  // Same key shape as the GENERIQUE screen's unfiltered query, so the two
  // share a cache entry and one invalidation refreshes both. Administration is
  // deliberately never focus-filtered: you assign zones you do not yet own.
  const query = useQuery({
    queryKey: ['zones', campaignId, false],
    queryFn: () => api.zones(campaignId),
  })

  const setPasses = useMutation({
    mutationFn: (passes: 1 | 2) =>
      api.setZonePasses(campaignId, [...selected], passes),
    onSuccess: (result, passes) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.updated} zone(s) à ${passes} comptage(s)`,
        passes === 1
          ? `${result.sheetsRemoved} feuille(s) n°2 supprimée(s).`
          : `${result.sheetsCreated} feuille(s) n°2 recréée(s), avec la même liste d’articles.`,
      )
    },
    onError: (error) => showError(error, 'Changement impossible'),
  })

  const setNegative = useMutation({
    mutationFn: (allowed: boolean) =>
      api.setZoneNegative(campaignId, [...selected], allowed),
    onSuccess: (result, allowed) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.updated} zone(s) mise(s) à jour`,
        allowed
          ? 'Les quantités négatives y sont désormais acceptées.'
          : 'Une quantité négative y sera de nouveau refusée à la saisie.',
      )
    },
    onError: (error) => showError(error, 'Changement impossible'),
  })

  // Un seul chemin pour la ligne et pour la sélection : deux mutations, ce
  // serait deux endroits où la confirmation peut diverger de la règle.
  const remove = useMutation({
    mutationFn: (zoneIds: string[]) => api.deleteZones(campaignId, zoneIds),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.zones} zone(s) supprimée(s)`,
        result.sheets
          ? `${result.sheets} feuille(s) de comptage retirée(s) avec elles.`
          : 'Elles n’avaient aucune feuille.',
      )
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  // Ce que la suppression emporte, énoncé avant qu'on la déclenche. Une zone
  // supprimée part avec ses feuilles et les lignes qu'on a mis une matinée à
  // corriger : `window.confirm` posait cette question-là comme il posait
  // n'importe quelle autre.
  const [removing, setRemoving] = useState<Zone[] | null>(null)
  const removalConsequences = (zones: Zone[]) => {
    const sheets = zones.reduce((n, z) => n + z.sheets.length, 0)
    const lines = zones.reduce((n, z) => n + (z.sheets[0]?.lineCount ?? 0), 0)
    const consequences = [`${sheets} feuille(s) de comptage`]
    if (lines) {
      consequences.push(`${lines} ligne(s) pré-imprimée(s)`)
    }
    const counted = zones.filter((z) => (z.sheets[0]?.countedLines ?? 0) > 0)
    if (counted.length) {
      consequences.push(
        `des quantités déjà saisies sur ${counted.length} zone(s) : ` +
          counted.map((z) => z.code).join(', '),
      )
    }
    return consequences
  }

  const assign = useMutation({
    mutationFn: (managerCode: string) =>
      api.assignZones(campaignId, [...selected], managerCode),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(`${result.updated} zone(s) affectée(s)`)
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  const [renaming, setRenaming] = useState<Zone | null>(null)
  const byCode = new Map(managers.map((m) => [m.code, m]))
  const columns: Column<Zone>[] = [
    { key: 'code', label: 'Zone', width: 200 },
    { key: 'label', label: 'Libellé', width: 220 },
    { key: 'sector', label: 'Secteur', width: 150 },
    {
      key: 'passes',
      label: 'Comptages',
      numeric: true,
      width: 120,
      render: (row) => (
        <Badge tone={row.passes === 1 ? 'warning' : 'neutral'}>
          {row.passes === 1 ? '1 — unique' : '2 — double'}
        </Badge>
      ),
      value: (row) => row.passes,
    },
    {
      key: 'lines',
      label: 'Lignes pré-imprimées',
      numeric: true,
      width: 180,
      render: (row) => {
        const lines = row.sheets[0]?.lineCount ?? 0
        if (row.free_entry && lines === 0) {
          return <Badge tone="info">saisie libre</Badge>
        }
        if (lines === 0) {
          return (
            <Badge tone="warning" title="Ni liste d’articles, ni saisie libre déclarée">
              à préparer
            </Badge>
          )
        }
        return <span className="num">{lines}</span>
      },
      value: (row) => row.sheets[0]?.lineCount ?? 0,
    },
    {
      key: 'allow_negative',
      label: 'Négatifs',
      width: 120,
      render: (row) =>
        row.allow_negative ? (
          <Badge
            tone="warning"
            title="Une quantité négative est acceptée sur cette zone — feuille de correction."
          >
            autorisés
          </Badge>
        ) : (
          <span className="subtle">refusés</span>
        ),
      value: (row) => (row.allow_negative ? 1 : 0),
    },
    {
      // Les trois sections, et non la seule qui s'imprimait. Que la colonne
      // existe est la moitié de la correction : tant que le réglage n'était
      // visible nulle part, « seule la section bord de ligne s'affiche » ne
      // pouvait se constater qu'à la sortie de l'imprimante.
      key: 'blank_rows',
      label: 'Lignes vierges',
      width: 190,
      render: (row) => {
        const declared = SECTIONS.filter((section) => row.blank_rows?.[section])
        const body =
          declared.length === 0 ? (
            <span className="subtle" title="Le nombre demandé à l’impression, en bord de ligne">
              au choix à l’impression
            </span>
          ) : (
            <span>
              {declared
                .map(
                  (section) =>
                    `${SECTION_LABELS[section] ?? section} ${row.blank_rows[section]}`,
                )
                .join(' · ')}
            </span>
          )
        if (!editable) return body
        return (
          <Button
            variant="ghost"
            size="sm"
            title={`Nombre de lignes vierges par section pour ${row.code}`}
            onClick={() => setBlankRows(row)}
          >
            {body}
          </Button>
        )
      },
      value: (row) =>
        SECTIONS.reduce((n, section) => n + (row.blank_rows?.[section] ?? 0), 0),
    } satisfies Column<Zone>,
    ...(editable
      ? [
          {
            key: 'rename',
            label: '',
            width: 52,
            sortable: false,
            filter: false as const,
            render: (row: Zone) => (
              <Button
                variant="ghost"
                size="sm"
                icon={<Icons.pencil size={13} />}
                aria-label={`Renommer ${row.code}`}
                title={`Renommer ${row.code}`}
                onClick={() => setRenaming(row)}
              />
            ),
          } satisfies Column<Zone>,
        ]
      : []),
    ...(onOpen
      ? [
          {
            key: 'open',
            label: '',
            width: 90,
            sortable: false,
            render: (row: Zone) => (
              <Button
                size="sm"
                onClick={() => onOpen(row)}
                title="Voir la feuille telle qu’elle sera imprimée, et la modifier"
              >
                Ouvrir
              </Button>
            ),
          } satisfies Column<Zone>,
        ]
      : []),
    ...(deletable
      ? [
          {
            key: 'remove',
            label: '',
            width: 52,
            sortable: false,
            filter: false as const,
            render: (row: Zone) => (
              <Button
                variant="ghost"
                size="sm"
                icon={<Icons.trash size={13} />}
                disabled={remove.isPending}
                title={`Supprimer la zone ${row.code} et ses feuilles`}
                onClick={() => setRemoving([row])}
              />
            ),
          } satisfies Column<Zone>,
        ]
      : []),
    ...(managers.length > 0
      ? [
          {
            key: 'manager_code',
            label: 'Gestionnaire',
            width: 180,
            render: (row: Zone) =>
              row.manager_code ? (
                <span>{byCode.get(row.manager_code)?.label || row.manager_code}</span>
              ) : (
                <span className="subtle">—</span>
              ),
            value: (row: Zone) => row.manager_code,
          } satisfies Column<Zone>,
        ]
      : []),
  ]

  return (
    <Card
      title="Zones et feuilles de comptage"
      message="Une zone par aire physique. Le nombre de comptages et le gestionnaire se changent sur une sélection."
      actions={
        editable && (
          <div className="row" style={{ gap: 'var(--space-2)' }}>
            <Button
              variant="primary"
              size="sm"
              icon={<Icons.plus size={13} />}
              onClick={() => setCreating(true)}
            >
              Créer une zone
            </Button>
            <Button
              size="sm"
              icon={<Icons.plus size={13} />}
              title="Coller une liste de zones : autant de zones que de lignes"
              onClick={() => setPasting(true)}
            >
              Créer un lot de zones
            </Button>
          </div>
        )
      }
      flush
    >
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={240} />}
        isEmpty={(zones) => zones.length === 0}
        empty={
          <EmptyState
            title="Aucune zone"
            action={
              editable && (
                <div className="row" style={{ gap: 'var(--space-2)' }}>
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    Créer la première zone
                  </Button>
                  <Button onClick={() => setPasting(true)}>
                    Créer un lot de zones
                  </Button>
                </div>
              )
            }
          >
            Chargez la grille « Feuilles de comptage » ci-dessus pour créer les zones
            et leur liste d’articles d’un coup, ou créez une feuille de saisie libre.
          </EmptyState>
        }
      >
        {(zones) => (
          <DataGrid
              columns={columns}
              rows={zones}
              exportTitle="Zones"
              campaignId={campaignId}
              getRowId={(row) => row.id}
              selectable={editable || Boolean(onPrint)}
              selected={selected}
              onSelectedChange={setSelected}
              searchPlaceholder="Filtrer par zone, libellé, secteur…"
              maxHeight={520}
              toolbar={
                selected.size > 0 ? (
                  <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
                    {onPrint && (
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<Icons.printer size={13} />}
                        onClick={() =>
                          onPrint(zones.filter((z) => selected.has(z.id)))
                        }
                      >
                        Imprimer la sélection
                      </Button>
                    )}
                    {deletable && (
                      <Button
                        size="sm"
                        variant="danger"
                        icon={<Icons.trash size={13} />}
                        disabled={remove.isPending}
                        onClick={() =>
                          setRemoving(zones.filter((z) => selected.has(z.id)))
                        }
                      >
                        Supprimer ({selected.size})
                      </Button>
                    )}
                    {editable && (
                    <>
                    <Button
                      size="sm"
                      disabled={setPasses.isPending}
                      onClick={() => setPasses.mutate(1)}
                    >
                      Un seul comptage
                    </Button>
                    <Button
                      size="sm"
                      disabled={setPasses.isPending}
                      onClick={() => setPasses.mutate(2)}
                    >
                      Double comptage
                    </Button>
                    <Button
                      size="sm"
                      disabled={setNegative.isPending}
                      title="Pour une feuille de correction : un retour à retrancher d’un comptage déjà posté."
                      onClick={() => setNegative.mutate(true)}
                    >
                      Autoriser les négatifs
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={setNegative.isPending}
                      onClick={() => setNegative.mutate(false)}
                    >
                      Refuser les négatifs
                    </Button>
                    {managers.length > 0 && (
                      <select
                        className="input"
                        style={{ width: 210 }}
                        value=""
                        disabled={assign.isPending}
                        onChange={(event) => {
                          if (event.target.value !== '') {
                            assign.mutate(
                              event.target.value === '__none__'
                                ? ''
                                : event.target.value,
                            )
                          }
                        }}
                      >
                        <option value="">Affecter à…</option>
                        <option value="__none__">— retirer l’affectation —</option>
                        {managers.map((manager) => (
                          <option key={manager.code} value={manager.code}>
                            {manager.label || manager.code}
                          </option>
                        ))}
                      </select>
                    )}
                    </>
                    )}
                  </div>
                ) : null
              }
            footer={
              <span>
                {zones.length} zone(s) ·{' '}
                {zones.filter((z) => z.passes === 1).length} à comptage unique ·{' '}
                {zones.filter((z) => z.free_entry).length} en saisie libre
              </span>
            }
          />
        )}
      </AsyncBoundary>

      {creating && (
        <CreateZoneModal
          campaignId={campaignId}
          managers={managers}
          onClose={() => setCreating(false)}
        />
      )}
      {pasting && (
        <BulkZonesModal campaignId={campaignId} onClose={() => setPasting(false)} />
      )}
      {blankRows && (
        <BlankRowsModal
          campaignId={campaignId}
          zone={blankRows}
          onClose={() => setBlankRows(null)}
        />
      )}
      {renaming && (
        <RenameZoneModal
          campaignId={campaignId}
          zone={renaming}
          onClose={() => setRenaming(null)}
        />
      )}
      {removing && (
        <ConfirmDelete
          what={
            removing.length === 1
              ? `la zone ${removing[0]!.code}`
              : `${removing.length} zones`
          }
          consequences={removalConsequences(removing)}
          pending={remove.isPending}
          onClose={() => setRemoving(null)}
          onConfirm={() => {
            remove.mutate(removing.map((z) => z.id))
            setRemoving(null)
          }}
        />
      )}
    </Card>
  )
}


/**
 * Renommer une zone.
 *
 * Le code d'une zone se décide avant d'avoir vu le terrain, et il se révèle
 * faux une fois sur place — deux aires sous un seul code, un code recopié d'une
 * campagne où l'atelier s'appelait autrement. Le seul recours était de
 * supprimer la zone et de la recréer, ce qui emporte ses feuilles avec leur
 * liste d'articles et leurs quantités.
 *
 * Rien d'autre ne bouge : feuilles, lignes, comptages et arbitrages tiennent à
 * l'identifiant de la zone, jamais à son code. La seule conséquence est dite
 * ici plutôt que découverte plus tard — l'import des feuilles reconnaît une
 * zone à son code, et un fichier qui porte encore l'ancien en créera une
 * seconde.
 */
function RenameZoneModal({
  campaignId,
  zone,
  onClose,
}: {
  campaignId: string
  zone: Zone
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [form, setForm] = useState({
    code: zone.code,
    label: zone.label ?? '',
    sector: zone.sector ?? '',
  })

  const rename = useMutation({
    mutationFn: () => api.renameZone(campaignId, zone.id, form),
    onSuccess: (renamed) => {
      void queryClient.invalidateQueries()
      toast.success(
        `Zone renommée en ${renamed.code}`,
        'Ses feuilles, ses lignes et ses comptages sont inchangés.',
      )
      onClose()
    },
    onError: (error) => showError(error, 'Renommage impossible'),
  })

  const changed =
    form.code !== zone.code ||
    form.label !== (zone.label ?? '') ||
    form.sector !== (zone.sector ?? '')

  return (
    <Modal
      title={`Renommer ${zone.code}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={!changed || !form.code.trim() || rename.isPending}
            onClick={() => rename.mutate()}
          >
            {rename.isPending ? 'Renommage…' : 'Renommer'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Code de la zone" hint="C’est le nom imprimé en tête de la feuille.">
          <input
            value={form.code}
            autoFocus
            onChange={(event) => setForm({ ...form, code: event.target.value })}
          />
        </Field>
        <Field label="Libellé">
          <input
            value={form.label}
            onChange={(event) => setForm({ ...form, label: event.target.value })}
          />
        </Field>
        <Field label="Secteur">
          <input
            value={form.sector}
            onChange={(event) => setForm({ ...form, sector: event.target.value })}
          />
        </Field>
        <Alert tone="info" title="Ce que le renommage ne touche pas">
          Les feuilles, leurs lignes, les comptages déjà saisis et les arbitrages
          restent attachés à cette zone. En revanche, l’import des feuilles
          reconnaît une zone à son <strong>code</strong> : recharger ensuite un
          fichier qui porte encore « {zone.code} » créera une seconde zone.
        </Alert>
      </div>
    </Modal>
  )
}
