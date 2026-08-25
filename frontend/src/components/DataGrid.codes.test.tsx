/**
 * Un code interne ne sort ni de la liste déroulante, ni du fichier Excel.
 *
 * Le défaut, capture à l'appui : `LINE_SIDE` apparaissait dans la liste
 * déroulante de la colonne « Section » d'une grille d'import, dans la même
 * colonne des feuilles préparées, et dans l'export envoyé au gestionnaire. Le
 * libellé — « Bord de ligne » — était pourtant déjà déclaré ; il n'était branché
 * que sur le filtre latéral, c'est-à-dire à l'endroit où personne ne regardait.
 *
 * Trois fils, donc, et ce sont les trois qui sont tenus ici : ce que le contrat
 * transmet, ce que la cellule éditée propose, et ce que l'export écrit.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DataGrid,
  columnsFromContract,
  exportValue,
  type Column,
} from './DataGrid'
import { ToastProvider } from './ui'
import type { GridContract } from '../lib/types'

// L'export part par le réseau ; ce qui est vérifié ici est ce que la grille met
// dans le corps de la requête, pas le fichier que le serveur en fait.
const download = vi.fn(() => Promise.resolve())
vi.mock('../lib/api', () => ({
  download: (...args: unknown[]) => download(...args),
  downloads: { table: (id: string) => `/campaigns/${id}/reports/table.xlsx` },
}))

interface Line {
  itemNumber: string
  section: string
  qty: number
}

const SECTION: Column<Line> = {
  key: 'section',
  label: 'Section',
  editable: true,
  choices: ['LINE_SIDE', 'WIP', 'WIP_OK'],
  choiceLabel: (value) =>
    ({ LINE_SIDE: 'Bord de ligne', WIP: 'WIP (à éclater)', WIP_OK: 'WIP assemblé' })[
      value
    ] ?? value,
}

const COLUMNS: Column<Line>[] = [
  { key: 'itemNumber', label: 'Article' },
  SECTION,
  { key: 'qty', label: 'Quantité', numeric: true },
]

const ROWS: Line[] = [
  { itemNumber: 'P-00324093', section: 'LINE_SIDE', qty: 12 },
  { itemNumber: 'MASS-00040707', section: 'WIP_OK', qty: 3 },
]

function grid(props: Partial<Parameters<typeof DataGrid<Line>>[0]> = {}) {
  return render(
    <ToastProvider>
      <DataGrid<Line>
        columns={COLUMNS}
        rows={ROWS}
        getRowId={(row) => row.itemNumber}
        {...props}
      />
    </ToastProvider>,
  )
}

describe('la cellule éditée', () => {
  it('propose les libellés, pas les codes', () => {
    grid({ editable: true, onRowsChange: vi.fn() })
    const options = screen
      .getAllByRole('option')
      .map((o) => o.textContent)
      .filter(Boolean)
    expect(options).toContain('Bord de ligne')
    expect(options).toContain('WIP assemblé')
    expect(options).not.toContain('LINE_SIDE')
    expect(options).not.toContain('WIP_OK')
  })

  it('choisit toujours le code', () => {
    // Le libellé s'affiche, la valeur envoyée reste `LINE_SIDE` : traduire la
    // liste ne doit pas changer ce que la ligne vaut.
    grid({ editable: true, onRowsChange: vi.fn() })
    const chosen = screen
      .getAllByRole('option')
      .find((o) => o.textContent === 'Bord de ligne') as HTMLOptionElement
    expect(chosen.value).toBe('LINE_SIDE')
  })

  it('laisse une colonne sans vocabulaire tranquille', () => {
    const plain: Column<Line> = { ...SECTION, choiceLabel: undefined }
    grid({ editable: true, onRowsChange: vi.fn(), columns: [plain] })
    expect(
      screen.getAllByRole('option').map((o) => o.textContent),
    ).toContain('LINE_SIDE')
  })
})

describe('ce qui part dans le fichier', () => {
  it('écrit le libellé sur une colonne codée', () => {
    expect(exportValue(ROWS[0]!, SECTION)).toBe('Bord de ligne')
    expect(exportValue(ROWS[1]!, SECTION)).toBe('WIP assemblé')
  })

  it('laisse les nombres intacts', () => {
    // Un tableur trie et somme des nombres : les traduire n'aurait aucun sens.
    const qty = COLUMNS[2]!
    expect(exportValue(ROWS[0]!, qty)).toBe(12)
  })

  it('laisse le texte libre intact', () => {
    expect(exportValue(ROWS[0]!, COLUMNS[0]!)).toBe('P-00324093')
  })

  it('ne fabrique pas un libellé pour une cellule vide', () => {
    // Une colonne peut nommer l'absence — le filtre latéral la coche sous
    // « (vide) ». Ce nom-là est fait pour être coché, pas pour remplir une
    // cellule d'un fichier : une case vide reste vide.
    const named: Column<Line> = {
      ...SECTION,
      choiceLabel: (value) => (value === '' ? 'Aucune section' : value),
    }
    const empty: Line = { itemNumber: 'X', section: '', qty: 0 }
    expect(exportValue(empty, named)).toBe('')
  })

  it('rend tel quel un code que la colonne ne connaît pas', () => {
    const odd: Line = { itemNumber: 'X', section: 'MOM_OK', qty: 0 }
    expect(exportValue(odd, SECTION)).toBe('MOM_OK')
  })
})

describe('le bouton Excel', () => {
  beforeEach(() => download.mockClear())

  /**
   * Le fil complet, du clic au corps de la requête.
   *
   * Les contrôles ci-dessus portent sur `exportValue` ; c'est l'export lui-même
   * qui l'appelle, et c'est ce raccordement-là qui manquait — la fonction
   * existait, le bouton continuait d'écrire le code.
   */
  it('envoie les libellés, pas les codes', async () => {
    render(
      <ToastProvider>
        <DataGrid<Line>
          columns={COLUMNS}
          rows={ROWS}
          getRowId={(row) => row.itemNumber}
          exportTitle="Feuilles"
          campaignId="camp-1"
        />
      </ToastProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: /Excel/ }))
    expect(download).toHaveBeenCalledTimes(1)
    const body = download.mock.calls[0]![1] as {
      rows: Array<Record<string, unknown>>
    }
    expect(body.rows.map((r) => r.section)).toEqual([
      'Bord de ligne',
      'WIP assemblé',
    ])
  })

  it('envoie les quantités comme des nombres', async () => {
    render(
      <ToastProvider>
        <DataGrid<Line>
          columns={COLUMNS}
          rows={ROWS}
          getRowId={(row) => row.itemNumber}
          exportTitle="Feuilles"
          campaignId="camp-1"
        />
      </ToastProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: /Excel/ }))
    const body = download.mock.calls[0]![1] as {
      rows: Array<Record<string, unknown>>
    }
    expect(body.rows.map((r) => r.qty)).toEqual([12, 3])
  })
})

