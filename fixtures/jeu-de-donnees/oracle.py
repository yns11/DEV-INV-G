"""Le calcul théorique attendu, écrit **sans l'application**.

Ce script ne lit rien de ``inventory``. Il ré-implémente les règles à partir de
ce que la documentation en dit — pas à partir du code qui les applique. C'est
tout l'intérêt : deux implémentations indépendantes qui tombent sur les mêmes
chiffres se confirment l'une l'autre, alors qu'une seule ne confirme rien.

Il lit les CSV du jeu de données à côté de lui et écrit ``attendu.json``.

    python fixtures/jeu-de-donnees/oracle.py

Les règles appliquées, dans l'ordre où elles s'enchaînent :

1. **Le périmètre.** Un emplacement `Désactivé` et un article exclu `ALL` sortent
   des quantités *et* des valeurs, des deux côtés.
2. **La référence.** Un emplacement précompté et scellé porte le stock ERP de
   son propre journal, à sa date ; tout autre emplacement porte le snapshot du
   jour J. Une ligne du snapshot qui vise un emplacement scellé est ignorée.
3. **Le comptage.** Seul le journal qui *possède* un emplacement le compte : les
   lignes de passage d'un autre journal restent une trace. `BOOK_ENFORCED`
   contribue la quantité du stock ERP elle-même.
4. **GENERIQUE.** Deux passages, arbitrage sur désaccord, éclatement des
   sections WIP par la nomenclature, retrait des articles exclus `GENERIC`
   **après** éclatement, retrait des produits finis comptés hors WIP.
5. **Le physique.** Compté + ajustements postés.
6. **La valorisation.** `prix standard × quantité`, partout et des deux côtés.
7. **Les KPI.** Écart net signé, écart brut en valeur absolue, fiabilités, IRA,
   matérialité par seuils.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

HERE = Path(__file__).parent

# Seuils de matérialité de la campagne de contrôle : une ligne est une
# exception quand **toutes** les portes configurées cèdent.
SEUIL_VALEUR = Decimal("100")      # |Δ€| >= 100
SEUIL_QTE_RELATIVE = Decimal("0.02")  # |Δqté| / stock ERP >= 2 %

QTE = Decimal("0.000001")
EUR = Decimal("0.01")


def q(v: Decimal) -> Decimal:
    return v.quantize(QTE, rounding=ROUND_HALF_UP)


def e(v: Decimal) -> Decimal:
    return v.quantize(EUR, rounding=ROUND_HALF_UP)


def lire(nom: str) -> list[dict[str, str]]:
    with (HERE / nom).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def dec(texte: str) -> Decimal:
    return Decimal((texte or "0").replace(",", ".").strip() or "0")


# --------------------------------------------------------------------------- #
# 1. Référentiels
# --------------------------------------------------------------------------- #

articles = {
    r["Numéro d'article"]: {
        "type": r["Type produit"],
        "prix": dec(r["Prix standard (€)"]),
        "exclusion": r["Exclusion"].strip(),
        "unite": r["Unité"],
    }
    for r in lire("01-articles.csv")
}

#: Un article `ALL` ne produit aucune ligne d'écart, où qu'il soit compté.
hors_perimetre = {ref for ref, a in articles.items() if a["exclusion"] == "ALL"}
#: Un article `GENERIC` sort de la consolidation GENERIQUE, et de là seulement :
#: son stock ERP et ses écarts ailleurs restent légitimes.
hors_generique = {ref for ref, a in articles.items() if a["exclusion"] == "GENERIC"}

nomenclature: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
for r in lire("02-nomenclatures.csv"):
    if r["Statut"].strip().lower().startswith("actif"):
        nomenclature[r["Assemblage (parent)"]].append(
            (r["Composant (enfant)"], dec(r["Quantité par assemblage"]))
        )

emplacements = {
    (r["Entrepôt"], r["Emplacement"]): r["Statut"]
    for r in lire("03-emplacements.csv")
}
desactives = {k for k, statut in emplacements.items() if statut.startswith("Dés")}

GENERIQUE = ("B06VRAC", "GENERIQUE")


def actif(entrepot: str, emplacement: str) -> bool:
    return (entrepot, emplacement) not in desactives


# --------------------------------------------------------------------------- #
# 2. Précomptages : périmètre déclaré, référence et comptage
# --------------------------------------------------------------------------- #

#: Ce que l'utilisateur déclare à l'écran — le périmètre de chaque journal.
#: Les autres emplacements touchés par ses lignes sont des lignes de passage.
PERIMETRES = {
    "NPEM-A": {("ATP", "SOL"), ("ATP", "SE2")},
    "NPEM-B": {("B06", "PAL01")},
}

precomptage = lire("04-journaux-precomptage.csv")

#: Qui possède chaque emplacement scellé.
proprietaire = {
    cle: journal for journal, cles in PERIMETRES.items() for cle in cles
}
scelles = set(proprietaire)

reference_t0: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
compte_t0: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
lignes_de_passage: list[dict[str, str]] = []

for r in precomptage:
    cle = (r["Entrepôt"], r["Emplacement"])
    ref = r["Numéro d'article"]
    if proprietaire.get(cle) != r["Journal ERP"]:
        # Ligne de passage : conservée comme trace, elle ne compte pas.
        lignes_de_passage.append(r)
        continue
    if not actif(*cle) or ref in hors_perimetre:
        continue
    k = (ref, *cle)
    reference_t0[k] += dec(r["Stock ERP"])
    compte_t0[k] += dec(r["Quantité comptée"])


# --------------------------------------------------------------------------- #
# 3. Stock ERP du jour J — les emplacements scellés sont préservés
# --------------------------------------------------------------------------- #

reference: dict[tuple[str, str, str], Decimal] = dict(reference_t0)
ignorees_car_scellees: list[tuple[str, str, str]] = []
#: Le coût porté par la ligne de stock. Il ne valorise rien tant que l'article a
#: un prix standard : la campagne se valorise au prix standard, partout et des
#: deux côtés. Il est le secours d'un article inconnu ou d'un prix nul — mieux
#: vaut la valeur que l'ERP portait que zéro.
cout_de_secours: dict[str, Decimal] = {}

for r in lire("05-stock-erp-jour-j.csv"):
    cle = (r["Entrepôt"], r["Emplacement"])
    ref = r["Numéro d'article"]
    if cle in scelles:
        ignorees_car_scellees.append((ref, *cle))
        continue
    if not actif(*cle) or ref in hors_perimetre:
        continue
    reference[(ref, *cle)] = reference.get((ref, *cle), Decimal(0)) + dec(
        r["Stock physique"]
    )
    # Coût de secours : il ne sert que si l'article n'a pas de prix standard.
    cout = dec(r["Coût unitaire (€)"])
    if cout:
        cout_de_secours.setdefault(ref, cout)


# --------------------------------------------------------------------------- #
# 4. Comptages du jour J
# --------------------------------------------------------------------------- #

compte: dict[tuple[str, str, str], Decimal] = dict(compte_t0)

for r in lire("06-journaux-jour-j.csv"):
    cle = (r["Entrepôt"], r["Emplacement"])
    ref = r["Numéro d'article"]
    if cle in scelles:
        # L'emplacement appartient à son précomptage : la ligne du jour J est
        # une trace, pas un comptage.
        lignes_de_passage.append(r)
        continue
    if not actif(*cle) or ref in hors_perimetre:
        continue
    compte[(ref, *cle)] = compte.get((ref, *cle), Decimal(0)) + dec(
        r["Quantité comptée"]
    )

#: Emplacements inventoriés ailleurs : le comptage *est* le stock ERP, écart nul
#: par construction.
FORCES_AU_STOCK_ERP = {("B06", "FORCE")}
for (ref, entrepot, emplacement), qte in list(reference.items()):
    if (entrepot, emplacement) in FORCES_AU_STOCK_ERP:
        compte[(ref, entrepot, emplacement)] = qte


# --------------------------------------------------------------------------- #
# 5. GENERIQUE : deux passages, arbitrage, éclatement
# --------------------------------------------------------------------------- #

passages: dict[tuple[str, str], dict[int, Decimal]] = defaultdict(dict)
for r in lire("09-comptages-generique.csv"):
    passages[(r["Numéro d'article"], r["Section"])][int(r["Passage"])] = dec(
        r["Quantité comptée"]
    )

arbitrages = {
    (r["Numéro d'article"], r["Section"]): dec(r["Quantité arbitrée"])
    for r in lire("09b-arbitrages-generique.csv")
    if r["Décidée"].strip().lower().startswith("o")
}

retenu: dict[tuple[str, str], Decimal] = {}
desaccords: list[str] = []
for cle, par_passage in passages.items():
    if cle in arbitrages:
        retenu[cle] = arbitrages[cle]          # 1. une décision d'arbitrage
        continue
    p1, p2 = par_passage.get(1), par_passage.get(2)
    if p1 is not None and p2 is not None:
        if p1 != p2:
            desaccords.append(f"{cle[0]} / {cle[1]}")
            continue                            # 4. non résolu : bloquant
        retenu[cle] = p2                        # 2. accord → passage 2
    else:
        retenu[cle] = p2 if p2 is not None else p1  # 3. un seul passage

bord_de_ligne: dict[str, Decimal] = defaultdict(Decimal)
wip_assemble: dict[str, Decimal] = defaultdict(Decimal)
wip_a_eclater: dict[str, Decimal] = defaultdict(Decimal)
finis_hors_wip: list[str] = []

for (ref, section), qte in retenu.items():
    if qte == 0:
        continue
    # Un produit fini n'entre que par la porte du WIP : compté en bord de ligne
    # il compterait une deuxième fois ce que ses composants comptent déjà.
    if section != "WIP (à éclater)" and articles.get(ref, {}).get("type") == "Produit fini":
        finis_hors_wip.append(ref)
        continue
    if section == "Bord de ligne":
        bord_de_ligne[ref] += qte
    elif section == "WIP assemblé":
        wip_assemble[ref] += qte
    else:
        wip_a_eclater[ref] += qte


def eclater(assemblage: str, qte: Decimal, sortie: dict[str, Decimal]) -> None:
    """Créditer les composants, en descendant jusqu'aux feuilles."""
    for enfant, par_unite in nomenclature.get(assemblage, []):
        if enfant in nomenclature:
            eclater(enfant, qte * par_unite, sortie)
        else:
            sortie[enfant] += qte * par_unite


