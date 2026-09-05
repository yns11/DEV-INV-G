/** L'aperçu de la feuille telle qu'elle sera imprimée, et son édition.
 *
 * En préparation, « Ouvrir » menait à la liste plate des lignes : une grille de
 * référence, section, unité, dans laquelle on ne voyait pas la feuille. Or ce
 * qu'un préparateur décide à ce moment-là, c'est justement un **document** —
 * l'ordre des articles, les intertitres qui disent où aller les chercher, les
 * respirations entre les groupes. Les classeurs Excel qu'on remplace le
 * faisaient, et c'est la première chose que les préparateurs ont réclamée.
 *
 * L'écran montre donc la page : les trois sections dans l'ordre où elles
 * s'impriment, chacune sous son en-tête — modifiable — et ses lignes dans
 * l'ordre du document. Insérer un intertitre, une ligne vide, déplacer,
 * supprimer : chaque geste porte sur ce qui sortira de l'imprimante.
 *
 * Rien n'est écrit avant « Enregistrer ». La sous-section portée par chaque
 * ligne d'article n'est pas éditée ici : le serveur la relit dans l'ordre des
 * lignes reçues, parce que deux endroits où saisir la même chose sont deux
 * endroits qui finissent par se contredire.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Sheet, Zone } from '../lib/types'
import { DEFAULT_SECTION_TITLES } from '../lib/format'
import { sectionLabel } from './sectionColumn'
import {
  Alert,
  AsyncBoundary,
  Button,
  Icons,
  Modal,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

/** Les sections dans l'ordre où elles s'impriment. */
export const PRINTED_SECTIONS = ['LINE_SIDE', 'WIP', 'WIP_OK'] as const

/** Une ligne de la feuille, telle que l'aperçu la manipule. */
export interface LayoutLine extends Record<string, unknown> {
  id?: string
  item_number?: string
  name?: string
  section?: string
  line_kind?: string
  label?: string
  unit?: string
}

const kindOf = (line: LayoutLine) => String(line.line_kind ?? 'ARTICLE')

/**
 * Réinsère une ligne ailleurs dans la liste.
 *
 * Séparé du composant pour être vérifiable : c'est le seul endroit où l'ordre
 * du document change, et un décalage d'un rang y déplacerait un intertitre
 * d'un article — donc trois articles d'un emplacement à un autre.
 */
export function moveLine<T>(lines: T[], from: number, to: number): T[] {
  if (from === to || to < 0 || to >= lines.length) return lines
  const next = [...lines]
  const [line] = next.splice(from, 1)
  if (line === undefined) return lines
  next.splice(to, 0, line)
  return next
}

