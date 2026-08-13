/**
 * La marque eMotors, en tête de la barre latérale.
 *
 * ⚠️ Le symbole ci-dessous est un **redessin** à partir du logo fourni, pas le
 * fichier officiel : celui-ci n'a pas pu être joint au dépôt depuis la session
 * qui a écrit ce composant. Les proportions et les couleurs suivent l'original,
 * mais un logo est un actif de marque et le vrai fichier doit prendre sa place.
 *
 * Pour le remplacer, une seule chose à faire : déposer le SVG (ou le PNG)
 * officiel dans `frontend/src/assets/logo-emotors.svg` et remplacer le corps de
 * `Mark` par `<img src={logo} alt="" />`. Rien d'autre dans l'application ne
 * connaît la forme du logo.
 */

/**
 * Le symbole : un « E » dont les trois barres sont coupées en biais.
 *
 * Dessiné en un seul tracé pour que la couleur se change d'un attribut, et sur
 * une grille de 100 × 100 pour que la taille se règle en pixels côté appelant.
 */
function Mark({ size = 34 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      role="img"
      aria-label="eMotors"
      focusable="false"
    >
      <path
        fill="var(--brand-ink)"
        d={[
          // Bord supérieur, de la pointe droite vers la gauche.
          'M92 4 H44',
          // Flanc gauche arrondi : le « C » qui porte les trois barres.
          'C20 4 6 22 6 50',
          'C6 78 20 96 44 96',
          'H74 L84 68 H46',
          'C36 68 30 62 29 56',
          'H80 L88 32 H29',
          'C31 24 37 18 46 18',
          'H84 Z',
        ].join(' ')}
      />
    </svg>
  )
}

/**
 * La marque complète : le symbole et le mot.
 *
 * Le mot est composé dans la police de l'application plutôt qu'en tracés : une
 * approximation de lettrage se voit, alors qu'un mot bien composé aux bonnes
 * couleurs se lit. Le « e » initial est vert, comme sur l'original.
 */
export function Logo({ size = 34 }: { size?: number }) {
  return (
    <span className="row" style={{ gap: 'var(--space-2)', alignItems: 'center' }}>
      <Mark size={size} />
      <span
        style={{
          fontSize: size * 0.5,
          fontWeight: 800,
          letterSpacing: '-0.02em',
          lineHeight: 1,
          color: 'var(--brand-ink)',
        }}
      >
        <span style={{ color: 'var(--brand-accent)' }}>e</span>Motors
      </span>
    </span>
  )
}