eclate: dict[str, Decimal] = defaultdict(Decimal)
for assemblage, qte in wip_a_eclater.items():
    eclater(assemblage, qte, eclate)

consolide: dict[str, Decimal] = {}
for ref in set(bord_de_ligne) | set(wip_assemble) | set(eclate):
    # L'exclusion GENERIQUE s'applique **après** l'éclatement : un assemblage
    # hors périmètre crédite quand même ses composants.
    if ref in hors_generique or ref in hors_perimetre:
        continue
    total = bord_de_ligne[ref] + wip_assemble[ref] + eclate[ref]
    if total:
        consolide[ref] = total

# Le résultat de la consolidation **remplace** tout comptage sur GENERIQUE.
for (ref, entrepot, emplacement) in list(compte):
    if (entrepot, emplacement) == GENERIQUE:
        del compte[(ref, entrepot, emplacement)]
for ref, qte in consolide.items():
    compte[(ref, *GENERIQUE)] = qte


# --------------------------------------------------------------------------- #
# 6. Ajustements postés après comptage
# --------------------------------------------------------------------------- #

ajuste: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
for r in lire("10-ajustements.csv"):
    cle = (r["Entrepôt"], r["Emplacement"])
    ref = r["Numéro d'article"]
    if not actif(*cle) or ref in hors_perimetre:
        continue
    ajuste[(ref, *cle)] += dec(r["Quantité"])


