/** Les écarts, référence par référence, et ce qui les explique. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, download, downloads } from '../lib/api'
import type { Overview, VarianceRow } from '../lib/types'
import { compositeKey } from '../lib/rowKey'
import { DASH, ITEM_TYPE_LABELS, moneyShort, qty, signClass, signedMoney, signedNum } from '../lib/format'
import { Pareto, VarianceBars } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { ExplainModal } from '../components/ExplainModal'
import { CauseDialog } from '../components/CauseDialog'
import { TransferCard } from '../components/TransferCard'
import { AsyncBoundary, Badge, Button, Card, EmptyState, Icons, Skeleton, useErrorToast, useToast } from '../components/ui'
import { MINE_EMPTY, MineToggle } from '../components/MineToggle'
import { FLAGS_COLUMN } from './varianceFlags'

// --------------------------------------------------------------------------- //
// Variances
// --------------------------------------------------------------------------- //

const DIMENSIONS = [
  { id: 'item_type', label: 'Type d’article' },
  { id: 'category', label: 'Catégorie' },
  { id: 'program', label: 'Programme' },
  { id: 'warehouse', label: 'Entrepôt' },
  { id: 'location', label: 'Emplacement' },
]

/**
 * Ce qui désigne une ligne d'écart, et rien d'autre.
 *
 * Écrite ici, au singulier, parce que deux endroits s'en servent et qu'ils
 * doivent tomber d'accord : la grille l'appelle pour étiqueter les cases à
 * cocher, l'écran pour retrouver les lignes cochées. Elle a porté la **position**
 * de la ligne en plus du triplet, et ces deux appels ne comptaient pas dans la
 * même liste — la grille numérote ce qu'elle affiche, c'est-à-dire après
 * recherche, filtre et tri ; l'écran renumérotait la liste brute. Trier une
 * colonne suffisait alors à ce qu'aucun identifiant ne se retrouve : le lot
 * partait vide et le serveur répondait « requête mal formée », ce qui était vrai
 * et n'expliquait rien.
 *
 * La position n'apportait rien à quoi que ce soit : `build_variances` agrège par
 * `(article, entrepôt, emplacement)`, donc le triplet est unique par
 * construction, aux deux granularités — en vue par article, entrepôt et
 * emplacement valent la chaîne vide pour tout le monde.
 *
 * Le séparateur vient de `compositeKey` et non d'un caractère choisi ici : un
 * emplacement peut porter une espace, un tiret ou une barre, et cette question
 * a déjà été tranchée une fois pour toutes les grilles.
 */
export const varianceRowKey = (row: {
  itemNumber: string
  warehouseId: string
  locationId: string
}) => compositeKey(row.itemNumber, compositeKey(row.warehouseId, row.locationId))

