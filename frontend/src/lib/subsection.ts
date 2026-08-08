/**
 * The view inside a screen, held in the URL rather than in component state.
 *
 * Two things stop working the moment a sub-section is local state: the sidebar
 * cannot show which one is open, and a link to "the thresholds grid" cannot
 * exist. Both matter here — the sidebar *is* the navigation now, and "look at
 * this" is how half the work gets handed over.
 *
 * A query parameter rather than a path segment: it survives a route that has no
 * sub-sections, and it keeps every screen's route declaration a single line.
 */

import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export const VIEW_PARAM = 'vue'

export function useSubSection<T extends string>(
  fallback: T,
  allowed: readonly T[],
): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get(VIEW_PARAM)
  // An unknown value in the URL falls back rather than rendering nothing: a
  // stale bookmark should land on the screen, not on a blank one.
  const current = allowed.includes(raw as T) ? (raw as T) : fallback

  const set = useCallback(
    (value: T) => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous)
          if (value === fallback) next.delete(VIEW_PARAM)
          else next.set(VIEW_PARAM, value)
          return next
        },
        { replace: true },
      )
    },
    [fallback, setParams],
  )

  return [current, set]
}

/** The href of a sub-section, for the sidebar. */
export function subSectionPath(base: string, id: string, fallback: string): string {
  return id === fallback ? base : `${base}?${VIEW_PARAM}=${encodeURIComponent(id)}`
}
