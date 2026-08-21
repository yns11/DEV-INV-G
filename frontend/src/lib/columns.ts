/**
 * Quelles colonnes l'utilisateur a masquées, par grille.
 *
 * Les grilles de cette application sont larges : le référentiel articles porte
 * douze colonnes, la comparaison quinze. Toutes servent à quelqu'un, aucune ne
 * sert à tout le monde le même jour — celui qui vérifie des prix n'a que faire
 * du programme, celui qui prépare des feuilles n'a que faire des prix.
 *
 * Le choix se mémorise, sinon il se referait à chaque navigation. Comme pour
 * les blocs repliés, **seules les colonnes masquées sont enregistrées** : le
 * défaut reste donc « visible », y compris pour une colonne ajoutée depuis. Une
 * grille qui s'ouvrirait avec des colonnes manquantes parce qu'une clé inconnue
 * a été lue comme masquée serait pire que pas de mémoire du tout.
 */

import { useCallback, useMemo, useState, useSyncExternalStore } from 'react'

const STORAGE_KEY = 'campagnes-inventaire.hidden-columns'

type Hidden = Record<string, string[]>

function read(): Hidden {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : {}
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>)
        .filter(([, v]) => Array.isArray(v))
        .map(([k, v]) => [k, (v as unknown[]).map(String)]),
    )
  } catch {
    // Stockage indisponible, ou écrit par une version antérieure : tout
    // visible est le repli sûr.
    return {}
  }
}

let hidden = read()
const listeners = new Set<() => void>()

function emit(): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(hidden))
  } catch {
    /* le choix tient quand même pour cette session */
  }
  listeners.forEach((listener) => listener())
}

/** Masque ou réaffiche une colonne de la grille *grid*. */
export function setColumnHidden(grid: string, key: string, value: boolean): void {
  const current = hidden[grid] ?? []
  if (current.includes(key) === value) return
  const next = value ? [...current, key] : current.filter((k) => k !== key)
  hidden = { ...hidden }
  if (next.length) hidden[grid] = next
  else delete hidden[grid]
  emit()
}

/** Réaffiche toutes les colonnes de *grid*. */
export function showAllColumns(grid: string): void {
  if (!hidden[grid]) return
  hidden = { ...hidden }
  delete hidden[grid]
  emit()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

const NONE: string[] = []

/**
 * Les colonnes masquées de *grid*, et de quoi les changer.
 *
 * ``grid`` nul — une grille sans nom, donc sans identité stable d'une visite à
 * l'autre — bascule sur un état local : le sélecteur fonctionne pour la
 * session et ne laisse rien derrière lui, plutôt que d'écrire dans le stockage
 * sous une clé qui ne se retrouvera jamais.
 */
export function useHiddenColumns(grid: string | null): {
  hidden: ReadonlySet<string>
  toggle: (key: string, value: boolean) => void
  reset: () => void
} {
  const stored = useSyncExternalStore(
    subscribe,
    () => (grid ? hidden[grid] ?? NONE : NONE),
    () => NONE,
  )
  const [local, setLocal] = useState<string[]>(NONE)
  const keys = grid ? stored : local

  const toggle = useCallback(
    (key: string, value: boolean) => {
      if (grid) {
        setColumnHidden(grid, key, value)
        return
      }
      setLocal((current) =>
        value
          ? current.includes(key) ? current : [...current, key]
          : current.filter((k) => k !== key),
      )
    },
    [grid],
  )
  const reset = useCallback(() => {
    if (grid) showAllColumns(grid)
    else setLocal(NONE)
  }, [grid])

  return { hidden: useMemo(() => new Set(keys), [keys]), toggle, reset }
}
