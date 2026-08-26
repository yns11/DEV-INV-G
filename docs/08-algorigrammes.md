# Algorigrammes

Le processus d'inventaire tel qu'il fonctionne aujourd'hui, puis tel qu'il
fonctionnerait avec les **comptages avancés** décrits dans
[`07-comptages-avances.md`](07-comptages-avances.md).

Les diagrammes sont en Mermaid : ils se lisent tels quels dans GitHub et dans la
plupart des éditeurs Markdown.

Convention de couleur, la même partout :

- **bleu** — étape existante, inchangée ;
- **vert** — étape nouvelle, apportée par les comptages avancés ;
- **rouge** — point de blocage, ou perte d'information ;
- **gris** — geste hors application : balisage physique, mécanique interne de
  l'ERP.

Notation des quantités :

| Symbole | Quantité |
|---|---|
| `livre@T0⁻` | Stock ERP **avant** postage du journal avancé — la référence |
| `compté@T0` | Physique relevé au comptage avancé |
| `ajusté@T0` | Ajustements saisis à T0 |
| `physique@T0` | `compté@T0 + ajusté@T0`, et donc `livre@T0⁺` après réalignement |
| `livre@J` | Stock ERP du snapshot général gelé le jour J |

---

## 1. Ce que fait un journal de comptage

C'est la mécanique qui commande tout le reste : poster un journal ne consigne pas
un écart, il **réaligne l'ERP sur le physique compté**. Vu palette par palette :

```mermaid
flowchart TD
    A(["Palette théoriquement<br/>en emplacement A"]) --> B{"Scannée en A ?"}

    B -->|oui| C["A devient son emplacement<br/>ERP officiel"]
    B -->|non| D["Transfert automatique<br/>vers l'emplacement TAMPON"]

    D --> E{"Scannée plus tard<br/>ailleurs, en B ?"}
    E -->|oui| F["Transfert TAMPON → B<br/>B devient son emplacement officiel"]
    E -->|non| G["Elle reste au TAMPON"]

    C --> H(["Stock ERP de A<br/>= physique compté en A"])
    F --> H2(["Stock ERP de B<br/>= physique compté en B"])
    G --> I(["Le TAMPON centralise tous les<br/>écarts du stock géré par lots"])

    classDef erp fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef res fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    class A,B,C,D,E,F,G erp
    class H,H2,I res
```

**Ce qu'il faut en retenir :** après postage, le stock ERP d'un emplacement vaut
le physique qui y a été compté. C'est de là que découle l'attendu du jour J —
`livre@J = physique@T0`, et non `livre@J = livre@T0⁻`.

---

## 2. Le processus actuel

```mermaid
flowchart TD
    A([Créer la campagne]) --> B[PRÉPARATION<br/>seuils, articles, nomenclatures]
    B --> C[Créer les zones GENERIQUE<br/>et imprimer les feuilles]
    C --> D{Passer en comptage ?}
    D -->|oui| E[COMPTAGE]

    E --> F[Charger le stock ERP<br/>snapshot complet]
    F --> G[/Référentiel des emplacements<br/>déduit du snapshot/]
    G --> H[/Un journal PENDING<br/>par emplacement actif/]
    H --> I[Geler le stock ERP<br/>= la référence de la campagne]

    I --> J[Compter]
    J --> J1[Emplacements étiquetés<br/>scan INVE]
    J --> J2[Emplacements vrac<br/>saisie INVV]
    J --> J3[GENERIQUE<br/>feuilles, 2 passages, arbitrage]

    J1 --> K[Poster les journaux]
    J2 --> K
    J3 --> K3[Clore les zones]

    K --> KR[/L'ERP se réaligne sur le compté<br/>introuvables → tampon/]

    KR --> L{Contrôles de passage<br/>en analyse}
    K3 --> L
    L -->|stock ERP non gelé<br/>journaux non postés<br/>zones non closes| LX[Transition refusée]
    LX --> J
    L -->|tout est vert| M[ANALYSE]

    M --> N[Écart = physique − référence gelée]
    N --> O[Ajustements<br/>mouvements réels post-comptage]
    N --> P[Causes, backflush,<br/>analyse IA]
    O --> Q{Écarts matériels<br/>tous expliqués ?}
    P --> Q
    Q -->|non| QX[Clôture refusée]
    QX --> P
    Q -->|oui| R[Publier l'archive Delta]
    R --> S([CLÔTURÉE])

    classDef existant fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef erp fill:#e5e7eb,stroke:#6b7280,color:#374151
    class A,B,C,D,E,F,G,H,I,J,J1,J2,J3,K,K3,L,M,N,O,P,Q,R,S existant
    class LX,QX bloc
    class KR erp
```

