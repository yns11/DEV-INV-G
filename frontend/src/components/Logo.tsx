/**
 * La marque eMotors, en tête de la barre latérale.
 *
 * Deux fichiers, parce que la marque en a deux : le tracé bleu nuit ne se voit
 * pas sur un fond sombre, et la version claire est fournie sur son propre fond.
 * Le choix se fait en CSS et non en JavaScript — le thème peut venir d'un
 * réglage explicite *ou* du système, et une seule règle couvre les deux sans
 * qu'un composant ait à s'abonner à quoi que ce soit.
 *
 * Les deux images sont dans le document, l'une masquée. C'est délibéré : les
 * échanger au montage ferait clignoter le logo à chaque changement de thème, et
 * elles pèsent le prix d'un chargement, pas d'un rendu.
 */

import dark from '../assets/logo-sombre.png'
import light from '../assets/logo.svg'

export function Logo({ height = 40 }: { height?: number }) {
  return (
    <span className="brand" aria-label="eMotors" role="img">
      <img className="brand__logo brand__logo--light" src={light} alt="" height={height} />
      <img className="brand__logo brand__logo--dark" src={dark} alt="" height={height} />
    </span>
  )
}