describe('une grille bâtie sur un contrat', () => {
  const contract = {
    key: 'count_sheets',
    title: 'Feuilles',
    description: '',
    hint: '',
    naturalKey: [],
    examples: [],
    fields: [
      {
        name: 'sheet_code',
        label: 'Feuille',
        type: 'string',
        required: true,
        aliases: [],
        choices: [],
        choiceLabels: {},
        default: null,
        help: '',
        width: 200,
      },
      {
        name: 'section',
        label: 'Section',
        type: 'enum',
        required: false,
        aliases: [],
        choices: ['LINE_SIDE', 'WIP', 'WIP_OK'],
        choiceLabels: {
          LINE_SIDE: 'Bord de ligne',
          WIP: 'WIP (à éclater)',
          WIP_OK: 'WIP assemblé',
        },
        default: 'LINE_SIDE',
        help: '',
        width: 150,
      },
    ],
  } as unknown as GridContract

  it('reprend le vocabulaire que le contrat livre', () => {
    // Sans cette reprise, la grille d'import — celle des captures — offrait de
    // choisir « LINE_SIDE » dans sa liste déroulante.
    const section = columnsFromContract(contract).find((c) => c.key === 'section')!
    expect(section.choiceLabel?.('LINE_SIDE')).toBe('Bord de ligne')
    expect(section.choices).toEqual(['LINE_SIDE', 'WIP', 'WIP_OK'])
  })

  it('n’en invente pas sur une colonne libre', () => {
    const code = columnsFromContract(contract).find((c) => c.key === 'sheet_code')!
    expect(code.choiceLabel).toBeUndefined()
  })
})
