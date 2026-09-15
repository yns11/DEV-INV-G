/**
 * La bascule « Mes références ».
 *
 * Un seul composant pour les trois écrans qui la portent — stock ERP, écart
 * backflush, écarts. Le même libellé et la même infobulle partout, parce que
 * c'est le même filtre : ce que la personne connectée suit, d'après le
 * portefeuille chargé dans Gestion.
 *
 * Le filtre est résolu **côté serveur**, à partir de l'identité que la
 * plateforme transmet : le navigateur n'envoie qu'un booléen et ne nomme jamais
 * personne, donc personne ne peut demander le portefeuille d'un autre en
 * changeant un paramètre d'URL.
 *
 * Ce n'est pas une habilitation : elle filtre l'affichage et n'interdit rien.
 */

import { Icons } from './ui'

export function MineToggle({
  active,
  onToggle,
}: {
  active: boolean
  onToggle: () => void
}) {
  return (
    <button
      className={`chip${active ? ' chip--active' : ''}`}
      aria-pressed={active}
      title="Ne garder que les références de votre portefeuille. Les portefeuilles se chargent dans Gestion › Portefeuilles."
      onClick={onToggle}
    >
      <Icons.filter size={12} />
      Mes références
    </button>
  )
}

/**
 * Ce qu'on dit quand la bascule est active et que la liste est vide.
 *
 * « Aucun écart » et « aucune référence ne vous est attribuée » sont deux
 * nouvelles très différentes, et la première à la place de la seconde laisse
 * quelqu'un conclure que son périmètre est en ordre alors qu'il ne l'a jamais
 * regardé.
 */
export const MINE_EMPTY =
  'Aucune référence de votre portefeuille ici. Si vous n’en avez aucune, ' +
  'demandez à un gestionnaire de charger le tableau des portefeuilles.'