# --------------------------------------------------------------------------- #
# 7. Écarts et KPI
# --------------------------------------------------------------------------- #

backflush = {
    r["Numéro d'article"]: dec(r["Écart backflush"]) for r in lire("11-backflush.csv")
}


def prix_de(ref: str) -> Decimal:
    """`prix standard × quantité` partout ; le coût de la ligne en secours."""
    prix = articles.get(ref, {}).get("prix", Decimal(0))
    return prix if prix else cout_de_secours.get(ref, Decimal(0))


def construire(par_emplacement: bool):
    """Une ligne d'écart par article (ou par article et emplacement)."""
    def cle_de(ref, entrepot, emplacement):
        return (ref, entrepot, emplacement) if par_emplacement else (ref, "", "")

    ref_agg: dict[tuple, Decimal] = defaultdict(Decimal)
    cpt_agg: dict[tuple, Decimal] = defaultdict(Decimal)
    aju_agg: dict[tuple, Decimal] = defaultdict(Decimal)
    for (ref, w, l), qte in reference.items():
        ref_agg[cle_de(ref, w, l)] += qte
    for (ref, w, l), qte in compte.items():
        cpt_agg[cle_de(ref, w, l)] += qte
    for (ref, w, l), qte in ajuste.items():
        aju_agg[cle_de(ref, w, l)] += qte

    lignes = []
    for cle in sorted(set(ref_agg) | set(cpt_agg) | set(aju_agg)):
        ref = cle[0]
        prix = prix_de(ref)
        stock_erp = q(ref_agg.get(cle, Decimal(0)))
        compte_q = q(cpt_agg.get(cle, Decimal(0)))
        ajuste_q = q(aju_agg.get(cle, Decimal(0)))
        physique = q(compte_q + ajuste_q)
        ecart = q(physique - stock_erp)
        lignes.append({
            "article": ref,
            "entrepot": cle[1],
            "emplacement": cle[2],
            "prixStandard": str(prix),
            "stockErpQte": str(stock_erp),
            "stockErpValeur": str(e(stock_erp * prix)),
            "compteQte": str(compte_q),
            "ajusteQte": str(ajuste_q),
            "physiqueQte": str(physique),
            "physiqueValeur": str(e(physique * prix)),
            "ecartQte": str(ecart),
            "ecartValeur": str(e(ecart * prix)),
            "ecartBackflushQte": str(backflush.get(ref, Decimal(0))),
            "compteSeul": cle not in ref_agg and cle in cpt_agg,
            "erpSeul": cle in ref_agg and cle not in cpt_agg,
            "exact": (compte_q == 0 if stock_erp == 0 else ecart == 0),
            "materiel": materiel(stock_erp, ecart, e(ecart * prix)),
        })
    return lignes


