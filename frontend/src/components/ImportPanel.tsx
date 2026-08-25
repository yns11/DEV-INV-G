/**
 * The import surface, shared by every grid.
 *
 * A row of actions, and nothing else until one is used. Where the grid has an
 * ERP source it comes **first**, because it is the better answer: nobody has
 * retyped anything, and the export/re-import round trip that produced most of
 * the referential errors disappears. The file, the paste and the template stay
 * — an ERP that is unreachable, incomplete or simply not yet updated is a
 * normal Tuesday, and the campaign cannot wait for it.

 * The expected-columns list and the paste box appear on demand — permanently
 * parked above every grid, they were a band of instructions people had read.
 *
 * What does not change is the loop that matters: a file or a paste is validated
 * in a dry run and the result is shown — accepted, rejected, why, on which line
 * — *before* anything is written, and only then does the user confirm.
 *
 * That "see it before you commit it" loop is the single biggest behavioural
 * difference from pasting into a spreadsheet, and it is what stops a broken
 * file from silently becoming the campaign's truth.
 */

import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api, downloads } from '../lib/api'
import type { GridContract, ImportResult, Overview } from '../lib/types'
import {
  Alert, Badge, Button, Card, Icons, useDownload, useErrorToast, useToast,
} from './ui'
import { DataGrid, columnsFromContract } from './DataGrid'
import { PasteArea } from './PasteArea'

/** Grids the ERP is authoritative for — mirrors `ERP_TARGETS` on the API. */
const ERP_TARGETS = ['items', 'boms', 'book_stock', 'backflush']

type Stage =
  | { kind: 'idle' }
  | { kind: 'validating' }
  | {
      kind: 'preview'
      result: ImportResult
      source: { file?: File; text?: string; erp?: boolean }
    }
  | { kind: 'importing' }
  | { kind: 'done'; result: ImportResult }

