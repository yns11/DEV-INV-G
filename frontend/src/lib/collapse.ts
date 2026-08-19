/**
 * Quels blocs l'utilisateur a repliés.
 *
 * Les écrans empilent des blocs pleine largeur — filtres, KPI, graphiques,
 * grilles — et tous ne servent pas à la même personne le même jour. Replier ce
 * qu'on n'utilise pas remonte le reste au-dessus de la ligne de flottaison, à
 * condition que le choix tienne : un bloc qui se rouvre à chaque navigation est
 * un bloc qu'on replie dix fois par jour.
 *
 * Seuls les blocs *repliés* sont mémorisés. Le défaut reste donc « ouvert »,
 * y compris pour un bloc apparu depuis — un écran qui s'ouvre à moitié vide
 * parce qu'une clé inconnue a été lue comme fermée serait pire que pas de
 * mémoire du tout.
 */

import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'campagnes-inventaire.collapsed'

function read(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(parsed) ? parsed.map(String) : [])
  } catch {
    // Stockage indisponible, ou contenu illisible parce qu'écrit par une
    // version antérieure : tout ouvert est le repli sûr.
    return new Set()
  }
}

let collapsed = read()
const listeners = new Set<() => void>()

/** Replie ou déplie *key*, et prévient tous les blocs montés. */
export function setCollapsed(key: string, value: boolean): void {
  if (collapsed.has(key) === value) return
  collapsed = new Set(collapsed)
  if (value) collapsed.add(key)
  else collapsed.delete(key)
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...collapsed]))
  } catch {
    /* le pli tient quand même pour cette session */
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** `[collapsed, setCollapsed]` pour un bloc donné. */
export function useCollapsed(key: string): [boolean, (value: boolean) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => collapsed.has(key),
    () => false,
  )
  return [value, (next: boolean) => setCollapsed(key, next)]
}
