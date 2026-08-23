/**
 * Ce qui s'affiche, et surtout ce qui ne doit jamais s'afficher.
 *
 * Deux règles gouvernent ce module, et ce sont deux règles d'audit — pas de
 * présentation :
 *
 * **Un manque n'est pas un zéro.** « — » et « 0 » répondent à deux questions
 * différentes : « on n'a pas le chiffre » et « le chiffre vaut zéro ». Le
 * classeur qu'on remplace confondait les deux, et c'est ainsi qu'un écart non
 * compté ressortait comme un écart nul.
 *
 * **Une quantité ne s'abrège jamais.** Un montant est un ordre de grandeur et
 * « 847 k€ » se lit comme tel. Une quantité est un compte d'objets physiques :
 * 3 420 vis n'est pas « 3 k vis », et l'arrondir à l'écran contredit en
 * silence la feuille qu'un compteur a remplie et la ligne dont l'ERP sera
 * ajusté. Aucun abréviateur de quantité n'existe, et ces contrôles sont ce qui
 * fait qu'on ne peut pas en ajouter un par mégarde.
 */

import { describe, expect, it } from 'vitest'

import {
  DASH, date, dateTime, money, moneyShort, num, percent, qty,
  relativeTime, signClass, signedMoney, signedNum,
} from './format'

/** L'espace fine insécable, séparateur français des milliers. */
const NBSP = ' '

/** Le texte, espaces insécables ramenées à des espaces ordinaires. */
const plain = (text: string) => text.replace(/[  ]/g, ' ')

describe('un manque n’est pas un zéro', () => {
  const formatters: [string, (v: number | null | undefined) => string][] = [
    ['num', num], ['qty', qty], ['money', money], ['moneyShort', moneyShort],
    ['percent', percent], ['signedMoney', signedMoney], ['signedNum', signedNum],
  ]

  it.each(formatters)('%s rend un tiret sur null', (_name, format) => {
    expect(format(null)).toBe(DASH)
  })

  it.each(formatters)('%s rend un tiret sur undefined', (_name, format) => {
    expect(format(undefined)).toBe(DASH)
  })

  it.each(formatters)('%s rend un tiret sur NaN', (_name, format) => {
    // Une division par zéro en amont ne doit pas s'afficher comme « NaN ».
    expect(format(Number.NaN)).toBe(DASH)
  })

  it.each(formatters)('%s ne rend pas un tiret sur zéro', (_name, format) => {
    expect(format(0)).not.toBe(DASH)
  })

  it('zéro s’écrit zéro', () => {
    expect(plain(num(0))).toBe('0')
    expect(plain(money(0))).toBe('0,00 €')
  })

  it('l’infini est traité comme un manque', () => {
    expect(num(Number.POSITIVE_INFINITY)).toBe(DASH)
  })
})

describe('une quantité ne s’abrège jamais', () => {
  it('trois mille quatre cent vingt vis s’écrivent en entier', () => {
    expect(plain(qty(3420))).toBe('3 420')
  })

  it('un million de pièces aussi', () => {
    // C'est précisément là qu'un abréviateur serait tentant.
    expect(plain(qty(1_000_000))).toBe('1 000 000')
  })

  it('une quantité signée reste entière', () => {
    expect(plain(signedNum(12_480))).toBe('+12 480')
  })

  it('aucun raccourci de quantité n’est exporté', async () => {
    // Le module n'en offre pas, délibérément. En ajouter un ferait échouer
    // ceci avant qu'un écran ne s'en serve.
    const module = await import('./format')
    const shorteners = Object.keys(module).filter(
      (name) => /short/i.test(name) && !/money/i.test(name),
    )
    expect(shorteners).toEqual([])
  })

  it('les décimales d’une quantité sont gardées', () => {
    // Kilogrammes, mètres, litres : arrondir change la quantité.
    expect(plain(qty(12.345))).toBe('12,345')
  })

  it('une unité est collée à la quantité', () => {
    expect(plain(qty(40, 'PCE'))).toBe('40 PCE')
  })

  it('un entier ne gagne pas de décimales', () => {
    expect(plain(qty(40))).toBe('40')
  })
})