export function SheetLayoutModal({
  campaignId,
  zone,
  sheet,
  onClose,
}: {
  campaignId: string
  zone: Zone
  sheet: Sheet
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const query = useQuery({
    queryKey: ['sheet', campaignId, sheet.id],
    queryFn: () => api.sheet(campaignId, sheet.id),
  })

  const [draft, setDraft] = useState<LayoutLine[] | null>(null)
  // Les en-têtes tels qu'ils sont *personnalisés*. Le champ vide n'est pas
  // pré-rempli avec le défaut : vider un champ veut dire « reprends le défaut »,
  // et y recopier le texte figerait chaque zone sur le texte du jour.
  const [titles, setTitles] = useState<Record<string, string> | null>(null)

  const lines: LayoutLine[] =
    draft ?? ((query.data?.lines as LayoutLine[] | undefined) ?? [])
  const headers = titles ?? (zone.section_labels ?? {})

  const save = useMutation({
    mutationFn: async () => {
      await api.setSectionLabels(campaignId, zone.id, headers)
      return api.saveSheetLines(
        campaignId,
        sheet.id,
        lines.map((line, index) => ({
          id: line.id ?? null,
          itemNumber: String(line.item_number ?? ''),
          section: String(line.section ?? 'LINE_SIDE'),
          lineKind: kindOf(line),
          label: String(line.label ?? ''),
          unit: String(line.unit ?? 'PCE'),
          displayOrder: index,
        })),
        true,
        Number(query.data?.sheet?.row_version) || undefined,
      )
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setDraft(null)
      setTitles(null)
      toast.success(`${result.written} ligne(s) enregistrée(s)`)
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const edit = (next: LayoutLine[]) => setDraft(next)

  const insertAfter = (index: number, kind: 'SUBSECTION' | 'SPACER', section: string) => {
    const next = [...lines]
    next.splice(index + 1, 0, {
      section,
      line_kind: kind,
      label: kind === 'SUBSECTION' ? 'Nouvel intertitre' : '',
      item_number: '',
      unit: '',
    })
    edit(next)
  }

  /** Les lignes d'une section, avec leur rang dans le document entier. */
  const bySection = useMemo(() => {
    const groups: Record<string, Array<{ line: LayoutLine; index: number }>> = {}
    for (const section of PRINTED_SECTIONS) groups[section] = []
    lines.forEach((line, index) => {
      const section = String(line.section ?? 'LINE_SIDE')
      const group = (groups[section] ??= [])
      group.push({ line, index })
    })
    return groups
  }, [lines])

  const dirty = draft !== null || titles !== null

  return (
    <Modal
      title={`${zone.label || zone.code} — aperçu de la feuille imprimée`}
      onClose={onClose}
      width={980}
      footer={
        <>
          <span className="spacer" />
          {dirty && (
            <Button
              variant="ghost"
              onClick={() => {
                setDraft(null)
                setTitles(null)
              }}
            >
              Annuler les modifications
            </Button>
          )}
          <Button
            variant="primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Enregistrement…' : 'Enregistrer'}
          </Button>
        </>
      }
    >
      <AsyncBoundary query={query} skeleton={<Skeleton height={320} />}>
        {() => (
          <div className="stack">
            <Alert tone="info" title="Ce que le compteur aura sous les yeux">
              L’ordre, les intertitres et les lignes vides sont ceux de la feuille
              imprimée — et du formulaire de saisie. Un article peut revenir sous
              plusieurs intertitres : ce sont autant de comptages, à autant
              d’endroits.
            </Alert>

            {PRINTED_SECTIONS.map((section) => {
              const group = bySection[section] ?? []
              return (
                <section key={section} className="stack" style={{ gap: 'var(--space-2)' }}>
                  <label className="stack" style={{ gap: 2 }}>
                    <span className="subtle">
                      En-tête imprimé — {sectionLabel(section)}
                    </span>
                    <input
                      value={headers[section] ?? ''}
                      placeholder={DEFAULT_SECTION_TITLES[section]}
                      aria-label={`En-tête de la section ${sectionLabel(section)}`}
                      onChange={(event) =>
                        setTitles({ ...headers, [section]: event.target.value })
                      }
                    />
                    <span className="subtle">
                      Laissez vide pour reprendre le texte par défaut.
                    </span>
                  </label>

                  <table className="data">
                    <tbody>
                      {group.length === 0 && (
                        <tr>
                          <td colSpan={3} className="subtle">
                            Aucune ligne dans cette section.
                          </td>
                        </tr>
                      )}
                      {group.map(({ line, index }, position) => {
                        const kind = kindOf(line)
                        return (
                          <tr
                            key={line.id ?? `new-${index}`}
                            className={kind === 'ARTICLE' ? undefined : 'row--layout'}
                          >
                            <td>
                              {kind === 'SUBSECTION' ? (
                                <input
                                  value={String(line.label ?? '')}
                                  aria-label="Texte de l’intertitre"
                                  onChange={(event) => {
                                    const next = [...lines]
                                    next[index] = { ...line, label: event.target.value }
                                    edit(next)
                                  }}
                                />
                              ) : kind === 'SPACER' ? (
                                <span className="subtle">— ligne vide —</span>
                              ) : (
                                <span>
                                  <strong className="mono">
                                    {String(line.item_number ?? '')}
                                  </strong>{' '}
                                  <span className="muted">{String(line.name ?? '')}</span>
                                </span>
                              )}
                            </td>
                            <td style={{ width: 90 }} className="subtle">
                              {kind === 'ARTICLE' ? String(line.unit ?? '') : ''}
                            </td>
                            <td style={{ width: 220 }}>
                              <span className="row" style={{ gap: 'var(--space-2)' }}>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  icon={<Icons.chevronUp size={13} />}
                                  disabled={position === 0}
                                  aria-label="Monter la ligne"
                                  title="Monter la ligne"
                                  onClick={() =>
                                    edit(moveLine(lines, index, group[position - 1]!.index))
                                  }
                                />
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  icon={<Icons.chevronDown size={13} />}
                                  disabled={position === group.length - 1}
                                  aria-label="Descendre la ligne"
                                  title="Descendre la ligne"
                                  onClick={() =>
                                    edit(moveLine(lines, index, group[position + 1]!.index))
                                  }
                                />
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  aria-label="Insérer un intertitre en dessous"
                                  title="Insérer un intertitre en dessous"
                                  onClick={() => insertAfter(index, 'SUBSECTION', section)}
                                >
                                  Intertitre
                                </Button>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  aria-label="Insérer une ligne vide en dessous"
                                  title="Insérer une ligne vide en dessous"
                                  onClick={() => insertAfter(index, 'SPACER', section)}
                                >
                                  Ligne vide
                                </Button>
                                {/* N'importe quelle ligne, article compris.
                                    Renvoyer vers « Toutes les lignes » pour
                                    retirer une référence obligeait à quitter
                                    l'aperçu, à retrouver la ligne dans une
                                    liste plate, et à revenir vérifier le
                                    résultat — pour un geste qui est de la
                                    préparation de feuille comme les autres. */}
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  icon={<Icons.trash size={13} />}
                                  aria-label="Supprimer cette ligne"
                                  title="Supprimer cette ligne"
                                  onClick={() =>
                                    edit(lines.filter((_, i) => i !== index))
                                  }
                                />
                              </span>
                            </td>
                          </tr>
                        )
                      })}
                      <tr>
                        <td colSpan={3}>
                          <span className="row" style={{ gap: 'var(--space-2)' }}>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<Icons.plus size={13} />}
                              onClick={() =>
                                insertAfter(
                                  group.length ? group[group.length - 1]!.index : lines.length - 1,
                                  'SUBSECTION',
                                  section,
                                )
                              }
                            >
                              Ajouter un intertitre
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<Icons.plus size={13} />}
                              onClick={() =>
                                insertAfter(
                                  group.length ? group[group.length - 1]!.index : lines.length - 1,
                                  'SPACER',
                                  section,
                                )
                              }
                            >
                              Ajouter une ligne vide
                            </Button>
                          </span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </section>
              )
            })}
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}