def materiel(stock_erp: Decimal, ecart_qte: Decimal, ecart_valeur: Decimal) -> bool:
    """Toutes les portes doivent céder — sinon la liste d'exceptions gonfle."""
    if ecart_qte == 0:
        return False
    if stock_erp == 0:
        return True          # un stock que l'ERP ignore n'est pas un arrondi
    if abs(ecart_valeur) < SEUIL_VALEUR:
        return False
    return abs(ecart_qte) / abs(stock_erp) >= SEUIL_QTE_RELATIVE


def kpis(lignes) -> dict:
    somme = lambda champ: sum(Decimal(l[champ]) for l in lignes)  # noqa: E731
    stock_erp_q = q(somme("stockErpQte"))
    stock_erp_v = e(somme("stockErpValeur"))
    physique_q = q(somme("physiqueQte"))
    physique_v = e(somme("physiqueValeur"))
    ecart_net_q = q(somme("ecartQte"))
    ecart_net_v = e(somme("ecartValeur"))
    ecart_brut_q = q(sum(abs(Decimal(l["ecartQte"])) for l in lignes))
    ecart_brut_v = e(sum(abs(Decimal(l["ecartValeur"])) for l in lignes))
    exactes = sum(1 for l in lignes if l["exact"])

    def fiabilite(numerateur: Decimal, base: Decimal):
        if base == 0:
            return None
        ratio = Decimal(1) - numerateur / base
        return str(max(Decimal(-1), min(Decimal(1), ratio)))

    return {
        "stockErpQte": str(stock_erp_q),
        "stockErpValeur": str(stock_erp_v),
        "compteQte": str(q(somme("compteQte"))),
        "physiqueQte": str(physique_q),
        "physiqueValeur": str(physique_v),
        "ecartNetQte": str(ecart_net_q),
        "ecartNetValeur": str(ecart_net_v),
        "ecartBrutQte": str(ecart_brut_q),
        "ecartBrutValeur": str(ecart_brut_v),
        "fiabiliteNetteValeur": fiabilite(abs(ecart_net_v), abs(stock_erp_v)),
        "fiabiliteBruteValeur": fiabilite(ecart_brut_v, abs(stock_erp_v)),
        "fiabiliteBruteQte": fiabilite(ecart_brut_q, abs(stock_erp_q)),
        "ira": str(Decimal(exactes) / Decimal(len(lignes))) if lignes else None,
        "nbLignes": len(lignes),
        "nbLignesExactes": exactes,
        "nbLignesMaterielles": sum(1 for l in lignes if l["materiel"]),
        "nbCompteSeul": sum(1 for l in lignes if l["compteSeul"]),
        "nbErpSeul": sum(1 for l in lignes if l["erpSeul"]),
    }


