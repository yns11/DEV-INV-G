# Algorigrammes

Le processus d'inventaire tel qu'il fonctionne aujourd'hui, puis tel qu'il
fonctionnerait avec les **comptages avancés** décrits dans
[`07-comptages-avances.md`](07-comptages-avances.md).

Les diagrammes sont en Mermaid : ils se lisent tels quels dans GitHub et dans la
plupart des éditeurs Markdown.

Convention de couleur, la même partout :

- **bleu** — étape existante, inchangée ;
- **vert** — étape nouvelle, apportée par les comptages avancés ;
- **rouge** — point de blocage, la campagne n'avance pas tant qu'il n'est pas
  levé ;
- **gris** — geste hors application (balisage physique, comptage papier).

---

## 1. Le processus actuel

```mermaid
flowchart TD
    A([Créer la campagne]) --> B[PRÉPARATION<br/>seuils, articles, nomenclatures]
    B --> C[Créer les zones GENERIQUE<br/>et imprimer les feuilles]
    C --> D{Passer en comptage ?}
    D -->|oui| E[COMPTAGE]

    E --> F[Charger le stock ERP<br/>snapshot complet]
    F --> G[/Le référentiel des emplacements<br/>est déduit du snapshot/]
    G --> H[/Un journal PENDING<br/>par emplacement actif/]
    H --> I[Geler le stock ERP]

    I --> J[Compter]
    J --> J1[Emplacements étiquetés<br/>scan INVE]
    J --> J2[Emplacements vrac<br/>saisie INVV]
    J --> J3[GENERIQUE<br/>feuilles, 2 passages, arbitrage]

    J1 --> K[Poster les journaux]
    J2 --> K
    J3 --> K3[Clore les zones]

    K --> L{Contrôles de passage<br/>en analyse}
    K3 --> L
    L -->|stock ERP non gelé<br/>journaux non postés<br/>zones non closes| LX[Transition refusée]
    LX --> J
    L -->|tout est vert| M[ANALYSE]

    M --> N[Écart = physique − stock ERP]
    N --> O[Ajustements<br/>mouvements post-comptage]
    N --> P[Causes, backflush,<br/>analyse IA]
    O --> Q{Écarts matériels<br/>tous expliqués ?}
    P --> Q
    Q -->|non| QX[Clôture refusée]
    QX --> P
    Q -->|oui| R[Publier l'archive Delta]
    R --> S([CLÔTURÉE])

    classDef existant fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class A,B,C,D,E,F,G,H,I,J,J1,J2,J3,K,K3,L,M,N,O,P,Q,R,S existant
    class LX,QX bloc
```

**Ce que ce schéma montre du problème.** Les journaux naissent du chargement du
stock ERP (`F → H`), et ce chargement est unique et complet. Il n'existe donc
aucun point, dans ce parcours, où l'on puisse compter un emplacement avant que la
photo générale ait été prise.

---

## 2. Le contournement possible aujourd'hui, et ce qu'il perd

```mermaid
flowchart TD
    subgraph P1 [Campagne dédiée « lot J-2 »]
        A1[Stock ERP<br/>des emplacements du lot] --> B1[Comptage] --> C1[Écarts + analyse] --> D1[Clôture + archive n°1]
    end

    subgraph P2 [Campagne générale, jour J]
        A2[Stock ERP complet] --> B2[Journaux de tous<br/>les emplacements]
        B2 --> C2[Forcer au stock ERP<br/>les emplacements du lot]
        C2 --> D2[/enforce_book_stock remplace<br/>les lignes du journal/]
        D2 --> E2[Écart nul par construction]
        B2 --> F2[Comptage du reste] --> G2[Analyse] --> H2[Clôture + archive n°2]
        E2 --> G2
    end

    D1 -.->|le chiffre compté<br/>ne traverse pas| C2
    E2 --> X1[Un mouvement survenu<br/>malgré le balisage<br/>est effacé, sans trace]

    H2 --> X2[Deux archives, deux IRA,<br/>deux jeux de seuils et de prix]

    classDef existant fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef perte fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class A1,B1,C1,D1,A2,B2,C2,D2,F2,G2,H2 existant
    class E2,X1,X2 perte
```

