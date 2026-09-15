/**
 * Poser une cause sur un écart, depuis l'écran où on le regarde.
 *
 * La cause se décidait dans un seul endroit — la vue Causes — avec une liste
 * déroulante par ligne et aucun commentaire. Or c'est en regardant les chiffres
 * qu'on sait quoi écrire : le stock ERP, le compté, la décomposition qu'on vient
 * d'ouvrir. Faire le tour par un autre écran pour noter ce qu'on vient de
 * comprendre est le meilleur moyen de ne pas le noter.
 *
 * Une fenêtre plutôt qu'une cellule éditable : le commentaire est du texte
 * libre, et une zone de saisie de trois lignes ne tient pas dans une grille qui
 * porte déjà douze colonnes.
 *
 * Deux usages, une seule fenêtre
 * ------------------------------
 * **Une ligne** — les champs montrent ce qu'elle porte déjà, et l'enregistrement
 * écrit exactement ce qui est affiché. Vider le commentaire l'efface : le champ
 * montrait sa valeur, donc l'effacer est un geste.
 *
 * **Un lot** — les champs s'ouvrent à blanc, puisqu'ils ne peuvent pas montrer
 * vingt valeurs différentes. Un commentaire laissé vide ne touche alors à rien :
 * il veut dire « je n'en parle pas », et la fenêtre le dit en toutes lettres
 * plutôt que de laisser deviner.
 */

import { useState } from 'react'
import type { AssignableCause } from '../lib/types'
import { Button, Field, Modal } from './ui'

export function CauseDialog({
  /** Le nombre de lignes visées. 1 pour une ligne, N pour un lot. */
  count,
  /** Ce que porte la ligne — jamais renseigné pour un lot. */
  initialCause = null,
  initialComment = '',
  causes,
  title,
  pending = false,
  onSubmit,
  onClose,
}: {
  count: number
  initialCause?: string | null
  initialComment?: string
  causes: AssignableCause[]
  title: string
  pending?: boolean
  onSubmit: (cause: string | null, comment: string) => void
  onClose: () => void
}) {
  const [cause, setCause] = useState(initialCause ?? '')
  const [comment, setComment] = useState(initialComment)
  const batch = count > 1

  return (
    <Modal
      title={title}
      onClose={onClose}
      width={560}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={pending}
            onClick={() => onSubmit(cause || null, comment)}
          >
            {batch ? `Affecter aux ${count} lignes` : 'Enregistrer'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Cause retenue">
          <select
            className="select"
            value={cause}
            onChange={(event) => setCause(event.target.value)}
          >
            <option value="">— non affectée —</option>
            {causes.map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.code} — {entry.label}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Commentaire"
          hint={
            batch
              ? 'Laissé vide, il ne touche pas aux commentaires déjà écrits sur ces lignes.'
              : 'Ce qui a été constaté, et ce qu’il reste à faire.'
          }
        >
          <textarea
            className="input"
            rows={4}
            value={comment}
            placeholder={
              batch
                ? 'Le même commentaire sur les lignes sélectionnées…'
                : 'Palette retrouvée en zone B, à recompter…'
            }
            onChange={(event) => setComment(event.target.value)}
          />
        </Field>
        {/* Dit avant l'enregistrement, pas après : c'est une écriture sur
            plusieurs lignes, et la seule façon de la défaire est de la refaire
            autrement. */}
        {batch && (
          <span className="subtle">
            La cause choisie remplacera celle des {count} lignes sélectionnées,
            y compris celles qui en portent déjà une.
          </span>
        )}
      </div>
    </Modal>
  )
}
