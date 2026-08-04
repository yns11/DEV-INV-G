/**
 * The import surface, shared by every grid.
 *
 * The behaviour the specification asks for, in order:
 *
 *  1. the expected columns are shown **first**, as an empty grid, so nobody has
 *     to guess what a file must contain;
 *  2. a file (or a paste) is validated in a dry run and the result is displayed
 *     — accepted, rejected, why, on which line — *before* anything is written;
 *  3. only then does the user confirm the import.
 *
 * That "see it before you commit it" loop is the single biggest behavioural
 * difference from pasting into a spreadsheet, and it is what stops a broken
 * file from silently becoming the campaign's truth.
 */

import { useRef, useState } from 'react'
import { api, downloads } from '../lib/api'
import type { GridContract, ImportResult } from '../lib/types'
import {
  Alert, Badge, Button, Card, Icons, useDownload, useErrorToast, useToast,
} from './ui'
import { DataGrid, columnsFromContract } from './DataGrid'

type Stage =
  | { kind: 'idle' }
  | { kind: 'validating' }
  | { kind: 'preview'; result: ImportResult; source: { file?: File; text?: string } }
  | { kind: 'importing' }
  | { kind: 'done'; result: ImportResult }

export function ImportPanel({
  campaignId,
  contract,
  target,
  disabled = false,
  disabledReason,
  replace = false,
  onImported,
  extraActions,
}: {
  campaignId: string
  contract: GridContract
  target: string
  disabled?: boolean
  disabledReason?: string
  replace?: boolean
  onImported?: (result: ImportResult) => void
  extraActions?: React.ReactNode
}) {
  const [stage, setStage] = useState<Stage>({ kind: 'idle' })
  const [pasteText, setPasteText] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const toast = useToast()
  const showError = useErrorToast()
  const startDownload = useDownload()

  const validate = async (source: { file?: File; text?: string }) => {
    setStage({ kind: 'validating' })
    try {
      const result = source.file
        ? await api.importFile(campaignId, target, source.file, { dryRun: true, replace })
        : await api.importPaste(campaignId, target, source.text ?? '', { dryRun: true })
      setStage({ kind: 'preview', result, source })
    } catch (error) {
      showError(error, 'Analyse du fichier impossible')
      setStage({ kind: 'idle' })
    }
  }

  const commit = async () => {
    if (stage.kind !== 'preview') return
    const { source } = stage
    setStage({ kind: 'importing' })
    try {
      const result = source.file
        ? await api.importFile(campaignId, target, source.file, { replace })
        : await api.importPaste(campaignId, target, source.text ?? '', { replace })
      setStage({ kind: 'done', result })
      setPasteText('')
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

  return (
    <div className="stack">
      {disabled && disabledReason && (
        <Alert tone="warning" title="Import verrouillé">
          {disabledReason}
        </Alert>
      )}

      <Card
        title={contract.title}
        message={contract.description}
        actions={
          <>
            {extraActions}
            <Button
              size="sm"
              icon={<Icons.download size={13} />}
              onClick={() => startDownload(downloads.gridTemplate(campaignId, contract.key))}
            >
              Télécharger le modèle
            </Button>
            <Button
              size="sm"
              variant="primary"
              icon={<Icons.upload size={13} />}
              disabled={disabled || stage.kind === 'validating' || stage.kind === 'importing'}
              onClick={() => fileInput.current?.click()}
            >
              Charger un fichier
            </Button>
          </>
        }
      >
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

        {contract.hint && (
          <Alert tone="info" title="Format attendu">
            {contract.hint}
          </Alert>
        )}

        <div className="stack" style={{ marginTop: 'var(--space-4)' }}>
          <div className="row-wrap">
            <strong style={{ fontSize: 'var(--text-sm)' }}>Colonnes attendues</strong>
            <span className="subtle">
              dans cet ordre — les en-têtes sont reconnus automatiquement, y compris
              les variantes de l’export ERP
            </span>
          </div>
          <div className="chips">
            {contract.fields.map((field) => (
              <span key={field.name} className="chip" title={field.aliases.join(' · ')}>
                {field.label}
                {field.required && <Badge tone="danger">requis</Badge>}
              </span>
            ))}
          </div>
          {contract.naturalKey.length > 0 && (
            <p className="subtle">
              Clé naturelle : {contract.naturalKey.join(' + ')} — les doublons sur
              cette clé sont signalés.
            </p>
          )}
        </div>

        <details style={{ marginTop: 'var(--space-4)' }}>
          <summary
            style={{
              cursor: 'pointer',
              fontSize: 'var(--text-sm)',
              fontWeight: 'var(--weight-medium)',
            }}
          >
            Coller un bloc depuis Excel
          </summary>
          <div className="stack" style={{ marginTop: 'var(--space-3)' }}>
            <textarea
              className="textarea mono"
              value={pasteText}
              disabled={disabled}
              onChange={(event) => setPasteText(event.target.value)}
              placeholder={
                'Collez ici (Ctrl+V) un bloc copié depuis Excel.\n' +
                'Avec ou sans ligne d’en-tête : elle est détectée automatiquement.'
              }
            />
            <div className="row">
              <Button
                variant="primary"
                size="sm"
                disabled={disabled || !pasteText.trim()}
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
        </details>
      </Card>

      {stage.kind === 'validating' && (
        <Alert tone="info" title="Analyse en cours…">
          Le fichier est validé ligne par ligne. Rien n’est encore enregistré.
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

function ImportReport({
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

        {warnings.length > 0 && (
          <Alert tone="warning" title={`${warnings.length} correction(s) automatique(s)`}>
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
