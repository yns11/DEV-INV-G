/**
 * Ce que chaque contrôle du navigateur suppose déjà en place.
 *
 * `jest-dom` ajoute les assertions qui parlent du DOM plutôt que d'objets —
 * « ce bouton est désactivé » plutôt que « cette propriété vaut true ». Un
 * échec y désigne l'élément fautif, ce qui est la moitié du travail quand le
 * contrôle tombe six mois plus tard.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// React laisse son arbre monté entre deux contrôles ; le suivant retrouverait
// alors deux fois le même bouton et `getByRole` échouerait sur l'ambiguïté.
afterEach(() => cleanup())