**Ce que ce schéma montre du problème.** Le gel de la référence (`I`) précède
tout comptage, et les journaux naissent du chargement (`F → H`). Il n'existe donc
aucun point de ce parcours où compter un emplacement avant que la photo générale
n'ait été prise.

---

## 3. Les quatre quantités dans le temps

Le cœur de la conception. À lire de gauche à droite.

```mermaid
flowchart LR
    subgraph T0A ["J-2 · avant le comptage"]
        A1["livre@T0⁻<br/>stock ERP du lot"]
    end

    subgraph T0B ["J-2 · comptage et postage"]
        A2["compté@T0"]
        A3["ajusté@T0"]
        A4["physique@T0<br/>= compté + ajusté"]
        A5["livre@T0⁺<br/>l'ERP réaligné"]
    end

    subgraph JJ ["Jour J · chargement général"]
        B1["livre@J"]
    end

    subgraph R ["Ce qu'on en tire"]
        C1["ÉCART D'INVENTAIRE<br/>physique@T0 − livre@T0⁻"]
        C2["DÉRIVE<br/>livre@J − physique@T0<br/>attendue nulle"]
    end

    A2 --> A4
    A3 --> A4
    A4 -->|postage| A5

    A1 --> C1
    A4 --> C1
    A4 --> C2
    B1 --> C2

    A5 -.->|"si rien ne bouge,<br/>livre@J = livre@T0⁺"| B1

    classDef av fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef q fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef r fill:#dcfce7,stroke:#15803d,color:#14532d
    class A1 av
    class A2,A3,A4,A5,B1 q
    class C1,C2 r
```

**La règle qui en découle**, et qui est déjà celle du code — la référence est
*ce contre quoi la campagne a été comptée* :

| Emplacement | Référence de l'écart |
|---|---|
| Ordinaire | `livre@J` — rien n'était compté quand la photo a été prise |
| Précompté et scellé | `livre@T0⁻` — c'est contre lui que le comptage a eu lieu |

Sans cette règle, l'écart d'un emplacement précompté vaudrait
`physique@T0 − livre@J`, c'est-à-dire **zéro** dans le cas nominal : le résultat
de l'inventaire disparaîtrait, et l'IRA tendrait vers 100 % à mesure qu'on
précompte.

---

## 4. Le processus avec comptages avancés

```mermaid
flowchart TD
    A([Créer la campagne]) --> B[PRÉPARATION<br/>seuils, articles, nomenclatures]
    B --> C{Passer en comptage ?}
    C -->|oui| D[COMPTAGE<br/>référentiels gelés]

    D --> E{Des emplacements<br/>à précompter ?}
    E -->|non| GEN

    E -->|oui| L1[Créer un lot avancé<br/>code, date, emplacements<br/>TAMPON exclu]
    L1 --> L1B{Un journal du lot<br/>est-il déjà posté ?}
    L1B -->|oui| L1X[Refusé : la référence<br/>T0 est déjà écrasée]
    L1B -->|non| L2["Charger le stock ERP du lot<br/>MODE AVANCÉ = livre@T0⁻"]
    L2 --> L3[/"livre@T0⁻ devient la référence<br/>de ces emplacements"/]
    L3 --> L4[Compter le lot]
    L4 --> L5[Poster les journaux du lot]
    L5 --> L5B[/L'ERP se réaligne<br/>introuvables → tampon/]
    L5B --> L6[Saisir et poster<br/>l'ajustement du lot]
    L6 --> L7[Clore et SCELLER le lot]
    L7 --> L8[Baliser physiquement<br/>plus aucun mouvement]
    L8 --> E

    GEN[Ouvrir le comptage général] --> G1{Des lots non scellés ?}
    G1 -->|oui| G1W[Avertissement<br/>non bloquant] --> G2
    G1 -->|non| G2[Charger le stock ERP général]

    G2 --> G3[/"Référence remplacée partout<br/>SAUF sur les scellés"/]
    G3 --> G4["livre@J des scellés → table de dérive<br/>dérive = livre@J − physique@T0"]
    G4 --> G5[Geler le stock ERP]

    G5 --> DRIFT{Des dérives<br/>matérielles ?}
    DRIFT -->|oui| DISP[Traiter chaque dérive<br/>voir §5]
    DRIFT -->|non| CNT
    DISP --> CNT

    CNT[Compter le reste<br/>scan, saisie, GENERIQUE] --> CNT2[Compter le TAMPON<br/>en dernier]
    CNT2 --> CNT3[Poster, clore les zones]

    CNT3 --> T{Contrôles de passage<br/>en analyse}
    T -->|"stock ERP non gelé · journaux non postés<br/>zones non closes · DÉRIVE NON TRAITÉE"| TX[Transition refusée]
    TX --> DISP
    T -->|tout est vert| AN[ANALYSE]

    AN --> V[Écart = physique − référence<br/>chacune à sa date]
    V --> W[Ajustements, causes,<br/>backflush, analyse IA]
    W --> Y{Écarts matériels<br/>tous expliqués ?}
    Y -->|non| YX[Clôture refusée] --> W
    Y -->|oui| Z[Publier l'archive Delta<br/>+ lots + dérives + dates de référence]
    Z --> ZZ([CLÔTURÉE])

    classDef existant fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef horsapp fill:#e5e7eb,stroke:#6b7280,color:#374151
    class A,B,C,D,CNT,CNT3,T,AN,W,Y,Z,ZZ existant
    class E,L1,L1B,L2,L3,L4,L5,L6,L7,GEN,G1,G1W,G2,G3,G4,G5,DRIFT,DISP,CNT2,V nouveau
    class TX,YX,L1X bloc
    class L5B,L8 horsapp
```