---

## 3. Le processus avec comptages avancés

```mermaid
flowchart TD
    A([Créer la campagne]) --> B[PRÉPARATION<br/>seuils, articles, nomenclatures]
    B --> C{Passer en comptage ?}
    C -->|oui| D[COMPTAGE<br/>référentiels gelés]

    D --> E{Des emplacements<br/>à précompter ?}
    E -->|non| GEN

    E -->|oui| L1[Créer un lot avancé<br/>code, date, emplacements]
    L1 --> L2[Charger le stock ERP du lot<br/>MODE AVANCÉ, partiel]
    L2 --> L3[/"livre@T0 → early_count_baseline<br/>journaux du lot créés"/]
    L3 --> L4[Compter le lot]
    L4 --> L5[Poster les journaux du lot]
    L5 --> L6[Clore et SCELLER le lot]
    L6 --> L7[Baliser physiquement<br/>plus aucun mouvement]
    L7 --> E

    GEN[Ouvrir le comptage général] --> G1{Des lots non scellés ?}
    G1 -->|oui| G1W[Avertissement<br/>non bloquant] --> G2
    G1 -->|non| G2[Charger le stock ERP général<br/>MODE GÉNÉRAL, complet]

    G2 --> G3[/"book_stock remplacé = livre@J<br/>journaux scellés préservés"/]
    G3 --> G4[Calculer la dérive<br/>sur les emplacements scellés]
    G4 --> G5[Geler le stock ERP]

    G5 --> DRIFT{Des dérives<br/>matérielles ?}
    DRIFT -->|oui| DISP[Traiter chaque dérive<br/>voir §4]
    DRIFT -->|non| CNT
    DISP --> CNT

    CNT[Compter le reste<br/>scan, saisie, feuilles GENERIQUE] --> CNT2[Poster, clore les zones]

    CNT2 --> T{Contrôles de passage<br/>en analyse}
    T -->|stock ERP non gelé<br/>journaux non postés<br/>zones non closes<br/>DÉRIVE NON TRAITÉE| TX[Transition refusée]
    TX --> DISP
    T -->|tout est vert| AN[ANALYSE]

    AN --> V["Écart = physique − livre@J<br/>+ décomposition écart / dérive"]
    V --> W[Ajustements, causes,<br/>backflush, analyse IA]
    W --> Y{Écarts matériels<br/>tous expliqués ?}
    Y -->|non| YX[Clôture refusée] --> W
    Y -->|oui| Z[Publier l'archive Delta<br/>+ baseline + dérives]
    Z --> ZZ([CLÔTURÉE])

    classDef existant fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef horsapp fill:#e5e7eb,stroke:#6b7280,color:#374151
    class A,B,C,D,CNT,CNT2,T,AN,W,Y,Z,ZZ existant
    class E,L1,L2,L3,L4,L5,L6,GEN,G1,G1W,G2,G3,G4,G5,DRIFT,DISP,V nouveau
    class TX,YX bloc
    class L7 horsapp
```

---

## 4. Le traitement d'une dérive

C'est la seule décision vraiment nouvelle demandée à l'exploitant. Elle se prend
ligne par ligne — un article, un emplacement.

