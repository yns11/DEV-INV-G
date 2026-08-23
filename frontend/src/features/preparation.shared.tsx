/** Ce que deux onglets de la préparation partagent, et rien d'autre. */



/**
 * Combien de lignes de stock la pilule « Top » retient.
 *
 * Vingt-cinq parce que c'est ce qui tient sur un écran sans faire défiler : la
 * liste sert à décider quoi recompter en priorité, et une liste qu'on fait
 * défiler n'est plus une priorité.
 */
export const TOP_STOCK_LINES = 25

/**
 * Préfixe des lignes qui n'existent que dans le navigateur.
 *
 * Une ligne neuve n'a pas encore d'identifiant : lui donner son indice de
 * tableau en guise d'identité la rendait indiscernable d'une ligne enregistrée,
 * et « 11 » partait vers le serveur comme s'il s'agissait d'un UUID. Le préfixe
 * rend la distinction visible partout où elle compte.
 */
export const DRAFT_PREFIX = 'brouillon:'

export const rowKey = (row: Record<string, unknown>, index: number) =>
  row.id ? String(row.id) : `${DRAFT_PREFIX}${index}`

/**
 * « Articles stockés / comptés ».
 *
 * Le référentiel porte tout le catalogue — des dizaines de milliers de
 * références, dont la plupart n'ont pas été détenues depuis des années. Ce qui
 * est réellement compté tient dans un sous-ensemble beaucoup plus court, et
 * c'est le seul sur lequel corriger une désignation ou un `qty_par` vaut le
 * temps qu'on y passe. Le tri se fait côté serveur : le total affiché reste
 * donc celui de ce qu'on regarde.
 */
export function StockedFilter({
  value,
  onChange,
}: {
  value: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <button
      className={`chip${value ? ' chip--active' : ''}`}
      title="Ne garder que les références présentes dans les feuilles B06VRAC GENERIQUE ou dans les journaux de comptage."
      onClick={() => onChange(!value)}
    >
      Articles stockés / comptés
    </button>
  )
}
