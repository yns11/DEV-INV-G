/** La lecture d'une pile de feuilles remplies à la main. */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { MultiScanReport, ScanJob, Zone } from '../lib/types'
import { percent } from '../lib/format'
import { Alert, Badge, Button, Modal, Progress, useErrorToast } from '../components/ui'

// --------------------------------------------------------------------------- //
// Printing
// --------------------------------------------------------------------------- //

/**
 * The print dialog.
 *
 * Three jobs share one document, and the difference between them is worth
 * spelling out rather than hiding behind three buttons: the blank form handed
 * to a counter, the record of what came back, and the free-entry sheet with
 * nothing pre-printed at all.
 */

/**
 * Reading a whole stack of sheets in one go.
 *
 * The pages are routed to their sheets by the identifier the application itself
 * printed in the footer. Two outcomes are surfaced loudly because both are ones
 * a silent import would bury: a page nobody could attribute, and a sheet whose
 * AI reading somebody has already corrected by hand.
 */
/**
 * L'avancement d'une lecture de pile, en clair.
 *
 * Six minutes de silence sont indistinguables d'une panne : c'est l'étape en
 * cours et le compteur de feuilles qui font la différence, pas le pourcentage
 * seul — « 0 % » pendant deux minutes de rendu n'apprend rien, « Préparation
 * des pages » si.
 */
/**
 * Où en est la lecture d'un scan — une pile, ou une feuille seule.
 *
 * **Deux barres, parce qu'il y a deux vérités.** Sur une pile, « douze feuilles
 * sur cent » est une mesure : la barre se remplit et le pourcentage veut dire
 * quelque chose. Sur une feuille seule, l'essentiel du temps part dans **un**
 * appel au modèle, dont personne ne connaît l'avancement : une barre qui
 * sauterait de 0 à 100 % ne mesurerait rien et laisserait croire à une panne
 * pendant toute la minute qu'elle passe à zéro. C'est donc une barre
 * indéterminée, doublée de l'étape en cours — qui, elle, avance vraiment.
 */
export function ScanProgress({ state }: { state: ScanJob | undefined }) {
  if (!state) return <p className="subtle">Mise en file…</p>
  const running = state.status === 'RUNNING' || state.status === 'QUEUED'
  const measurable = state.sheetsTotal > 1
  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }}>
      <div className="row">
        <Badge tone={running ? 'info' : 'success'}>{state.step || 'En file'}</Badge>
        {state.totalPages > 0 && (
          <span className="subtle">{state.totalPages} page(s)</span>
        )}
        {measurable && (
          <span className="subtle">
            {state.sheetsDone}/{state.sheetsTotal} feuille(s) lue(s)
          </span>
        )}
      </div>
      {measurable ? (
        <Progress
          total={state.sheetsTotal}
          segments={[
            {
              label: 'Feuilles lues',
              value: state.sheetsDone,
              color: 'var(--accent)',
            },
          ]}
          caption={running ? `${state.percent} %` : null}
        />
      ) : (
        <div
          className={`progress__track${running ? ' progress__track--pending' : ''}`}
          role="progressbar"
          aria-label="Lecture du scan"
          aria-valuetext={state.step || 'En file'}
        >
          {!running && (
            <div className="progress__fill" style={{ width: '100%', background: 'var(--accent)' }} />
          )}
        </div>
      )}
    </div>
  )
}