```mermaid
flowchart TD
    A[/"Emplacement scellé<br/>livre@T0, compté@T0, livre@J"/] --> B{"livre@J = livre@T0 ?"}
    B -->|oui| OK["Cas nominal<br/>le balisage a tenu<br/>écart = compté@T0 − livre@T0"]
    B -->|non| C["dérive = livre@J − livre@T0"]

    C --> D{Dérive matérielle ?<br/>seuils de la campagne}
    D -->|non| OK2[Consignée, non bloquante]
    D -->|oui| E{Origine du mouvement ?}

    E --> F[Recompter]
    E --> G[Ajuster]
    E --> H[Accepter]

    F --> F1[Desceller le journal<br/>motif obligatoire, tracé]
    F1 --> F2[Recompter le jour J]
    F2 --> F3[Le comptage du jour J fait foi<br/>le comptage avancé reste en audit]

    G --> G1[Ligne d'ajustement<br/>RECOUNT ou ADJUSTMENT]
    G1 --> G2["physique = compté@T0 + ajusté"]

    H --> H1["compté@T0 conservé"]
    H1 --> H2[Cause obligatoire<br/>MOUVEMENT_APRES_SCELLEMENT<br/>+ commentaire]
    H2 --> H3[La dérive reste dans l'écart,<br/>mais nommée et chiffrée]

    F3 --> Z([Dérive traitée])
    G2 --> Z
    H3 --> Z

    NO[Aucune disposition] --> BLOC[Passage en ANALYSE refusé<br/>EARLY_COUNT_DRIFT_UNRESOLVED]
    D -->|oui| NO

    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class A,B,C,D,E,F,G,H,F1,F2,F3,G1,G2,H1,H2,H3,OK,OK2,Z nouveau
    class NO,BLOC bloc
```

**Pourquoi une décision humaine et non un calcul.** Un mouvement informatique
seul (régularisation, saisie tardive) et un mouvement physique réel produisent
la même trace dans l'ERP, y compris dans le miroir `erp_mouvements` : même
forme, même quantité, même date. Le choix de la branche `E` ne peut pas être
déduit des données.

Les deux cas sans ligne en face suivent le même parcours : un article **apparu**
dans `livre@J` a `livre@T0 = 0` et `compté@T0 = 0` ; un article **disparu** a
`livre@J = 0`. C'est pourquoi le rapprochement doit être une jointure externe
complète, pas une jointure interne.

---

## 5. Cycle de vie d'un journal de comptage

À gauche l'existant, à droite ce que le scellement ajoute.

```mermaid
stateDiagram-v2
    [*] --> PENDING : chargement du stock ERP

    PENDING --> IN_PROGRESS : première quantité saisie
    IN_PROGRESS --> POSTED : poster
    POSTED --> IN_PROGRESS : rouvrir (campagne en COMPTAGE)
    PENDING --> BOOK_ENFORCED : forcer au stock ERP<br/>(remplace les lignes)

    POSTED --> SCELLE : sceller le lot avancé
    SCELLE --> POSTED : desceller<br/>motif obligatoire, tracé

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
        Reste disponible pour ce pour quoi
        il a été écrit : un magasin extérieur
        dont on reprend le chiffre ERP sans
        preuve de comptage.
    end note
```

---

## 6. Les trois quantités, vues comme un flux

```mermaid
flowchart LR
    subgraph T0 [J-2 · comptage avancé]
        A1["livre@T0<br/>stock ERP du lot"]
        A2["compté@T0<br/>physique relevé"]
    end

    subgraph J [Jour J · comptage général]
        B1["livre@J<br/>stock ERP gelé"]
    end

    subgraph R [Résultat]
        C1["écart d'inventaire<br/>compté@T0 − livre@T0"]
        C2["dérive post-scellement<br/>livre@J − livre@T0"]
        C3["écart de campagne<br/>physique − livre@J"]
    end

    A1 --> C1
    A2 --> C1
    A1 --> C2
    B1 --> C2
    A2 --> C3
    B1 --> C3

    C1 -.->|s'analyse comme<br/>n'importe quel écart| C3
    C2 -.->|s'explique,<br/>ne se soustrait pas| C3

    classDef q fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef r fill:#dcfce7,stroke:#15803d,color:#14532d
    class A1,A2,B1 q
    class C1,C2,C3 r
```

L'égalité qui doit rester vraie, et qui est la raison de conserver `livre@T0` :

```
écart de campagne = écart d'inventaire − dérive + ajustements
```

Si `livre@T0` n'est pas conservé, seul le membre de gauche est calculable, et un
écart d'inventaire de +10 masqué par une dérive de +10 s'affiche à zéro.
