/**
 * Analyser un bloc collé depuis Excel pour en faire des zones.
 *
 * Une campagne réelle compte quarante à soixante zones, et la liste existe
 * déjà : dans un tableur, dans le compte rendu de la campagne précédente, sur
 * le plan de l'atelier. Les recréer une par une dans une fenêtre modale, c'est
 * quarante allers-retours pour recopier ce qu'on a sous les yeux.
 *
 * **Une seule colonne est obligatoire : le code.** C'est le nom de la zone, et
 * c'est la seule chose qu'une liste d'atelier porte à coup sûr. Tout le reste —
 * le libellé, le nombre de lignes vierges par section — est facultatif et se
 * reconnaît à son en-tête.
 *
 * **L'en-tête décide, pas la position.** Les blocs que les gens collent n'ont
 * pas deux fois le même ordre de colonnes, et exiger un ordre reviendrait à
 * demander de retravailler le tableau avant de le coller — c'est-à-dire à ne
 * rien faire gagner. Sans en-tête reconnaissable, la première colonne est le
 * code et les suivantes sont lues dans l'ordre BDL, WIP, WIP OK, qui est celui
 * de la feuille imprimée.
 *
 * **Une ligne dont on ne tire aucun code est signalée, jamais ignorée.** Une
 * zone perdue entre le tableur et l'application est exactement le genre de
 * silence que cette application existe pour supprimer.
 */

/** Le nombre maximum de lignes vierges qu'une section peut demander. */
export const MAX_BLANK_ROWS = 120

export type ParsedZone = {
  code: string
  label: string
  blankRows: Record<string, number>
}

export type ZonePasteOutcome = {
  zones: ParsedZone[]
  /** Rangs (base 1) des lignes collées dont aucun code n'a pu être lu. */
  rejected: number[]
  /** Vrai quand la première ligne a été reconnue et consommée comme en-tête. */
  headerSkipped: boolean
  /** Ce qui dépasse les bornes, dit ligne par ligne. */
  refusals: string[]
}

/**
 * Le vocabulaire des en-têtes.
 *
 * Trois façons de nommer chaque section, parce que les listes viennent de trois
 * endroits : le vocabulaire de l'application, celui de l'ancien classeur Excel,
 * et l'abréviation qu'emploie l'atelier.
 */
const HEADERS: Record<string, keyof ParsedZone | 'LINE_SIDE' | 'WIP' | 'WIP_OK'> = {
  CODE: 'code',
  ZONE: 'code',
  'CODE ZONE': 'code',
  'NOM': 'code',
  'NOM DE LA ZONE': 'code',
  LIBELLE: 'label',
  'LIBELLÉ': 'label',
  DESCRIPTION: 'label',
  BDL: 'LINE_SIDE',
  'LIGNES BDL': 'LINE_SIDE',
  'BORD DE LIGNE': 'LINE_SIDE',
  'LIGNES BORD DE LIGNE': 'LINE_SIDE',
  LINE_SIDE: 'LINE_SIDE',
  WIP: 'WIP',
  'LIGNES WIP': 'WIP',
  'EN COURS': 'WIP',
  'WIP OK': 'WIP_OK',
  'LIGNES WIP OK': 'WIP_OK',
  'WOP OK': 'WIP_OK',
  'LIGNES WOP OK': 'WIP_OK',
  WIP_OK: 'WIP_OK',
  'MOM OK': 'WIP_OK',
}

/** L'ordre lu quand aucun en-tête n'est reconnu : celui de la feuille. */
const POSITIONAL = ['code', 'LINE_SIDE', 'WIP', 'WIP_OK'] as const

function normalise(cell: string): string {
  return cell.trim().toUpperCase().replace(/\s+/g, ' ')
}

/** Les colonnes d'une ligne, tabulation ou point-virgule. */
function cells(line: string): string[] {
  return line.split(line.includes('\t') ? '\t' : ';').map((c) => c.trim())
}

/**
 * La disposition des colonnes, lue sur la première ligne si elle s'y prête.
 *
 * Une ligne d'en-tête est une ligne dont **au moins deux** cellules sont des
 * mots connus. Une seule ne suffit pas : « BDL » seul est un nom de zone
 * parfaitement plausible, et le consommer comme en-tête ferait disparaître une
 * zone en silence.
 */
function layout(first: string[]): string[] | null {
  const mapped = first.map((cell) => HEADERS[normalise(cell)] ?? null)
  const known = mapped.filter(Boolean).length
  if (known < 2) return null
  return mapped.map((m, index) => m ?? POSITIONAL[index] ?? '')
}

function count(raw: string): number | null {
  const text = raw.trim().replace(/\s/g, '')
  if (!text) return null
  if (!/^\d+$/.test(text)) return Number.NaN
  return Number(text)
}

export function parseZonePaste(text: string): ZonePasteOutcome {
  const rows = text
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => line.trim() !== '')

  const zones: ParsedZone[] = []
  const rejected: number[] = []
  const refusals: string[] = []
  if (rows.length === 0) {
    return { zones, rejected, headerSkipped: false, refusals }
  }

  const header = layout(cells(rows[0]!))
  const columns = header ?? [...POSITIONAL]
  const body = header ? rows.slice(1) : rows

  body.forEach((line, index) => {
    const values = cells(line)
    const zone: ParsedZone = { code: '', label: '', blankRows: {} }
    values.forEach((value, position) => {
      const column = columns[position]
      if (!column || !value) return
      if (column === 'code') zone.code = value
      else if (column === 'label') zone.label = value
      else {
        const number = count(value)
        if (number === null) return
        if (Number.isNaN(number) || number > MAX_BLANK_ROWS) {
          refusals.push(
            `ligne ${index + 1} · ${column} : « ${value} » n’est pas un nombre de ` +
              `lignes entre 0 et ${MAX_BLANK_ROWS}`,
          )
          return
        }
        // Zéro ne se transmet pas : « cette section ne s'imprime pas » et
        // « rien n'est déclaré » sont le même état côté serveur.
        if (number > 0) zone.blankRows[column] = number
      }
    })
    if (!zone.code) {
      rejected.push(index + 1)
      return
    }
    zones.push(zone)
  })

  return { zones, rejected, headerSkipped: header !== null, refusals }
}