---

## 5. Le traitement d'une dérive

La seule décision réellement nouvelle demandée à l'exploitant. Elle se prend
ligne par ligne — un article, un emplacement.

```mermaid
flowchart TD
    A[/"Emplacement scellé<br/>livre@T0⁻ · physique@T0 · livre@J"/] --> B{"livre@J = physique@T0 ?"}
    B -->|oui| OK["Cas nominal : le balisage a tenu<br/>et le réalignement a pris"]
    B -->|non| C["dérive = livre@J − physique@T0"]

    C --> D{"Dérive matérielle ?<br/>seuils de la campagne"}
    D -->|non| OK2[Consignée, non bloquante]

    D -->|oui| SIG{"dérive = −écart d'inventaire ?"}
    SIG -->|oui| R0["RÉALIGNEMENT MANQUÉ<br/>l'ERP est resté à livre@T0⁻"]
    R0 --> R1[Rejouer le postage]
    R1 --> R2[Recalculer la dérive]
    R2 --> B

    SIG -->|non| E{"Origine du mouvement ?"}

    E --> F[Recompter]
    E --> G[Ajuster]
    E --> H[Accepter]

    F --> F1[Desceller le journal<br/>motif obligatoire, tracé]
    F1 --> F2[Recompter le jour J]
    F2 --> F3["Le comptage du jour J fait foi<br/>la référence redevient livre@J"]

    G --> G1[Mouvement physique réel<br/>ligne d'ajustement]
    G1 --> G2["physique = physique@T0 + ajusté<br/>campagne et ERP d'accord au jour J"]

    H --> H1[Mouvement purement informatique]
    H1 --> H2["physique@T0 conservé<br/>cause MOUVEMENT_APRES_SCELLEMENT"]
    H2 --> H3["Campagne et ERP restent en désaccord<br/>de la valeur de la dérive : assumé"]

    F3 --> Z([Dérive traitée])
    G2 --> Z
    H3 --> Z

    D -->|oui| NO[Aucune disposition]
    NO --> BLOC[Passage en ANALYSE refusé<br/>EARLY_COUNT_DRIFT_UNRESOLVED]

    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef sig fill:#fef3c7,stroke:#b45309,color:#78350f
    class A,B,C,D,E,F,G,H,F1,F2,F3,G1,G2,H1,H2,H3,OK,OK2,Z nouveau
    class SIG,R0,R1,R2 sig
    class NO,BLOC bloc
```

**Pourquoi une décision humaine.** Un mouvement informatique seul, un prélèvement
réel et la correction d'un mauvais scan produisent la même trace dans l'ERP — même
type, même quantité, même date — y compris dans le miroir `erp_mouvements`. Seule
la branche jaune se déduit : une dérive exactement opposée à l'écart d'inventaire
signifie que l'ERP est resté où il était, donc que le postage n'a pas pris.

