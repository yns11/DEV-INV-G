/**
 * Le markdown que l'IA rend, peint en éléments React — jamais en HTML.
 *
 * Ce qu'il y avait
 * ---------------
 * L'assistant portait son propre rendu, enfermé dans son écran : gras, puces,
 * et rien d'autre. Un tableau demandé à un modèle revenait donc à l'écran sous
 * la forme où il l'avait écrit — des barres verticales et des tirets, alignés
 * sur rien, dans une bulle de discussion. Et la **Synthèse IA**, à qui le
 * serveur demande pourtant explicitement du markdown structuré en sections,
 * l'affichait entièrement brute : « ## Message clé » s'y lisait tel quel.
 *
 * Pourquoi pas du HTML
 * --------------------
 * Rendre la réponse en HTML serait plus court d'une centaine de lignes, et ce
 * serait une porte ouverte. Le dossier envoyé au modèle porte des désignations
 * d'articles, des commentaires de comptage, des pièces jointes — du texte venu
 * de fichiers que l'application n'écrit pas. Une réponse qui reprendrait une de
 * ces chaînes, et qu'on injecterait telle quelle, exécuterait ce qu'elle
 * contient. Ici **tout est un nœud texte** : le pire qu'un fichier hostile
 * obtienne est de s'afficher.
 *
 * C'est aussi ce qui permet aux tableaux d'être de vrais tableaux, stylés comme
 * ceux du reste de l'application plutôt que comme un bloc de texte à chasse
 * fixe.
 */

import type { ReactNode } from 'react'

/** Ce qu'une colonne de tableau déclare, ou ce qu'on en déduit. */
type Align = 'left' | 'center' | 'right' | null

/** Une ligne de séparation GFM : `|---|:--:|---:|`. */
const SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

/** Ce qui s'écrit dans une cellule et se lit comme un nombre. */
const NUMERIC = /^[-+]?[\d\s  ]*[\d][\d\s  ,.]*\s*(%|€|k€|M€|PCE|[A-Z]{1,3})?$/

export function Markdown({ text }: { text: string }) {
  return <>{blocksOf(text).map((block, index) => render(block, index))}</>
}

/**
 * Les blocs, séparés par une ligne vide — **sauf un tableau**.
 *
 * Un tableau collé à son paragraphe d'introduction, ce que les modèles font
 * volontiers, formait un seul bloc et se rendait en paragraphe. Les lignes qui
 * commencent par une barre sont donc détachées de ce qui les précède.
 */
function blocksOf(text: string): string[] {
  const out: string[] = []
  for (const chunk of text.replace(/\r\n/g, '\n').split(/\n{2,}/)) {
    let current: string[] = []
    let inTable = false
    for (const line of chunk.split('\n')) {
      const isRow = /^\s*\|/.test(line)
      if (isRow !== inTable && current.length) {
        out.push(current.join('\n'))
        current = []
      }
      inTable = isRow
      current.push(line)
    }
    if (current.length) out.push(current.join('\n'))
  }
  return out.filter((block) => block.trim() !== '')
}

function render(block: string, key: number): ReactNode {
  const lines = block.split('\n').filter((line) => line.trim() !== '')
  if (lines.length === 0) return null

  const table = tableOf(lines)
  if (table) return <Table key={key} {...table} />

  const heading = /^(#{1,4})\s+(.*)$/.exec(lines[0] ?? '')
  if (heading && lines.length === 1) {
    const level = Math.min(6, heading[1]!.length + 2)
    const Tag = `h${level}` as 'h3'
    return (
      <Tag key={key} className="md__heading">
        {inline(heading[2] ?? '')}
      </Tag>
    )
  }

  if (lines.every((line) => /^\s*\d+[.)]\s+/.test(line))) {
    return (
      <ol key={key} className="md__list">
        {lines.map((line, i) => (
          <li key={i}>{inline(line.replace(/^\s*\d+[.)]\s+/, ''))}</li>
        ))}
      </ol>
    )
  }

  if (lines.every((line) => /^\s*[-*•]\s+/.test(line))) {
    return (
      <ul key={key} className="md__list">
        {lines.map((line, i) => (
          <li key={i}>{inline(line.replace(/^\s*[-*•]\s+/, ''))}</li>
        ))}
      </ul>
    )
  }

  // Une coupure entre deux lignes, pas après la dernière : le `<br/>` final
  // ajoutait une ligne vide sous chaque paragraphe, et donc sous chaque
  // réponse.
  return (
    <p key={key}>
      {lines.map((line, i) => (
        <span key={i}>
          {i > 0 && <br />}
          {inline(line)}
        </span>
      ))}
    </p>
  )
}