export function VariancesTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const [materialOnly, setMaterialOnly] = useState(false)
  // Filtre de *grille*, comme « au-delà des seuils » juste à côté : les deux
  // cartes du haut et la carte des transferts restent sur la campagne entière,
  // parce qu'elles répondent à des questions de campagne — où se concentre
  // l'écart, quelle part n'est qu'un déplacement — et qu'un Pareto sur trente
  // références ne dit rien.
  const [mine, setMine] = useState(false)
  const [granularity, setGranularity] = useState<'item' | 'item_location'>('item')
  const [dimension, setDimension] = useState('item_type')
  const [explain, setExplain] = useState<string | null>(null)
  const [drill, setDrill] = useState<
    { itemNumber: string; aspect: BreakdownAspect; warehouseId?: string; locationId?: string } | null
  >(null)
  const [exporting, setExporting] = useState<'xlsx' | 'pdf' | null>(null)
  // La cause se décide **ici**, en regardant les chiffres. `null` : fenêtre
  // fermée ; une liste de références : fenêtre ouverte sur elles, qu'il y en ait
  // une ou vingt.
  const [assigning, setAssigning] = useState<VarianceRow[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const showError = useErrorToast()
  const toast = useToast()
  const queryClient = useQueryClient()
  // Même garde que la vue Causes : affecter une cause est une écriture
  // d'analyse, et elle se ferme à la clôture comme le reste de l'analyse.
  const editable = overview.permissions.analysis

  const causes = useQuery({
    queryKey: ['causes', campaignId],
    queryFn: () => api.causes(campaignId),
  })
  const assign = useMutation({
    mutationFn: ({ items, cause, comment }: {
      items: string[]
      cause: string | null
      comment: string
    }) =>
      api.saveVarianceCauses(campaignId, {
        itemNumbers: items,
        causeCode: cause,
        comment,
      }),
    onSuccess: (result) => {
      // Tout, et pas seulement la grille : la carte de répartition des causes
      // et la vue Causes lisent les mêmes lignes, et les laisser périmées
      // ferait douter de ce qu'on vient d'enregistrer.
      void queryClient.invalidateQueries()
      setAssigning(null)
      setSelected(new Set())
      toast.success(`${result.updated} écart(s) mis à jour`)
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  // En vue par emplacement, la ligne cliquée en désigne un : décomposer l'article
  // entier répondrait à une autre question que celle posée.
  const openDrill = (row: VarianceRow, aspect: BreakdownAspect) =>
    setDrill({
      itemNumber: row.itemNumber,
      aspect,
      ...(granularity === 'item_location'
        ? { warehouseId: row.warehouseId, locationId: row.locationId }
        : {}),
    })

  const exportAs = async (format: 'xlsx' | 'pdf') => {
    setExporting(format)
    try {
      await download(
        downloads.variances(campaignId, format, {
          granularity,
          materialOnly: materialOnly || undefined,
          mine: mine || undefined,
        }),
      )
    } catch (error) {
      showError(error, 'Export impossible')
    } finally {
      setExporting(null)
    }
  }

  const variances = useQuery({
    queryKey: ['variances', campaignId, materialOnly, granularity, mine],
    queryFn: () =>
      api.variances(campaignId, {
        limit: 1000,
        materialOnly,
        granularity,
        mine: mine || undefined,
      }),
  })
  const aggregate = useQuery({
    queryKey: ['aggregate', campaignId, dimension],
    queryFn: () => api.aggregate(campaignId, dimension, 40),
  })
  const pareto = useQuery({
    queryKey: ['pareto', campaignId],
    queryFn: () => api.pareto(campaignId, 0.8),
  })
  // The chart needs the whole ranked population: computing a cumulative curve
  // over an already-filtered top-N would place the 80 % marker at a rank that
  // does not exist in reality.
  const ranked = useQuery({
    queryKey: ['aggregate', campaignId, 'item'],
    queryFn: () => api.aggregate(campaignId, 'item', 5000),
  })

  const maxAbs = useMemo(
    () => Math.max(...(variances.data ?? []).map((r) => Math.abs(r.varianceValue)), 1),
    [variances.data],
  )

  const columns: Column<VarianceRow>[] = [
    {
      key: 'itemNumber',
      label: 'Article',
      width: 200,
      render: (row) => (
        <div>
          <div className="mono">{row.itemNumber}</div>
          <div className="subtle truncate" style={{ maxWidth: 190 }}>
            {row.name}
          </div>
        </div>
      ),
      value: (row) => row.itemNumber,
    },
    // La désignation est déjà à l'écran, sous la référence, et c'est délibéré :
    // deux colonnes prendraient la moitié de la largeur pour la même
    // information. Elle n'avait pour autant ni filtre propre, ni prise sur la
    // recherche libre — la barre annonçait « désignation » et ne la trouvait
    // pas. Déclarée en filtre seul, elle gagne les deux sans prendre de place.
    {
      key: 'name',
      label: 'Désignation',
      filterOnly: true,
      filter: 'text',
      value: (row) => row.name,
    },
    {
      key: 'manufacturedProduct',
      label: 'Produit fabriqué',
      filterOnly: true,
      filter: 'choice',
      // Un produit à la fois : la question qu'on pose ici est « montre-moi cet
      // ensemble », pour y chercher deux écarts qui se compensent. Trois
      // produits cochés rendraient un mélange dont ce rapprochement ne sort
      // pas. Une trentaine de valeurs, donc une liste et non un « contient ».
      choiceSingle: true,
      value: (row) => row.manufacturedProduct,
    },
    ...(granularity === 'item_location'
      ? [
          { key: 'warehouseId', label: 'Entrepôt', width: 110 } as Column<VarianceRow>,
          { key: 'locationId', label: 'Emplacement', width: 140 } as Column<VarianceRow>,
        ]
      : []),
    {
      key: 'itemType',
      label: 'Type',
      width: 120,
      render: (row) => <Badge tone="neutral">{ITEM_TYPE_LABELS[row.itemType]}</Badge>,
      value: (row) => row.itemType,
    },
    // Every cell of this row that carries both figures uses the same
    // arrangement: quantity on the first line, amount on the second. Mixing the
    // two orders — a quantity heading one column and an amount heading the next
    // — puts different units at the same height, and the eye reading across a
    // row cannot tell which is which without checking the header every time.
    {
      key: 'bookQty',
      label: 'Stock ERP',
      numeric: true,
      width: 130,
      render: (row) => (
        <DrillCell
          disabled={row.bookQty === 0}
          onOpen={() => openDrill(row, 'book')}
        >
          <QtyOverValue qty={qty(row.bookQty)} value={moneyShort(row.bookValue)} />
        </DrillCell>
      ),
      value: (row) => row.bookQty,
    },
    {
      key: 'countedQty',
      label: 'Compté',
      numeric: true,
      width: 130,
      render: (row) => (
        <DrillCell
          disabled={row.countedQty === 0}
          onOpen={() => openDrill(row, 'counted')}
        >
          <QtyOverValue
            qty={qty(row.countedQty)}
            value={moneyShort(row.countedQty * row.unitCost)}
          />
        </DrillCell>
      ),
      value: (row) => row.countedQty,
    },
    {
      key: 'physicalQty',
      label: 'Physique',
      numeric: true,
      width: 130,
      render: (row) => (
        <DrillCell
          disabled={row.physicalQty === 0}
          onOpen={() => openDrill(row, 'physical')}
        >
          <QtyOverValue
            qty={qty(row.physicalQty)}
            value={moneyShort(row.physicalValue)}
          />
        </DrillCell>
      ),
      value: (row) => row.physicalQty,
    },
    {
      key: 'varianceValue',
      label: 'Écart',
      numeric: true,
      width: 170,
      render: (row) => (
        <div>
          <DrillCell
            disabled={row.varianceQty === 0}
            onOpen={() => openDrill(row, 'variance')}
          >
            <QtyOverValue
              qty={signedNum(row.varianceQty)}
              value={signedMoney(row.varianceValue)}
              tone={signClass(row.varianceValue)}
            />
          </DrillCell>
          <CellBarInline value={row.varianceValue} max={maxAbs} />
        </div>
      ),
      value: (row) => row.varianceValue,
    },
    // Ce que le comptage seul montrait. Sans ajustement il répète l'écart à
    // l'identique : une colonne de doublons est une colonne qu'on apprend à
    // sauter, donc une colonne à ne pas afficher.
    ...(variances.data?.some((row) => row.adjustedQty !== 0)
      ? [
          {
            key: 'countedVarianceValue',
            label: 'Avant ajust.',
            numeric: true,
            width: 140,
            render: (row: VarianceRow) => (
              <DrillCell
                disabled={row.countedVarianceQty === 0}
                onOpen={() => openDrill(row, 'counted')}
              >
                <QtyOverValue
                  qty={signedNum(row.countedVarianceQty)}
                  value={signedMoney(row.countedVarianceValue)}
                  tone={signClass(row.countedVarianceValue)}
                />
              </DrillCell>
            ),
            value: (row: VarianceRow) => row.countedVarianceValue,
          } as Column<VarianceRow>,
        ]
      : []),
    // Le backflush n'apparaît que là où il a été mesuré. Une colonne pleine de
    // tirets sur une campagne qui ne l'a pas chargé serait une colonne à
    // ignorer, c'est-à-dire une colonne à retirer.
    ...(variances.data?.some((row) => row.backflushMeasured)
      ? [
          {
            key: 'unexplainedValue',
            label: 'Inexpliqué',
            numeric: true,
            width: 150,
            render: (row: VarianceRow) =>
              row.backflushMeasured ? (
                <DrillCell
                  disabled={row.backflushShareQty === 0}
                  onOpen={() => openDrill(row, 'variance')}
                >
                  <QtyOverValue
                    qty={signedNum(row.unexplainedQty)}
                    value={signedMoney(row.unexplainedValue)}
                    tone={signClass(row.unexplainedValue)}
                  />
                </DrillCell>
              ) : (
                <span className="subtle">{DASH}</span>
              ),
            value: (row: VarianceRow) => row.unexplainedValue,
          } as Column<VarianceRow>,
          {
            key: 'backflushShareQty',
            label: 'Part backflush',
            numeric: true,
            width: 140,
            render: (row: VarianceRow) =>
              row.backflushMeasured ? (
                <QtyOverValue
                  qty={signedNum(row.backflushShareQty)}
                  value={signedMoney(row.backflushShareValue)}
                />
              ) : (
                <span className="subtle">{DASH}</span>
              ),
            value: (row: VarianceRow) => row.backflushShareQty,
          } as Column<VarianceRow>,
        ]
      : []),
    FLAGS_COLUMN,
    {
      key: 'explain',
      label: '',
      width: 96,
      sortable: false,
      render: (row) => (
        <span className="row" style={{ gap: 2 }}>
          <Button
            variant="ghost"
            size="sm"
            icon={<Icons.sparkles size={13} />}
            onClick={() => setExplain(row.itemNumber)}
            title="Expliquer cet écart"
            aria-label="Expliquer"
          />
          {/* Voisin du précédent, et c'est voulu : l'un propose une lecture,
              l'autre enregistre la vôtre. Les deux se font au même moment, sur
              la même ligne, avec les mêmes chiffres sous les yeux. */}
          <Button
            variant={row.causeCode ? 'primary' : 'ghost'}
            size="sm"
            icon={<Icons.clipboard size={13} />}
            // Même garde que le lot. Sans elle, le bouton invitait à remplir
            // une fenêtre dont l'enregistrement était refusé par le serveur :
            // une porte peinte sur un mur.
            disabled={!editable}
            onClick={() => setAssigning([row])}
            title={
              row.causeCode
                ? `Cause : ${row.causeCode}${row.comment ? ` — ${row.comment}` : ''}`
                : 'Affecter une cause'
            }
            aria-label="Affecter une cause"
          />
        </span>
      ),
    },
  ]

  return (
    <div className="stack">
      <div className="grid grid--2">
        <AsyncBoundary query={aggregate} skeleton={<Card title="Répartition"><Skeleton height={260} /></Card>}>
          {(rows) => (
            <Card
              title="Écart en valeur par dimension"
              message={
                rows[0]
                  ? `${rows[0].key} porte le plus gros écart absolu (${moneyShort(rows[0].absVarianceValue)}).`
                  : undefined
              }
              actions={
                <div className="segmented">
                  {DIMENSIONS.map((option) => (
                    <button
                      key={option.id}
                      className={`segmented__item${dimension === option.id ? ' segmented__item--active' : ''}`}
                      onClick={() => setDimension(option.id)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              }
            >
              <VarianceBars
                data={rows.slice(0, 12).map((row) => ({ label: row.key, value: row.varianceValue }))}
                format={moneyShort}
              />
            </Card>
          )}
        </AsyncBoundary>

        <AsyncBoundary query={ranked} skeleton={<Card title="Pareto"><Skeleton height={260} /></Card>}>
          {(rows) => (
            <Card
              title="Concentration des écarts"
              message={
                pareto.data
                  ? `${pareto.data.length} article(s) sur ${rows.length} portent 80 % de l’écart absolu — c’est la liste sur laquelle concentrer l’analyse.`
                  : undefined
              }
            >
              <Pareto
                data={rows.map((row) => ({ label: row.key, value: row.absVarianceValue }))}
                format={moneyShort}
                coverage={0.8}
              />
            </Card>
          )}
        </AsyncBoundary>
      </div>

      <TransferCard campaignId={campaignId} onDrillDown={() => setGranularity('item_location')} />

      <Card
        title={
          granularity === 'item'
            ? 'Écarts par référence'
            : 'Écarts par référence et emplacement'
        }
        message={
          granularity === 'item'
            ? 'Emplacements agrégés : ce que le site a réellement perdu ou gagné.'
            : 'Où aller recompter. Un article déplacé apparaît deux fois — en moins ici, en plus là.'
        }
        actions={
          <div className="row-wrap">
            <div className="segmented">
              <button
                className={`segmented__item${granularity === 'item' ? ' segmented__item--active' : ''}`}
                onClick={() => setGranularity('item')}
                title="La perte ou le gain réel du site"
              >
                Par référence
              </button>
              <button
                className={`segmented__item${granularity === 'item_location' ? ' segmented__item--active' : ''}`}
                onClick={() => setGranularity('item_location')}
                title="Où aller recompter"
              >
                Détail par emplacement
              </button>
            </div>
            <button
              className={`chip${materialOnly ? ' chip--active' : ''}`}
              onClick={() => setMaterialOnly((value) => !value)}
            >
              <Icons.filter size={12} />
              Au-delà des seuils uniquement
            </button>
            <MineToggle active={mine} onToggle={() => setMine((value) => !value)} />
            {/* Les deux boutons emportent la vue telle qu'elle est réglée —
                granularité et filtre compris. Un export qui ignorerait les
                réglages produirait un fichier qui ne ressemble pas à l'écran
                depuis lequel on l'a demandé. */}
            <Button
              size="sm"
              icon={<Icons.download size={13} />}
              disabled={exporting !== null}
              onClick={() => void exportAs('xlsx')}
              title="Quantités, valeurs et écarts en colonnes séparées, stock ERP et stock compté"
            >
              {exporting === 'xlsx' ? 'Export…' : 'Excel'}
            </Button>
            <Button
              size="sm"
              icon={<Icons.printer size={13} />}
              disabled={exporting !== null}
              onClick={() => void exportAs('pdf')}
              title="Le tableau imprimable, plus gros écarts en tête. Les lignes sans écart de quantité n’y figurent pas ; l’export Excel les contient."
            >
              {exporting === 'pdf' ? 'Export…' : 'PDF'}
            </Button>
          </div>
        }
        flush
      >
        <AsyncBoundary
          query={variances}
          isEmpty={(rows) => rows.length === 0}
          empty={
            mine ? (
              <EmptyState title="Rien dans votre portefeuille" icon={<Icons.filter size={20} />}>
                {MINE_EMPTY}
              </EmptyState>
            ) : (
              <EmptyState title="Aucun écart" icon={<Icons.check size={20} />}>
                {materialOnly
                  ? 'Aucun écart ne dépasse les seuils de matérialité configurés.'
                  : 'Le stock compté correspond au stock ERP.'}
              </EmptyState>
            )
          }
        >
          {(rows) => {
            // Les lignes réellement visées, résolues **avant** d'annoncer quoi
            // que ce soit. Une coche peut désigner une ligne que la liste ne
            // porte plus — on change de granularité, on coupe au seuil, le
            // portefeuille se referme — et annoncer « 12 lignes » pour en
            // envoyer 9 est un mensonge que l'écran est seul à pouvoir éviter.
            // Le bouton compte donc ce qu'il enverra, et disparaît quand il
            // n'enverrait rien.
            const picked = rows.filter((row) => selected.has(varianceRowKey(row)))
            return (
            <DataGrid
              columns={columns}
              rows={rows}
              exportTitle="Écarts"
              campaignId={campaignId}
              getRowId={varianceRowKey}
              selectable={editable}
              selected={selected}
              onSelectedChange={setSelected}
              toolbar={
                // L'invitation compte autant que le bouton : sans elle, une
                // colonne de cases à cocher ne dit pas ce qu'on peut en faire,
                // et le bouton n'apparaît qu'une fois quelque chose de coché.
                picked.length > 0 ? (
                  <Button
                    size="sm"
                    variant="primary"
                    icon={<Icons.clipboard size={13} />}
                    onClick={() => setAssigning(picked)}
                  >
                    Affecter une cause aux {picked.length} ligne(s)
                  </Button>
                ) : editable ? (
                  <span className="subtle">
                    Cochez des lignes pour leur affecter une cause commune.
                  </span>
                ) : null
              }
              searchPlaceholder="Filtrer par article, désignation, programme…"
              maxHeight={640}
              // Ascendant : les manques d'abord. L'écart le plus négatif est
              // le stock que l'usine a perdu, et c'est celui qu'on va chercher
              // en premier — un tri descendant mettait les excédents en tête.
              initialSort={{ key: 'varianceValue', direction: 'asc' }}
              footer={
                <span className="subtle">
                  Périmètre : {overview.campaign.code}
                  {mine && ' · votre portefeuille uniquement'} · stock ERP gelé le{' '}
                  {new Date(overview.campaign.book_stock_frozen_at!).toLocaleDateString('fr-FR')}
                </span>
              }
            />
            )
          }}
        </AsyncBoundary>
      </Card>

      {explain && (
        <ExplainModal campaignId={campaignId} itemNumber={explain} onClose={() => setExplain(null)} />
      )}
      {assigning && (
        <CauseDialog
          count={assigning.length}
          // Pré-rempli sur une ligne, à blanc sur un lot : vingt lignes n'ont
          // pas une valeur à montrer. C'est ce qui décide aussi du sort d'un
          // commentaire vide — voir `CauseDialog`.
          initialCause={assigning.length === 1 ? assigning[0]!.causeCode : null}
          initialComment={assigning.length === 1 ? assigning[0]!.comment : ''}
          causes={causes.data ?? []}
          title={
            assigning.length === 1
              ? `Cause de l’écart — ${assigning[0]!.itemNumber}`
              : `Cause pour ${assigning.length} écarts`
          }
          pending={assign.isPending}
          onSubmit={(cause, comment) =>
            assign.mutate({
              items: assigning.map((row) => row.itemNumber),
              cause,
              comment,
            })
          }
          onClose={() => setAssigning(null)}
        />
      )}
      {drill && (
        <BreakdownModal
          campaignId={campaignId}
          itemNumber={drill.itemNumber}
          aspect={drill.aspect}
          warehouseId={drill.warehouseId}
          locationId={drill.locationId}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  )
}

/**
 * A quantity over an amount, in that order, everywhere.
 *
 * One component rather than the same two lines written per column: the point is
 * that the arrangement cannot drift, and a shared component is the only version
 * of "consistent" that survives the next column being added.
 */
export function QtyOverValue({
  qty: quantity,
  value,
  tone,
}: {
  qty: string
  value: string
  tone?: string
}) {
  return (
    <div className="num">
      <strong className={tone}>{quantity}</strong>
      <div className="subtle">{value}</div>
    </div>
  )
}

export function CellBarInline({ value, max }: { value: number; max: number }) {
  const ratio = Math.min(Math.abs(value) / max, 1)
  return (
    <span className="cell-bar" aria-hidden="true">
      <span
        className="cell-bar__fill"
        style={{
          width: `${ratio * 100}%`,
          background: value >= 0 ? 'var(--variance-positive)' : 'var(--variance-negative)',
        }}
      />
    </span>
  )
}
