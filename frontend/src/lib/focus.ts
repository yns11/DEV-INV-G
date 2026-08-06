/**
 * The « Mon périmètre » switch.
 *
 * A single boolean, persisted per browser, that every screen reads so the top
 * bar and the lists can never disagree about whether the filter is on. It is
 * kept outside React state on purpose: the switch lives in the campaign header
 * while the lists it filters live several routes away, and threading a prop
 * through would mean the header owns data it does not display.
 *
 * The value only ever *asks* for filtering. What a perimeter contains is
 * resolved by the server from the signed-in identity — the browser never names
 * a manager, and never receives the objects the filter excluded.
 */

import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'campagnes-inventaire.focus'

function read(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true'
  } catch {
    // Private browsing, or storage disabled by policy. Defaulting to "off"
    // is the safe reading: a filter nobody asked for that silently hides work
    // is worse than a list that is too long.
    return false
  }
}

let enabled = read()
const listeners = new Set<() => void>()

export function setFocusEnabled(value: boolean): void {
  if (value === enabled) return
  enabled = value
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    /* the switch still works for this session */
  }
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** `[enabled, setEnabled]`, shared by every component that mounts it. */
export function useFocusMode(): [boolean, (value: boolean) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => enabled,
    () => false,
  )
  return [value, setFocusEnabled]
}