interface TableModel {
  head: string[]
  rows: string[][]
  align: Align[]
}

/** Le tableau que ces lignes décrivent, ou `null` si ce n'en est pas un. */
function tableOf(lines: string[]): TableModel | null {
  if (lines.length < 2 || !SEPARATOR.test(lines[1] ?? '')) return null
  if (!lines.every((line) => line.includes('|'))) return null

  const head = cellsOf(lines[0] ?? '')
  const align = cellsOf(lines[1] ?? '').map(alignOf)
  const rows = lines.slice(2).map(cellsOf)
  if (head.length === 0) return null
  return { head, align, rows }
}

/**
 * Les cellules d'une ligne.
 *
 * Les barres de bord sont facultatives en GFM, et `\|` est une barre littérale
 * — un libellé comme « Écart | valeur » ne doit pas ouvrir une colonne.
 */
function cellsOf(line: string): string[] {
  return splitCells(
    line.replace(/^\s*\|/, '').replace(/\|\s*$/, ''),
  )
}

/**
 * Coupe sur les barres, en laissant passer `\|`.
 *
 * Une barre échappée est une barre littérale : un libellé comme « Écart | brut »
 * ne doit pas ouvrir une colonne. Le découpage se fait donc caractère par
 * caractère plutôt que par un `split` sur une sentinelle — une sentinelle est un
 * octet invisible dans le source, et le jour où elle apparaît dans la donnée
 * elle coupe au mauvais endroit.
 */
function splitCells(line: string): string[] {
  const cells: string[] = []
  let current = ''
  for (let index = 0; index < line.length; index += 1) {
    const character = line[index]
    if (character === '\\' && line[index + 1] === '|') {
      current += '|'
      index += 1
    } else if (character === '|') {
      cells.push(current.trim())
      current = ''
    } else {
      current += character
    }
  }
  cells.push(current.trim())
  return cells.length === 1 && cells[0] === '' ? [] : cells
}

function alignOf(cell: string): Align {
  const left = cell.startsWith(':')
  const right = cell.endsWith(':')
  if (left && right) return 'center'
  if (right) return 'right'
  if (left) return 'left'
  return null
}

function Table({ head, rows, align }: TableModel) {
  // Une colonne de chiffres s'aligne à droite même quand le modèle ne l'a pas
  // demandé : c'est ainsi que se lisent les quantités et les euros partout
  // ailleurs dans l'application, et un tableau d'écarts aligné à gauche ne se
  // compare pas d'une ligne à l'autre. L'alignement déclaré, lui, l'emporte.
  const columns = head.map((_, index) => {
    if (align[index]) return align[index]
    const values = rows.map((row) => row[index] ?? '').filter((cell) => cell !== '')
    const numeric = values.length > 0 && values.every((cell) => NUMERIC.test(cell))
    return numeric ? 'right' : null
  })
  return (
    <div className="table-wrap md__table">
      <table className="data">
        <thead>
          <tr>
            {head.map((cell, index) => (
              <th key={index} style={{ textAlign: columns[index] ?? 'left' }}>
                {inline(cell)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {head.map((_, column) => (
                <td
                  key={column}
                  className={columns[column] === 'right' ? 'num' : undefined}
                  style={{ textAlign: columns[column] ?? 'left' }}
                >
                  {inline(row[column] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Gras, italique et code — dans cet ordre, le gras avant l'italique. */
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g

export function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return <strong key={index}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={index}>{part.slice(1, -1)}</code>
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      return <em key={index}>{part.slice(1, -1)}</em>
    }
    return part
  })
}