export function ImportPanel({
  campaignId,
  contract,
  target,
  disabled = false,
  disabledReason,
  replace = false,
  params,
  transport,
  onImported,
  extraActions,
}: {
  campaignId: string
  contract: GridContract
  target: string
  disabled?: boolean
  disabledReason?: string
  replace?: boolean
  /**
   * Paramètres propres à la grille, ajoutés à chaque appel.
   *
   * Les bornes de période de l'écart backflush passent par là : elles
   * qualifient *la lecture*, pas le contenu du fichier — un fichier n'a pas de
   * corps où les mettre, et un téléversement non plus.
   */
  params?: Record<string, string | number | boolean | undefined>
  /**
   * Transport de remplacement, quand la grille n'est pas servie par la route
   * d'import générique.
   *
   * Les trois chargements de la réconciliation ont leurs propres points
   * d'entrée — ils écrivent dans une série, pas dans la campagne. Le panneau
   * reste identique : c'est la boucle « voir avant d'écrire » qui compte, et
   * elle ne doit pas se dédoubler.
   */
  transport?: {
    file: (file: File, options: { dryRun?: boolean }) => Promise<ImportResult>
    paste: (text: string, options: { dryRun?: boolean }) => Promise<ImportResult>
  }
  onImported?: (result: ImportResult) => void
  extraActions?: React.ReactNode
}) {
  // Le rôle vient du contexte de la campagne : le panneau est toujours rendu
  // sous elle, et le faire descendre par les sept appelants n'ajouterait que
  // sept occasions de l'oublier.
  const { access } = useOutletContext<Overview>()
  const [stage, setStage] = useState<Stage>({ kind: 'idle' })
  const [pasteText, setPasteText] = useState('')
  const [pasting, setPasting] = useState(false)
  // Only the two referential grids have an ERP source; asking for the rest
  // would be a round trip whose answer is always the same.
  const hasErp = ERP_TARGETS.includes(target)
  const erp = useQuery({
    queryKey: ['erp-source'],
    queryFn: api.erpSource,
    enabled: hasErp,
    staleTime: Infinity,
  })
  const mirror = erp.data?.mirror?.[target] ?? null
  // Le stock est une suite de photos ; les autres grilles sont un état. Il est
  // donc la seule à demander « laquelle ».
  const datedSource = target === 'book_stock'
  const stockDates = useQuery({
    queryKey: ['erp-stock-dates'],
    queryFn: api.erpStockDates,
    enabled: datedSource && erp.data?.available === true,
    staleTime: 5 * 60 * 1000,
  })
  const dates = stockDates.data?.dates ?? []
  const [chosenDate, setChosenDate] = useState('')
  // La plus récente par défaut, sans la figer : `chosenDate` reste vide tant
  // que personne n'a choisi, si bien qu'une liste rafraîchie propose la
  // nouvelle photo au lieu de rester sur celle d'hier.
  const snapshotDate = chosenDate || dates[0] || ''
  const erpParams = {
    ...params,
    ...(datedSource && snapshotDate ? { dateSnapshot: snapshotDate } : {}),
  }
  const fileInput = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const showError = useErrorToast()
  const startDownload = useDownload()

  const validate = async (source: { file?: File; text?: string; erp?: boolean }) => {
    setStage({ kind: 'validating' })
    try {
      const result = source.erp
        ? await api.importErp(campaignId, target, { dryRun: true, replace, params: erpParams })
        : source.file
          ? transport
            ? await transport.file(source.file, { dryRun: true })
            : await api.importFile(campaignId, target, source.file, {
                dryRun: true, replace, params,
              })
          : transport
            ? await transport.paste(source.text ?? '', { dryRun: true })
            : await api.importPaste(campaignId, target, source.text ?? '', {
                dryRun: true, params,
              })
      setStage({ kind: 'preview', result, source })
    } catch (error) {
      showError(error, source.erp ? 'Lecture ERP impossible' : 'Analyse impossible')
      setStage({ kind: 'idle' })
    }
  }

  const commit = async () => {
    if (stage.kind !== 'preview') return
    const { source } = stage
    setStage({ kind: 'importing' })
    try {
      const result = source.erp
        ? await api.importErp(campaignId, target, { replace, params: erpParams })
        : source.file
          ? transport
            ? await transport.file(source.file, {})
            : await api.importFile(campaignId, target, source.file, { replace, params })
          : transport
            ? await transport.paste(source.text ?? '', {})
            : await api.importPaste(campaignId, target, source.text ?? '', {
                replace, params,
              })
      setStage({ kind: 'done', result })
      setPasteText('')
      setPasting(false)
      toast.success(
        `${result.rowsAccepted.toLocaleString('fr-FR')} ligne(s) importée(s)`,
        result.rowsRejected
          ? `${result.rowsRejected} ligne(s) rejetée(s) — voir le détail.`
          : undefined,
      )
      onImported?.(result)
    } catch (error) {
      showError(error, 'Import impossible')
      setStage({ kind: 'idle' })
    }
  }

  const reset = () => setStage({ kind: 'idle' })

  // A locked panel is dead weight: every button in it would refuse. The reason
  // alone says everything it would have, in one line.
  //
  // Qui ne peut pas écrire du tout l'emporte sur la raison passée par l'écran.
  // Celles-ci nomment toutes la phase — « gelé depuis le passage en comptage »
  // — et cette phrase devient un mensonge pour un lecteur devant une campagne
  // en préparation : rien n'est gelé, c'est lui qui n'a pas le droit. Décidé
  // ici, à l'unique endroit qui rend le verrou, plutôt qu'aux sept appels.
  if (!access.canWrite) {
    return (
      <Alert tone="info" title={`${contract.title} — lecture seule`}>
        Cette campagne ne se modifie pas depuis votre compte. Demandez à{' '}
        {access.owner || 'son créateur'} de vous déclarer comme gestionnaire.
      </Alert>
    )
  }
  if (disabled) {
    return disabledReason ? (
      <Alert tone="info" title={`${contract.title} — import verrouillé`}>
        {disabledReason}
      </Alert>
    ) : null
  }

  const busy = stage.kind === 'validating' || stage.kind === 'importing'

  return (
    <div className="stack">
      {/* A row of actions rather than a permanent panel. Loading a file is one
          click either way, and the expected-columns list only earns its space
          when somebody is actually assembling a paste. */}
      <div className="row-wrap">
        {extraActions}
        {hasErp && (
          <Button
            size="sm"
            variant="primary"
            icon={<Icons.refresh size={13} />}
            disabled={busy || !erp.data?.available}
            title={
              erp.data?.available
                ? `Lecture de ${erp.data.tables[target as 'items' | 'boms']}`
                : erp.data?.reason ?? 'Vérification de la source ERP…'
            }
            onClick={() => void validate({ erp: true })}
          >
            Lire depuis l’ERP
          </Button>
        )}
        {/* Quelle photo. Le stock ERP est publié une fois par jour, et la
            journée de comptage n'est pas toujours celle du chargement : le
            comptage a commencé samedi matin, la reprise se fait le lundi, et
            c'est la photo de samedi qui fait foi. Prendre la plus récente
            d'office rendait ce cas inatteignable, sans le dire. */}
        {datedSource && erp.data?.available && dates.length > 0 && (
          <label className="row" style={{ gap: 'var(--space-2)' }}>
            <span className="muted">Photo du</span>
            <select
              className="input input--mini"
              value={snapshotDate}
              disabled={busy}
              onChange={(event) => setChosenDate(event.target.value)}
            >
              {dates.map((date, index) => (
                <option key={date} value={date}>
                  {new Date(`${date}T00:00:00`).toLocaleDateString('fr-FR')}
                  {index === 0 ? ' (la plus récente)' : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        {/* The age of the copy, next to the button that loads it. Reading a
            month-old referential without noticing is exactly the error this
            application exists to remove — it cannot be left to a log line. */}
        {hasErp && erp.data?.available && mirror && (
          <span className={mirror.stale ? 'badge badge--warning' : 'muted'}>
            Données ERP
            {mirror.syncedAt
              ? ` · ${new Date(mirror.syncedAt).toLocaleDateString('fr-FR')}`
              : ' · jamais copiées'}
          </span>
        )}
        <Button
          size="sm"
          variant={hasErp ? 'secondary' : 'primary'}
          icon={<Icons.upload size={13} />}
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          Charger un fichier
        </Button>
        <Button
          size="sm"
          icon={<Icons.download size={13} />}
          onClick={() => startDownload(downloads.gridTemplate(campaignId, contract.key))}
        >
          Télécharger le modèle
        </Button>
        <Button
          size="sm"
          icon={<Icons.copy size={13} />}
          disabled={busy}
          onClick={() => setPasting((open) => !open)}
        >
          Copier / Coller
        </Button>
      </div>

      <input
        ref={fileInput}
        type="file"
        accept=".xlsx,.xlsm,.csv,.tsv,.txt"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void validate({ file })
          event.target.value = ''
        }}
      />

      {pasting && (
        <Card
          title={`Coller ${contract.title.toLowerCase()}`}
          message={contract.hint || contract.description}
          actions={
            <Button variant="ghost" size="sm" onClick={() => setPasting(false)}>
              Fermer
            </Button>
          }
        >
          <div className="stack">
            <div className="chips">
              {contract.fields.map((field) => (
                <span key={field.name} className="chip" title={field.aliases.join(' · ')}>
                  {field.label}
                  {field.required && <Badge tone="danger">requis</Badge>}
                </span>
              ))}
            </div>
            <PasteArea
              value={pasteText}
              autoFocus
              aria-label={`Coller ${contract.title.toLowerCase()}`}
              onChange={setPasteText}
              placeholder={
                'Collez ici (Ctrl+V) un bloc copié depuis Excel.\n' +
                'La touche Tab insère une tabulation ; Échap la rend à la navigation.'
              }
            />
            <div className="row">
              <Button
                variant="primary"
                size="sm"
                disabled={!pasteText.trim()}
                onClick={() => void validate({ text: pasteText })}
              >
                Analyser le collage
              </Button>
              {pasteText && (
                <Button variant="ghost" size="sm" onClick={() => setPasteText('')}>
                  Effacer
                </Button>
              )}
            </div>
          </div>
        </Card>
      )}

      {stage.kind === 'validating' && (
        <Alert tone="info" title="Analyse en cours…">
          Validation ligne par ligne. Rien n’est encore enregistré.
        </Alert>
      )}

      {hasErp && erp.data && !erp.data.available && (
        <Alert tone="info" title="Lecture ERP indisponible">
          {erp.data.reason} Chargez un fichier en attendant.
        </Alert>
      )}

      {stage.kind === 'preview' && (
        <ImportReport
          result={stage.result}
          contract={contract}
          onCancel={reset}
          onConfirm={() => void commit()}
        />
      )}

      {stage.kind === 'importing' && (
        <Alert tone="info" title="Import en cours…">
          Enregistrement des lignes validées.
        </Alert>
      )}

      {stage.kind === 'done' && (
        <ImportReport result={stage.result} contract={contract} onCancel={reset} done />
      )}
    </div>
  )
}

export function ImportReport({
  result,
  contract,
  onCancel,
  onConfirm,
  done = false,
}: {
  result: ImportResult
  contract: GridContract
  onCancel: () => void
  onConfirm?: () => void
  done?: boolean
}) {
  // Defaults on every collection: this payload crosses the network, and a
  // single missing array used to unmount the whole application. The server
  // contract is pinned by a test, but a client that blanks the screen when an
  // API evolves is a client that is too brittle to deploy.
  const {
    missingColumns = [],
    unknownColumns = [],
    duplicateKeys = [],
    warnings = [],
    errors = [],
    details = {},
  } = result
  const blocked = missingColumns.length > 0
  const outOfScopeLines = Number(details.outOfScopeLines ?? 0)
  const outOfScopeItems = Number(details.outOfScopeItems ?? 0)
  const unknownLines = Number(details.unknownLines ?? 0)
  const unknownItems = Number(details.unknownItems ?? 0)
  // Quelques-unes seulement : le panneau sert à décider tout de suite, pas à
  // relire douze mille références. La liste entière est dans Contrôles.
  const unknownSample = (
    Array.isArray(details.unknownItemNumbers) ? details.unknownItemNumbers : []
  ).slice(0, 6).map(String)
  const sample = (result as unknown as { sample?: Array<Record<string, unknown>> }).sample ?? []

  return (
    <Card
      title={done ? 'Import terminé' : 'Vérification avant import'}
      message={
        done
          ? `${result.rowsAccepted.toLocaleString('fr-FR')} ligne(s) enregistrée(s).`
          : 'Rien n’a encore été enregistré. Vérifiez le résultat puis confirmez.'
      }
      actions={
        <>
          <Button variant="ghost" onClick={onCancel}>
            {done ? 'Fermer' : 'Annuler'}
          </Button>
          {!done && onConfirm && (
            <Button variant="primary" disabled={blocked} onClick={onConfirm}>
              Confirmer l’import
            </Button>
          )}
        </>
      }
    >
      <div className="grid grid--kpi" style={{ marginBottom: 'var(--space-4)' }}>
        <div className="kpi">
          <div className="kpi__label">Lignes reçues</div>
          <div className="kpi__value num">{result.rowsReceived.toLocaleString('fr-FR')}</div>
        </div>
        <div className="kpi">
          <div className="kpi__label">Acceptées</div>
          <div className="kpi__value num pos">
            {result.rowsAccepted.toLocaleString('fr-FR')}
          </div>
        </div>
        <div className="kpi">
          <div className="kpi__label">Rejetées</div>
          <div className={`kpi__value num ${result.rowsRejected ? 'neg' : ''}`}>
            {result.rowsRejected.toLocaleString('fr-FR')}
          </div>
        </div>
      </div>

      <div className="stack">
        {blocked && (
          <Alert tone="danger" title="Colonnes obligatoires absentes">
            Le fichier ne contient pas : <strong>{missingColumns.join(', ')}</strong>.
            Téléchargez le modèle pour obtenir la structure attendue.
          </Alert>
        )}

        {result.duplicateOf && (
          <Alert tone="warning" title="Ce fichier a déjà été importé">
            Import identique du {new Date(result.duplicateOf.importedAt).toLocaleString('fr-FR')} par{' '}
            {result.duplicateOf.importedBy} ({result.duplicateOf.rowsAccepted} lignes).
            Réimporter est possible mais créera des doublons si la clé naturelle diffère.
          </Alert>
        )}

        {/* Le stock ERP couvre toute l'usine ; la campagne choisit son
            périmètre. Les lignes des articles exclus ne sont pas chargées —
            elles ne sont pas non plus refusées, sans quoi l'écriture entière
            serait annulée. Le décompte est en tête, parce qu'un périmètre trop
            large ou trop étroit se voit à ce chiffre-là et à aucun autre. */}
        {outOfScopeLines > 0 && (
          <Alert
            tone="info"
            title={`${outOfScopeLines.toLocaleString('fr-FR')} ligne(s) hors périmètre, non chargée(s)`}
          >
            {outOfScopeItems > 0 && (
              <>
                {outOfScopeItems.toLocaleString('fr-FR')} article(s) exclu(s) de
                cette campagne.{' '}
              </>
            )}
            Leur stock n’entre pas dans l’inventaire — c’est ce que l’exclusion
            veut dire. Pour en inventorier un, levez son exclusion sur la grille
            Articles puis rechargez le stock.
          </Alert>
        )}

        {/* L'autre moitié du même choix : une référence que le référentiel ne
            connaît pas encore. Écartée elle aussi, et pour la même raison — un
            refus annulerait tout le chargement — mais ce n'est pas une décision,
            c'est un manque, d'où le ton et le geste différents. Le constat
            survit à ce panneau : la vue Contrôles le reprend, avec la liste. */}
        {unknownLines > 0 && (
          <Alert
            tone="warning"
            title={`${unknownLines.toLocaleString('fr-FR')} ligne(s) sur des références inconnues, non chargée(s)`}
          >
            {unknownItems > 0 && (
              <>
                {unknownItems.toLocaleString('fr-FR')} référence(s) absente(s) du
                référentiel articles{unknownSample.length > 0 && <> : {unknownSample.join(' · ')}
                {unknownItems > unknownSample.length && ' …'}</>}.{' '}
              </>
            )}
            Aucun écart ne sera calculé dessus. Complétez le référentiel articles
            puis rechargez le stock — un import de stock ne crée jamais d’article.
            La liste complète reste dans <strong>Contrôles</strong>.
          </Alert>
        )}

        {warnings.length > 0 && (
          <Alert tone="warning" title={`${warnings.length} ligne(s) signalée(s)`}>
            <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
              {warnings.slice(0, 8).map((warning, index) => (
                <li key={index}>
                  Ligne {warning.line} — {warning.message}
                </li>
              ))}
            </ul>
          </Alert>
        )}

        {unknownColumns.length > 0 && (
          <Alert tone="info" title="Colonnes ignorées">
            {unknownColumns.join(', ')} — ces colonnes ne sont pas utilisées par
            cette grille et n’empêchent pas l’import.
          </Alert>
        )}

        {duplicateKeys.length > 0 && (
          <Alert tone="warning" title={`${duplicateKeys.length} doublon(s) de clé`}>
            {duplicateKeys.slice(0, 5).join(' · ')}
            {duplicateKeys.length > 5 && ' …'}
          </Alert>
        )}

        {errors.length > 0 && (
          <div className="card" style={{ borderColor: 'var(--danger-border)' }}>
            <div className="card__head">
              <h3 className="card__title" style={{ fontSize: 'var(--text-base)' }}>
                Lignes rejetées
                {result.truncatedErrors > 0 && (
                  <span className="subtle"> (+{result.truncatedErrors} non affichées)</span>
                )}
              </h3>
            </div>
            <div className="table-wrap" style={{ maxHeight: 260 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th className="num">Ligne</th>
                    <th>Colonne</th>
                    <th>Valeur</th>
                    <th>Motif</th>
                  </tr>
                </thead>
                <tbody>
                  {errors.map((error, index) => (
                    <tr key={index}>
                      <td className="num">{error.line}</td>
                      <td className="mono">{error.column}</td>
                      <td className="mono">{error.value ?? '—'}</td>
                      <td>{error.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {Object.keys(details).length > 0 && (
          <Card title="Effets de cet import" className="card">
            <dl className="kv">
              {Object.entries(details).map(([key, value]) => (
                <div key={key} style={{ display: 'contents' }}>
                  <dt>{DETAIL_LABELS[key] ?? key}</dt>
                  <dd className="num">
                    {Array.isArray(value) ? value.join(', ') || '—' : String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        )}

        {!done && sample.length > 0 && (
          <Card title="Aperçu des données" flush>
            <DataGrid
              columns={columnsFromContract(contract)}
              rows={sample}
              getRowId={(_, index) => String(index)}
              searchable={false}
              maxHeight={320}
              dense
            />
          </Card>
        )}
      </div>
    </Card>
  )
}

const DETAIL_LABELS: Record<string, string> = {
  newLocations: 'Nouveaux emplacements découverts',
  totalLocations: 'Emplacements au référentiel',
  journalsCreated: 'Journaux de comptage créés',
  journalsTouched: 'Journaux mis à jour',
  journalsPosted: 'Journaux postés',
  journalsInProgress: 'Journaux en cours',
  disabledLocationsSkipped: 'Emplacements désactivés ignorés',
  warehouses: 'Entrepôts',
  replacedLinks: 'Liens de nomenclature remplacés',
  bomCycles: 'Cycles de nomenclature détectés',
}
