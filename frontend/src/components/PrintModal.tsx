/**
 * The print dialog, shared by the two screens that need it.
 *
 * Printing happens *before* counting: the sheets are prepared, handed out, and
 * only then filled in. It therefore belongs to the preparation screen as much
 * as to the counting one — and having one dialog rather than two is what keeps
 * the print matrix (which document is offered when) from drifting between them.
 */

import { useState } from 'react'

import { downloads } from '../lib/api'
import { PRINT_MODE_LABELS, type PrintMode } from '../lib/types'
import {
  Alert, Badge, Button, Field, Icons, Modal, Switch, useDownload,
} from './ui'

/**
 * Printing a counting sheet.
 *
 * A sheet is three different documents and only some of them exist at any given
 * moment: a zone with a pre-printed list has nothing to gain from a blank grid,
 * a free-entry zone has no list to print, and the record with quantities does
 * not exist before anything has been counted. Which modes apply is decided
 * server-side and arrives as `zone.printModes`; this dialog offers exactly
 * those, so a choice on screen is never one the endpoint will refuse.
 */
export function PrintModal({
  campaignId,
  sheetId,
  zoneIds,
  modes,
  zonesByMode,
  onClose,
}: {
  campaignId: string
  sheetId?: string
  /**
   * Restrict the batch to these zones.
   *
   * Reprinting one sector, or the four zones whose stack got soaked, is the
   * common case the day after — and printing the whole site again to get them
   * is how a second, contradictory pile of paper ends up on the floor.
   */
  zoneIds?: string[]
  modes: PrintMode[]
  /** For the batch print: how many zones each mode would produce a sheet for. */
  zonesByMode?: Record<PrintMode, number>
  onClose: () => void
}) {
  const startDownload = useDownload()
  const [passNo, setPassNo] = useState<1 | 2>(1)
  const [mode, setMode] = useState<PrintMode>(modes[0] ?? 'list')
  const [withSources, setWithSources] = useState(false)
  const [blankLines, setBlankLines] = useState('40')

  const lines = Number(blankLines)
  const linesInvalid =
    mode === 'blank' && (!Number.isInteger(lines) || lines < 10 || lines > 180)

  const print = () => {
    const options = {
      mode,
      withSources: mode === 'filled' && withSources,
      blankLines: mode === 'blank' ? lines : undefined,
    }
    startDownload(
      sheetId
        ? downloads.countingSheet(campaignId, sheetId, options)
        : downloads.allCountingSheets(campaignId, passNo, {
            ...options,
            zoneIds: zoneIds?.length ? zoneIds.join(',') : undefined,
          }),
    )
    onClose()
  }

  if (modes.length === 0) {
    return (
      <Modal
        title="Impression"
        onClose={onClose}
        width={520}
        footer={<Button onClick={onClose}>Fermer</Button>}
      >
        <Alert tone="info" title="Rien à imprimer pour l’instant">
          Aucune zone n’a de liste d’articles ni de saisie libre déclarée.
        </Alert>
      </Modal>
    )
  }

  return (
    <Modal
      title={
        sheetId
          ? 'Imprimer cette feuille'
          : zoneIds?.length
            ? `Imprimer ${zoneIds.length} zone(s)`
            : 'Imprimer les feuilles'
      }
      onClose={onClose}
      width={600}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            icon={<Icons.printer size={14} />}
            disabled={linesInvalid}
            onClick={print}
          >
            Imprimer
          </Button>
        </>
      }
    >
      <div className="stack">
        {!sheetId && (
          <Field label="Comptage">
            <div className="segmented">
              {([1, 2] as const).map((value) => (
                <button
                  key={value}
                  className={`segmented__item${passNo === value ? ' segmented__item--active' : ''}`}
                  onClick={() => setPassNo(value)}
                >
                  Comptage n°{value}
                </button>
              ))}
            </div>
          </Field>
        )}

        <Field label="Document">
          <div className="chips">
            {modes.map((value) => (
              <button
                key={value}
                className={`chip${mode === value ? ' chip--active' : ''}`}
                onClick={() => setMode(value)}
              >
                {PRINT_MODE_LABELS[value]}
                {zonesByMode && (
                  <Badge tone="neutral">{zonesByMode[value] ?? 0} zone(s)</Badge>
                )}
              </button>
            ))}
          </div>
        </Field>

        {mode === 'blank' && (
          <Field
            label="Nombre de lignes"
            hint="Entre 10 et 180."
            error={linesInvalid ? 'Entier entre 10 et 180.' : undefined}
          >
            <input
              className="input num"
              inputMode="numeric"
              value={blankLines}
              onChange={(event) => setBlankLines(event.target.value.trim())}
            />
          </Field>
        )}

        {mode === 'filled' && (
          <Switch
            checked={withSources}
            onChange={setWithSources}
            label="Ajouter les colonnes Source et Commentaire"
          />
        )}

        <Alert tone="info" title={PRINT_MODE_LABELS[mode]}>
          {mode === 'blank' && 'Une grille vide : le compteur écrit la référence et la quantité.'}
          {mode === 'list' && 'La liste à parcourir, plus 5 lignes libres en bord de ligne, 3 en WIP et 2 en WIP terminé.'}
          {mode === 'filled' && 'Toutes les lignes portant une référence, y compris « non compté ». Aucune ligne vide ajoutée.'}
        </Alert>
      </div>
    </Modal>
  )
}