par_article = construire(par_emplacement=False)
par_emplacement = construire(par_emplacement=True)

attendu = {
    "campagne": {
        "code": "INV-TEST-01",
        "dateComptage": "2026-06-13",
        "seuilValeurEur": str(SEUIL_VALEUR),
        "seuilQteRelative": str(SEUIL_QTE_RELATIVE),
    },
    "kpi": kpis(par_article),
    "ecartsParArticle": par_article,
    "ecartsParEmplacement": par_emplacement,
    "consolidationGenerique": {
        ref: str(q(qte)) for ref, qte in sorted(consolide.items())
    },
    "arbitragesNonResolus": sorted(desaccords),
    "produitsFinisHorsWip": sorted(finis_hors_wip),
    "emplacementsScelles": sorted(f"{w} / {l}" for w, l in scelles),
    "referenceScellee": {
        f"{ref} @ {w} / {l}": str(q(qte))
        for (ref, w, l), qte in sorted(reference_t0.items())
    },
    "lignesDuSnapshotIgnorees": sorted(
        f"{ref} @ {w} / {l}" for ref, w, l in ignorees_car_scellees
    ),
    "lignesDePassage": sorted(
        f"{r['Journal ERP']} ligne {r['Numéro de ligne']} → "
        f"{r['Entrepôt']} / {r['Emplacement']}"
        for r in lignes_de_passage
    ),
    "alertesEtiquettes": [
        {
            "etiquette": "ET-002",
            "article": "P-200",
            "emplacementScelle": "ATP / SOL",
            "compteeAussiEn": "ATP / QUAI",
            "dansLeJournal": "NPEM-J1",
        }
    ],
}

if __name__ == "__main__":
    (HERE / "attendu.json").write_text(
        json.dumps(attendu, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    k = attendu["kpi"]
    print(f"Stock ERP     {k['stockErpQte']:>18} unités   {k['stockErpValeur']:>12} €")
    print(f"Stock physique{k['physiqueQte']:>18} unités   {k['physiqueValeur']:>12} €")
    print(f"Écart net     {k['ecartNetQte']:>18} unités   {k['ecartNetValeur']:>12} €")
    print(f"Écart brut    {k['ecartBrutQte']:>18} unités   {k['ecartBrutValeur']:>12} €")
    print(f"IRA           {k['ira']}   ({k['nbLignesExactes']}/{k['nbLignes']})")
    print(f"Lignes matérielles : {k['nbLignesMaterielles']}")
    print(f"→ {HERE / 'attendu.json'}")