describe('un montant abrégé porte toujours son suffixe', () => {
  it('les milliers', () => {
    // Sans suffixe, 847 k€ se lirait 847 € — l'erreur qui a transformé un
    // écart de 22 M€ en « 22 » sur une diapositive.
    expect(plain(moneyShort(847_000))).toBe('847 k€')
  })

  it('les millions', () => {
    expect(plain(moneyShort(1_200_000))).toBe('1,2 M€')
  })

  it('les milliards', () => {
    expect(plain(moneyShort(2_500_000_000))).toBe('2,5 Md€')
  })

  it('en dessous du millier, l’euro reste', () => {
    expect(plain(moneyShort(312))).toBe('312 €')
  })

  it('le signe négatif est conservé', () => {
    expect(plain(moneyShort(-847_000))).toBe('-847 k€')
  })

  it('l’abréviation choisit le palier sur la valeur absolue', () => {
    expect(plain(moneyShort(-1_200_000))).toBe('-1,2 M€')
  })

  it('un montant complet n’est jamais abrégé', () => {
    expect(plain(money(1_234_567.89))).toBe('1 234 567,89 €')
  })
})

describe('un écart montre sa direction', () => {
  it('un gain porte un plus explicite', () => {
    expect(plain(signedMoney(1_000))).toBe('+1 k€')
  })

  it('une perte porte son moins', () => {
    expect(plain(signedMoney(-1_000))).toBe('-1 k€')
  })

  it('zéro ne porte pas de signe', () => {
    expect(plain(signedMoney(0)).startsWith('+')).toBe(false)
  })

  it('la classe dit la direction, pas un jugement', () => {
    // Un écart positif n'est pas « bon » : c'est du stock trouvé en plus, ce
    // qui peut être une erreur de comptage autant qu'une bonne nouvelle.
    expect(signClass(5)).toBe('pos')
    expect(signClass(-5)).toBe('neg')
  })

  it('zéro et l’absence sont neutres tous les deux', () => {
    expect(signClass(0)).toBe('neutral')
    expect(signClass(null)).toBe('neutral')
  })
})

describe('un pourcentage', () => {
  it('se lit sur une proportion, pas sur un nombre déjà multiplié', () => {
    expect(plain(percent(0.427))).toBe('42,7 %')
  })

  it('accepte une seconde décimale quand on la demande', () => {
    expect(plain(percent(0.4275, 2))).toBe('42,75 %')
  })
})

describe('les dates', () => {
  it('s’écrivent à la française', () => {
    expect(date('2026-06-13')).toBe('13/06/2026')
  })

  it('une date invalide ne s’affiche pas comme telle', () => {
    // « Invalid Date » à l'écran est pire qu'un tiret : il n'apprend rien et
    // ressemble à une panne.
    expect(date('pas une date')).toBe(DASH)
    expect(dateTime('pas une date')).toBe(DASH)
    expect(relativeTime('pas une date')).toBe(DASH)
  })

  it('une date absente rend un tiret', () => {
    expect(date(null)).toBe(DASH)
    expect(date('')).toBe(DASH)
  })

  it('l’heure accompagne la date quand on la demande', () => {
    expect(dateTime('2026-06-13T08:42:00Z')).toMatch(/^13\/06\/2026/)
  })
})

describe('le temps écoulé, pour le journal d’audit', () => {
  const agoBy = (ms: number) => new Date(Date.now() - ms).toISOString()

  it('quelques secondes se disent « à l’instant »', () => {
    expect(relativeTime(agoBy(5_000))).toBe("à l'instant")
  })

  it('les minutes', () => {
    expect(relativeTime(agoBy(3 * 60_000))).toBe('il y a 3 min')
  })

  it('les heures', () => {
    expect(relativeTime(agoBy(2 * 3_600_000))).toBe('il y a 2 h')
  })

  it('les jours', () => {
    expect(relativeTime(agoBy(4 * 86_400_000))).toBe('il y a 4 j')
  })

  it('au-delà d’un mois, la date absolue est plus utile', () => {
    // « il y a 47 j » oblige à compter ; une date se lit.
    expect(relativeTime(agoBy(60 * 86_400_000))).toMatch(/^\d{2}\/\d{2}\/\d{4}$/)
  })
})

describe('rien ne se coupe en fin de ligne', () => {
  it('les milliers d’un nombre restent ensemble', () => {
    // Coupé, « 12 480 » devient « 12 » sur une ligne et « 480 » sur la
    // suivante — deux nombres là où il y en avait un.
    expect(num(12_480)).toContain(NBSP)
  })

  it('l’unité reste collée à sa quantité', () => {
    // Comparaison exacte, sans normaliser : c'est le caractère lui-même qui
    // est en cause, et le normaliser reviendrait à ne rien contrôler.
    expect(qty(40, 'PCE')).toBe(`40${NBSP}PCE`)
  })

  it('l’euro reste collé à son montant', () => {
    expect(money(0)).toBe(`0,00${NBSP}€`)
  })

  it('le suffixe reste collé au montant abrégé', () => {
    expect(moneyShort(847_000)).toBe(`847${NBSP}k€`)
  })
})
