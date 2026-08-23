/**
 * Clés de ligne composées de deux colonnes.
 *
 * Une grille identifie ses lignes par une chaîne. Quand l'identité tient à deux
 * colonnes — un assemblage et son composant, un entrepôt et son emplacement —
 * il faut les recoller, puis savoir les redécouper au moment d'agir sur la
 * sélection.
 *
 * Le séparateur n'est pas anodin : une référence peut contenir une espace, un
 * tiret ou une barre oblique, et la normalisation ne les interdit pas. Le seul
 * caractère qu'aucune valeur métier ne porte est le caractère nul.
 *
 * Recoller et redécouper vivent donc ici, ensemble. Ils avaient divergé dans la
 * vue Préparation, qui recollait sur une espace et redécoupait sur le caractère
 * nul : le composant ressortait indéfini, `JSON.stringify` retirait la clé de
 * l'objet, et l'activation groupée des nomenclatures était refusée à l'entrée
 * sans que l'écran puisse dire pourquoi.
 */

const SEPARATOR = '\u0000'

/**
 * Recolle les deux colonnes qui font l'identité d'une ligne.
 *
 * Les valeurs sont converties comme le ferait une interpolation : les grilles
 * décrivent souvent leurs lignes sans type précis, et exiger une chaîne ici
 * ferait ajouter un `String(...)` à chaque appel sans rien garantir de plus.
 */
export function compositeKey(first: unknown, second: unknown): string {
  return `${String(first)}${SEPARATOR}${String(second)}`
}

/**
 * Redécoupe une clé produite par {@link compositeKey}.
 *
 * Un tuple, pas un tableau : une partie manquante ressort en chaîne vide plutôt
 * qu'en `undefined`, parce qu'une valeur absente doit rester visible dans ce
 * qui part au serveur au lieu de disparaître de la requête.
 */
export function splitCompositeKey(key: string): [string, string] {
  const [first = '', second = ''] = key.split(SEPARATOR)
  return [first, second]
}