export function MultiScanModal({
  campaignId,
  file,
  zones,
  onBusy,
  onClose,
}: {
  campaignId: string
  file: File
  zones: Zone[]
  onBusy: (busy: boolean) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const showError = useErrorToast()
  const [jobId, setJobId] = useState<string | null>(null)

  const atRisk = zones.flatMap((zone) =>
    zone.sheets
      .filter((sheet) => sheet.correctedLines > 0)
      .map((sheet) => ({ zone, sheet })),
  )

  // Le dépôt rend un travail, pas un rapport : la lecture d'une pile de cent
  // feuilles dure des minutes, et l'attendre dans la requête de chargement
  // faisait couper la passerelle avant la fin.
  const scan = useMutation({
    mutationFn: (overwrite: boolean) =>
      api.scanMultipleSheets(campaignId, file, overwrite),
    onMutate: () => onBusy(true),
    onSuccess: (queued) => setJobId(queued.id),
    onError: (error) => {
      onBusy(false)
      showError(error, 'Dépôt du scan impossible')
      onClose()
    },
  })

  // Tant que le travail tourne, on redemande où il en est. Deux secondes : assez
  // souvent pour que la barre bouge, assez rare pour ne pas peser sur une base
  // qui écrit en même temps cent feuilles de comptage.
  const job = useQuery({
    queryKey: ['scan-job', campaignId, jobId],
    queryFn: () => api.scanJob(campaignId, jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.isDone ? false : 2000),
  })

  const finished = job.data?.isDone ?? false
  useEffect(() => {
    if (!finished) return
    onBusy(false)
    void queryClient.invalidateQueries()
  }, [finished, onBusy, queryClient])

  // --- la lecture est en cours : on montre où elle en est ---------------------
  if (jobId && !finished) {
    const state = job.data
    return (
      <Modal title="Lecture du scan en cours" onClose={onClose} width={620}>
        <div className="stack">
          <p>
            <strong className="mono">{file.name}</strong> — vous pouvez fermer
            cette fenêtre : la lecture continue et les feuilles se remplissent au
            fur et à mesure.
          </p>
          <ScanProgress state={state} />
        </div>
      </Modal>
    )
  }

  // --- terminé en échec ------------------------------------------------------
  if (finished && job.data?.status === 'FAILED') {
    return (
      <Modal
        title="Scan multi-feuilles — échec"
        onClose={onClose}
        width={620}
        footer={
          <Button variant="primary" onClick={onClose}>
            Fermer
          </Button>
        }
      >
        <Alert tone="danger" title="La lecture n’a pas abouti">
          {job.data.error || 'Raison inconnue.'}
        </Alert>
      </Modal>
    )
  }

  const report = (finished ? job.data?.report : null) as MultiScanReport | null

  if (report) {
    return (
      <Modal
        title="Scan multi-feuilles — résultat"
        onClose={onClose}
        width={840}
        footer={
          <Button variant="primary" onClick={onClose}>
            Fermer
          </Button>
        }
      >
        <div className="stack">
          <dl className="kv">
            <dt>Pages lues</dt>
            <dd className="num">{report.pages}</dd>
            <dt>Feuilles renseignées</dt>
            <dd className="num">{report.sheetsProcessed.length}</dd>
            <dt>Feuilles préservées</dt>
            <dd className="num">{report.sheetsSkipped.length}</dd>
            <dt>Pages non attribuées</dt>
            <dd className="num">{report.unroutedPages.length}</dd>
          </dl>

          {report.sheetsProcessed.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: 240 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Feuille</th>
                    <th className="num">Pages</th>
                    <th className="num">Quantités lues</th>
                    <th className="num">Confiance</th>
                    <th>À vérifier</th>
                  </tr>
                </thead>
                <tbody>
                  {report.sheetsProcessed.map((sheet) => (
                    <tr key={sheet.sheetId}>
                      <td>
                        {sheet.zoneCode} — n°{sheet.passNo}
                      </td>
                      <td className="num">{sheet.pages.join(', ')}</td>
                      <td className="num">{sheet.counted}</td>
                      <td className="num">
                        {sheet.meanConfidence === null
                          ? '—'
                          : percent(sheet.meanConfidence)}
                      </td>
                      <td className="subtle">
                        {[
                          sheet.lowConfidence.length
                            ? `${sheet.lowConfidence.length} valeur(s) douteuse(s)`
                            : '',
                          sheet.missing.length
                            ? `${sheet.missing.length} non lue(s)`
                            : '',
                          sheet.overwroteCorrections
                            ? `${sheet.overwroteCorrections} correction(s) écrasée(s)`
                            : '',
                        ]
                          .filter(Boolean)
                          .join(' · ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {report.sheetsSkipped.length > 0 && (
            <Alert
              tone="success"
              title={`${report.sheetsSkipped.length} feuille(s) préservée(s)`}
            >
              Valeurs lues par l’IA puis corrigées à la main : elles n’ont pas été
              relues.
              <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
                {report.sheetsSkipped.map((sheet) => (
                  <li key={sheet.sheetId}>
                    {sheet.zoneCode} — n°{sheet.passNo} · {sheet.correctedLines}{' '}
                    ligne(s) corrigée(s)
                  </li>
                ))}
              </ul>
            </Alert>
          )}

          {report.unroutedPages.length > 0 && (
            <Alert
              tone="warning"
              title={`${report.unroutedPages.length} page(s) non attribuée(s)`}
            >
              Signalées plutôt que devinées. Ouvrez la feuille concernée et
              importez ces pages une par une.
              <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
                {report.unroutedPages.map((page) => (
                  <li key={page.page}>
                    Page {page.page} — {page.note}
                    {/* Ce que le modèle dit avoir lu, à côté de la raison :
                        « pied de page illisible » ne distingue pas une bande
                        abîmée d'une bande lisible que le rapprochement n'a pas
                        su résoudre, et les deux appellent des gestes opposés. */}
                    {page.read && (
                      <>
                        {' '}
                        <span className="subtle mono">(lu : {page.read})</span>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </Alert>
          )}
        </div>
      </Modal>
    )
  }

  return (
    <Modal
      title="Importer un scan de plusieurs feuilles"
      onClose={scan.isPending ? () => {} : onClose}
      width={700}
      footer={
        <>
          <Button variant="ghost" disabled={scan.isPending} onClick={onClose}>
            Annuler
          </Button>
          {atRisk.length > 0 && (
            <Button
              variant="danger"
              disabled={scan.isPending}
              onClick={() => scan.mutate(true)}
            >
              Lire et écraser les corrections
            </Button>
          )}
          <Button
            variant="primary"
            disabled={scan.isPending}
            onClick={() => scan.mutate(false)}
          >
            {scan.isPending ? 'Lecture en cours…' : 'Lire le scan'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <p>
          <strong className="mono">{file.name}</strong> — chaque page sera rattachée
          à sa feuille par l’identifiant que l’application a imprimé en pied de page.
          Une page dont le pied est illisible est signalée, jamais devinée.
        </p>

        {atRisk.length > 0 ? (
          <Alert
            tone="warning"
            title={`${atRisk.length} feuille(s) portent des corrections humaines`}
          >
            Elles seront <strong>préservées</strong> par défaut. « Lire et écraser »
            les relit quand même — à n’utiliser que si le scan est plus récent que
            les corrections.
            <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
              {atRisk.slice(0, 8).map(({ zone, sheet }) => (
                <li key={sheet.id}>
                  {zone.code} — comptage n°{sheet.pass_no === 'PASS_1' ? 1 : 2} ·{' '}
                  {sheet.correctedLines} ligne(s) corrigée(s)
                </li>
              ))}
            </ul>
          </Alert>
        ) : (
          <Alert tone="info" title="Aucune correction humaine en jeu">
            Aucune feuille ne porte de valeur IA corrigée à la main.
          </Alert>
        )}
      </div>
    </Modal>
  )
}
