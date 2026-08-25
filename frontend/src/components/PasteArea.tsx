import { useEffect, useRef, useState } from 'react'

/**
 * La zone où l'on colle un bloc venu d'Excel.
 *
 * Ce qu'elle change à une `<textarea>` ordinaire : **Tab insère une
 * tabulation**. C'est le séparateur de colonnes du presse-papier d'Excel, donc
 * le caractère dont on a besoin ici, et c'était le seul qu'on ne pouvait pas
 * taper — la touche envoyait le focus sur le bouton suivant. Ajouter une
 * colonne oubliée à la main obligeait à ouvrir un éditeur à côté, à y composer
 * la ligne, puis à la recoller.
 *
 * Le prix est réel : une touche qui déplaçait le focus ne le déplace plus, et
 * qui navigue au clavier se retrouve enfermé. **Échap libère** — la tabulation
 * suivante ressort du champ — et la frappe suivante rend la touche à son rôle
 * ici. C'est le compromis habituel des éditeurs de code, et il laisse une
 * sortie qui ne demande pas la souris.
 */
export function PasteArea({
  value,
  onChange,
  placeholder,
  autoFocus,
  rows,
  'aria-label': ariaLabel,
}: {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  autoFocus?: boolean
  rows?: number
  'aria-label'?: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  /** Où replacer le curseur après une insertion, `null` s'il ne bouge pas. */
  const caret = useRef<number | null>(null)
  /** Échap a été pressé : la prochaine tabulation sort du champ. */
  const [released, setReleased] = useState(false)

  // Après le rendu, et pas avant : la valeur passe par le parent, et poser le
  // curseur tout de suite le verrait sauter en fin de texte au rendu suivant.
  useEffect(() => {
    const at = caret.current
    if (at === null || !ref.current) return
    caret.current = null
    ref.current.setSelectionRange(at, at)
  }, [value])

  return (
    <textarea
      ref={ref}
      className="textarea mono"
      value={value}
      autoFocus={autoFocus}
      rows={rows}
      aria-label={ariaLabel}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      onBlur={() => setReleased(false)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          setReleased(true)
          return
        }
        if (event.key !== 'Tab') {
          // Reprendre la frappe, c'est revenir dans le texte : la touche
          // retrouve son rôle sans qu'on ait à y penser.
          if (released) setReleased(false)
          return
        }
        // Maj+Tab reste la navigation arrière : sans elle, un champ vide dont
        // on vient d'entrer serait sans issue tant qu'on n'a pas trouvé Échap.
        if (event.shiftKey || released) return
        event.preventDefault()
        const field = event.currentTarget
        const start = field.selectionStart
        const end = field.selectionEnd
        caret.current = start + 1
        onChange(`${value.slice(0, start)}\t${value.slice(end)}`)
      }}
    />
  )
}