---

## 6. Ce que la dérive ne verra pas

La limite honnête du dispositif. Elle se lit en suivant une palette sortie d'un
emplacement scellé **sans aucune transaction ERP**.

```mermaid
flowchart TD
    A(["Palette comptée à T0<br/>dans l'emplacement scellé A"]) --> B["Elle sort de A physiquement<br/>aucun mouvement ERP"]
    B --> C["livre@J de A l'inclut toujours<br/>DÉRIVE = 0"]
    C --> D{"Est-elle scannée<br/>ailleurs le jour J ?"}

    D -->|oui, en B| E["L'ERP la rattache à B<br/>mais après le gel"]
    E --> F["Comptée deux fois :<br/>physique@T0 de A + comptage de B"]
    F --> G["Visible comme excédent sur la RÉFÉRENCE<br/>EARLY_COUNT_DOUBLE_COUNT"]

    D -->|non| H["Rien ne la voit"]
    H --> I["L'ERP la croit en A,<br/>la campagne le confirme"]
    I --> J["La perte n'apparaîtra<br/>qu'à l'inventaire suivant"]

    J --> K["Comptée le jour J, elle serait partie<br/>au tampon et la perte aurait été vue"]
    K --> L(["Le précomptage échange du pouvoir de détection<br/>contre de la charge en moins.<br/>La fenêtre T0 → J en mesure l'ampleur."])

    classDef fait fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef vu fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef risque fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class A,B,C,D,E,F fait
    class G vu
    class H,I,J,K,L risque
```

Aucune écriture de code ne rattrape la branche de droite : seul le balisage
physique le fait. C'est un argument pour des fenêtres courtes et pour réserver le
précomptage aux emplacements réellement immobilisés.

---

## 7. L'emplacement tampon dans le temps

```mermaid
flowchart LR
    A(["TAMPON<br/>vide en début de campagne"]) --> B["J-2 · lot avancé posté<br/>+ les introuvables du lot"]
    B --> C["Jour J · comptage général<br/>+ les introuvables du général"]
    C --> D["Jour J · palettes retrouvées<br/>− celles rattachées ailleurs"]
    D --> E(["Compté en DERNIER<br/>= tous les écarts du stock géré par lots"])

    F["Jamais précompté<br/>jamais scellé"] -.-> A
    G["Aucun contrôle de dérive<br/>ne s'y applique"] -.-> C

    classDef t fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef regle fill:#dcfce7,stroke:#15803d,color:#14532d
    class A,B,C,D,E t
    class F,G regle
```

Le tampon agrège les manquants de tous les emplacements : la lecture **par
emplacement** en devient structurellement trompeuse — un emplacement scellé
paraît juste, le tampon paraît catastrophique, alors qu'il ne s'agit que d'un
déplacement d'écriture. L'application a déjà tranché ce point, et l'écran
d'analyse s'ouvre sur la lecture par référence :

> L'écart vu par emplacement compte deux fois une palette déplacée. La différence
> avec l'écart par référence mesure exactement cette part-là.

Le précomptage ne change pas cette conclusion, il la renforce.

---

## 8. Cycle de vie d'un journal de comptage

```mermaid
stateDiagram-v2
    [*] --> PENDING : chargement du stock ERP

    PENDING --> IN_PROGRESS : première quantité saisie
    IN_PROGRESS --> POSTED : poster, et l'ERP se réaligne
    POSTED --> IN_PROGRESS : rouvrir, campagne en COMPTAGE
    PENDING --> BOOK_ENFORCED : forcer au stock ERP

    POSTED --> SCELLE : sceller le lot avancé
    SCELLE --> POSTED : desceller, motif obligatoire

    POSTED --> [*] : passage en ANALYSE
    SCELLE --> [*] : passage en ANALYSE
    BOOK_ENFORCED --> [*] : passage en ANALYSE

    note right of SCELLE
        Premier gel par objet du produit.
        mutability_of garde le dernier mot
        pour interdire ; le scellement ne
        fait que restreindre davantage.
    end note

    note right of BOOK_ENFORCED
        Force compté := livre@J.
        Détruit la référence, donc le
        résultat de l'inventaire, et
        absorbe la dérive sans un mot.
        Reste juste pour ce pour quoi il
        a été écrit : un magasin extérieur
        dont on reprend le chiffre ERP
        sans preuve de comptage.
    end note
```
