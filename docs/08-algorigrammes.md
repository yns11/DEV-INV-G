# Algorigrammes

Le processus d'inventaire avant les **comptages avancés**, puis tel qu'il
fonctionne depuis. La logique est décrite dans
[`07-comptages-avances.md`](07-comptages-avances.md), le mode d'emploi dans le
[guide utilisateur](04-guide-utilisateur.md).

Les diagrammes sont en Mermaid : ils se lisent tels quels dans GitHub et dans la
plupart des éditeurs Markdown.

Convention de couleur, la même partout :

- **bleu** — étape existante, inchangée ;
- **vert** — étape nouvelle, apportée par les comptages avancés ;
- **rouge** — point de blocage, ou perte d'information ;
- **jaune** — décision humaine ;
- **gris** — geste hors application : balisage physique, mécanique interne de
  l'ERP.

Notation des quantités :

| Symbole | Quantité |
|---|---|
| `compté@T0` | Colonne « Qté Comptée » du journal avancé, agrégée sur son périmètre |
| `ERP@J` | Snapshot ERP général, gelé le jour J — **la référence, et la seule** |

> `ERP@T0` — la colonne « Stock ERP » du journal avancé — a été une référence
> dans une version précédente. Elle ne l'est plus : le journal est posté dans
> l'ERP avant que la photo du jour J ne soit prise, donc la photo l'intègre déjà,
> et mesurer contre l'état antérieur comptait deux fois la même correction.

---

## 1. Ce que contient une ligne de journal ERP

Le journal porte sa propre référence : chaque ligne donne à la fois l'ERP d'avant
comptage et le compté.

```mermaid
flowchart TD
    A(["Ligne de journal ERP<br/>article · entrepôt · emplacement · étiquette"]) --> B["ERP = avant comptage<br/>Qté Comptée = physique relevé"]
    B --> C["écart de ligne<br/>= Qté Comptée − ERP"]

    C --> D{"Écart nul ?"}
    D -->|oui| E["Rien à dire<br/>21 373 lignes sur l'export du 13 juin"]
    D -->|non| F{"Arrivée ou départ ?"}

    F -->|"ERP 0, compté > 0<br/>18 696 lignes"| G["CAS A · la pièce est ici<br/>sa localisation ERP était fausse"]
    F -->|"ERP > 0, compté 0<br/>17 971 lignes"| H["CAS B · la pièce n'est pas là"]

    H --> I{"Comptée dans un autre<br/>journal encore ouvert ?"}
    I -->|oui| J["CAS B.1 · absente d'ici,<br/>présente là-bas"]
    I -->|non| K["CAS B.2 · affectée au tampon<br/>entrepôt INV · emplacement 01"]

    L(["Un écart de ligne n'est pas une anomalie :<br/>un moins ici et un plus là-bas,<br/>c'est un déplacement"]) -.- C

    classDef erp fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef res fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    class A,B,C,D,F,I erp
    class E,G,H,J,K,L res
```

---

## 2. Le périmètre d'un journal se déclare

Un journal appartient à un seul entrepôt mais couvre plusieurs emplacements — 48
sur 73 dans l'export, jusqu'à 54 pour l'un d'eux. Et les emplacements de ses
lignes ne suffisent pas à dire lesquels il couvre : certaines lignes ne sont là
que pour matérialiser un déplacement.

```mermaid
flowchart TD
    A(["Nouveau journal importé"]) --> B["Emplacements présents<br/>dans ses lignes"]
    B --> C["− ceux du tampon INV / 01"]
    C --> D["− ceux déjà alloués<br/>à un autre journal"]
    D --> E{"L'utilisateur sélectionne<br/>le ou les emplacements du journal"}

    E --> F["Lignes DANS le périmètre"]
    E --> G["Lignes HORS périmètre"]

    F --> F1["Qté Comptée → comptage de l'emplacement"]
    F --> F2["ERP → référence de l'emplacement"]

    G --> G1["Ne comptent pas ici :<br/>elles appartiennent à un autre emplacement"]
    G --> G2["Conservées et signalées,<br/>jamais supprimées"]

    H["Périmètre non déclaré<br/>= rien n'est calculable"] --> HX["Import bloquant"]
    E -.-> H

    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef dec fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class A,B,C,D,F,G,F1,F2,G1,G2 nouveau
    class E dec
    class H,HX bloc
```

---

## 3. Le processus actuel

```mermaid
flowchart TD
    A([Créer la campagne]) --> B[PRÉPARATION<br/>seuils, articles, nomenclatures]
    B --> C[Créer les zones GENERIQUE<br/>et imprimer les feuilles]
    C --> D{Passer en comptage ?}
    D -->|oui| E[COMPTAGE]

    E --> F[Charger l'ERP général]
    F --> G[/Référentiel des emplacements<br/>déduit de l'ERP/]
    G --> H[/Un journal PENDING<br/>par emplacement actif/]
    H --> I[Geler l'ERP<br/>= la référence de la campagne]

    I --> J[Compter]
    J --> J1[Emplacements étiquetés<br/>journaux INVE]
    J --> J2[Emplacements vrac<br/>journaux INVV]
    J --> J3[GENERIQUE<br/>feuilles, 2 passages, arbitrage]

    J1 --> K[Poster les journaux]
    J2 --> K
    J3 --> K3[Clore les zones]

    K --> KR[/L'ERP se réaligne sur le compté<br/>introuvables → tampon INV / 01/]

    KR --> L{Contrôles de passage<br/>en analyse}
    K3 --> L
    L -->|"ERP non gelé · journaux non postés · zones non closes"| LX[Transition refusée]
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
tout comptage, et les journaux naissent du chargement (`F → H`), un par
emplacement. Il n'existe aucun point de ce parcours où compter un emplacement
avant la photo générale — ni aucune place pour un journal ERP qui en couvre
cinquante.

---

## 4. Les deux quantités dans le temps

```mermaid
flowchart LR
    subgraph T0 ["J-2 · le journal avancé"]
        A2["compté@T0<br/>colonne Qté Comptée"]
    end

    subgraph POST ["J-2 · postage ERP"]
        A5["L'ERP se réaligne<br/>sur compté@T0"]
    end

    subgraph JJ ["Jour J · chargement général"]
        B1["ERP@J<br/>la référence, et la seule"]
    end

    subgraph R ["Ce qu'on en tire"]
        C1["ÉCART D'INVENTAIRE<br/>compté@T0 − ERP@J<br/>≈ 0, et c'est juste"]
        C2["DÉRIVE<br/>ERP@J − compté@T0<br/>affichage seul"]
    end

    A2 --> A5
    A5 --> B1
    A2 --> C1
    B1 --> C1
    A2 --> C2
    B1 --> C2

    classDef q fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef erp fill:#e5e7eb,stroke:#6b7280,color:#374151
    classDef r fill:#dcfce7,stroke:#15803d,color:#14532d
    class A2,B1 q
    class A5 erp
    class C1,C2 r
```

**Le point clé : l'ERP se réaligne avant la photo.** Poster le journal du
précomptage inscrit sa correction dans l'ERP, et la photo du jour J la reprend.
La référence est donc unique pour tout le monde :

| Emplacement | Référence |
|---|---|
| Ordinaire | `ERP@J` |
| Précompté et scellé | `ERP@J` — la même, parce que la photo a déjà intégré son précomptage |

**Conséquence, et il faut l'annoncer** : l'écart d'un emplacement précompté vaut
zéro dans le cas nominal, et c'est exact. Le résultat de son inventaire n'est
pas perdu — il a été enregistré plus tôt, dans l'ERP, avant la campagne. Plus on
précompte, meilleur est l'IRA de la campagne, et c'est la vérité de ce qu'elle
mesure : ce qui restait à corriger le jour J.

C'est aussi pourquoi **rien ne s'affiche avant le gel** : un écart a besoin
d'une référence, et elle arrive le jour J.

---

## 5. Le processus avec comptages avancés

```mermaid
flowchart TD
    A([Créer la campagne]) --> B[PRÉPARATION]

    B --> E{Des emplacements<br/>à précompter ?}
    E -->|non| C

    E -->|oui| L1[Compter et POSTER le journal<br/>dans l'ERP]
    L1 --> L2[Exécuter le notebook<br/>sur la fenêtre du comptage]
    L2 --> L3{Sélectionner le périmètre<br/>du journal}
    L3 --> L4[/"Déclarer SCELLE : le comptage du journal est posé,<br/>aucune référence n'est écrite"/]
    L4 --> L8[Baliser physiquement]
    L8 --> E

    C{Passer en comptage ?} -->|oui| D[COMPTAGE<br/>référentiels gelés]
    D --> GEN[Ouvrir le comptage général]
    GEN --> G2[Charger l'ERP général]

    G2 --> G3[/"Référence remplacée PARTOUT,<br/>emplacements scellés compris"/]
    G3 --> G3B[Désactiver INV / 01<br/>lignes conservées]
    G3B --> G4["dérive = ERP@J − compté@T0"]
    G4 --> G5[Geler l'ERP]
    G5 --> G6[/"Les écarts et les indicateurs<br/>s'affichent — pas avant"/]

    G6 --> CNT[Compter le reste]
    CNT --> IMP[Réexécuter le notebook<br/>très régulièrement]
    IMP --> IMP2[/Remplacement par numéro de journal<br/>écarts recalculés · heure affichée/]
    IMP2 --> DRIFT[Regarder dérives et étiquettes<br/>dans les Contrôles · voir §6]
    DRIFT --> CNT2

    CNT2[Clore les zones<br/>vérifier que tout est posté] --> T{Contrôles de passage<br/>en analyse}
    T -->|"ERP non gelé · journaux non postés<br/>zones non closes"| TX[Transition refusée]
    TX --> CNT2
    T -->|tout est vert| AN[ANALYSE]

    AN --> V[Écart = physique − ERP@J<br/>une seule référence, une seule date]
    V --> W[Ajustements, causes,<br/>backflush, analyse IA]
    W --> Y{Écarts matériels<br/>tous expliqués ?}
    Y -->|non| YX[Clôture refusée] --> W
    Y -->|oui| Z[Publier l'archive Delta<br/>+ dérives + périmètres]
    Z --> ZZ([CLÔTURÉE])

    classDef existant fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef bloc fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef dec fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef horsapp fill:#e5e7eb,stroke:#6b7280,color:#374151
    class A,B,C,D,CNT,CNT2,T,AN,W,Y,Z,ZZ existant
    class E,L2,L4,L5,L7,GEN,G1,G1W,G2,G3,G3B,G4,G5,IMP,IMP2,DRIFT,DISP,V nouveau
    class L3,L6 dec
    class TX,YX,L6X bloc
    class L1,L8 horsapp
```

---

## 6. La dérive : une quantité, aucune issue

```mermaid
flowchart TD
    A[/"Emplacement scellé<br/>compté@T0 · ERP@J"/] --> B["dérive = ERP@J − compté@T0"]
    B --> C{"Nulle ?"}
    C -->|oui| OK[Conservée en base,<br/>non affichée : c'est le cas normal]
    C -->|non| D[/"Affichée dans Contrôles → Dérives"/]

    D --> D1["Ce qui a bougé entre le précomptage<br/>et le jour J : sortie, réception,<br/>correction saisie entre-temps"]
    D1 --> D2["Aucune action requise<br/>Aucun constat bloquant"]
    D2 --> Z([Regardée, ou pas])

    D2 -.->|"si on veut recompter"| U["Desceller le journal<br/>motif obligatoire"]
    U --> U1["L'emplacement rejoint<br/>le comptage du jour J"]

    classDef nouveau fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef dec fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef gris fill:#e5e7eb,stroke:#6b7280,color:#374151
    class A,B,D,D1,D2,OK,Z nouveau
    class C dec
    class U,U1 gris
```

Aucune issue, là où il y en avait deux — *conserver le comptage avancé* et
*recompter le jour J* — et quatre avant elles. Les quatre reposaient sur la même
prémisse : qu'un emplacement scellé porte sa propre référence, et qu'il faille
arbitrer entre deux vérités. Cette prémisse est tombée avec `ERP@T0`.

Ce qui reste existe toujours, à sa place, et se déclenche pour ses raisons
propres : le **descellement** pour un emplacement qu'on veut recompter, la
colonne d'**ajustement** pour un mouvement réel.

---

## 7. Ce que la dérive ne voit pas, et ce que l'étiquette rattrape

La dérive se calcule entre deux lectures de l'ERP : elle ne voit que ce que l'ERP
a appris.

```mermaid
flowchart TD
    A(["Pièce comptée à T0<br/>dans l'emplacement scellé A"]) --> B["Elle sort de A physiquement<br/>aucune transaction ERP"]
    B --> C["ERP@J de A l'inclut toujours<br/>DÉRIVE = 0"]
    C --> D{"Son étiquette est-elle comptée<br/>dans un autre journal ?"}

    D -->|oui| E["EARLY_LABEL_COUNTED_ELSEWHERE<br/>emplacement scellé et nouvel emplacement désignés"]
    E --> F(["Rattrapé par le contrôle étiquette<br/>433 étiquettes sur 39 558 dans l'export"])

    D -->|non| G["Rien ne la voit"]
    G --> H["L'ERP la croit en A,<br/>la campagne le confirme"]
    H --> I["La perte n'apparaîtra<br/>qu'à l'inventaire suivant"]
    I --> J(["Comptée le jour J, elle serait partie au tampon<br/>et la perte aurait été constatée"])

    classDef fait fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef vu fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef dec fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef risque fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class A,B,C fait
    class D dec
    class E,F vu
    class G,H,I,J risque
```

C'est le seul contrôle du dispositif qui descende au grain de l'étiquette, et
c'est ce qui justifie de remonter `SILlabelID` dans l'application. Reste la
branche de droite : le précomptage échange une part de pouvoir de détection
contre de la charge en moins, et seule la durée de la fenêtre T0 → J en règle
l'ampleur.

---

## 8. `INV / 01`, le tampon

```mermaid
flowchart TD
    A(["Pièce introuvable<br/>lors d'un comptage"]) --> B["L'ERP produit deux lignes"]
    B --> C["Départ · emplacement réel<br/>ERP 1, compté 0"]
    B --> D["Arrivée · INV / 01<br/>ERP 0, compté 1"]

    C --> E{"INV / 01 est-il<br/>dans le périmètre ?"}
    D --> E

    E -->|"oui, par défaut"| F["Les deux lignes se compensent<br/>à l'échelle de l'article"]
    F --> G["La perte disparaît"]

    E -->|"non, désactivé"| H["Seule la ligne de départ subsiste"]
    H --> I["La perte redevient un écart visible"]

    J["INV / 01 est virtuel :<br/>aucun emplacement physique,<br/>aucun journal créé par l'ERP"] -.- A
    K["Le journal est créé par l'app au chargement<br/>général, puis DÉSACTIVÉ par l'exploitant.<br/>Les lignes restent, pour la traçabilité."] -.- E
    L["Jamais précompté, jamais scellé,<br/>aucun contrôle de dérive"] -.- E

    classDef fait fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef dec fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef mauvais fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef bon fill:#dcfce7,stroke:#15803d,color:#14532d
    class A,B,C,D,J,K,L fait
    class E dec
    class F,G mauvais
    class H,I bon
```

L'ERP concentre les pertes dans un emplacement, l'application les laisse là où
elles ont été constatées. La désactivation est ce qui fait le pont entre les deux
représentations — d'où son caractère obligatoire.

---

## 9. Cycle de vie d'un journal de comptage

```mermaid
stateDiagram-v2
    [*] --> IMPORTE : le notebook rapporte le journal

    IMPORTE --> PERIMETRE_A_DECLARER : entrepôts et emplacements proposés
    PERIMETRE_A_DECLARER --> OUVERT : l'utilisateur sélectionne

    OUVERT --> OUVERT : réimport, remplacement par numéro de journal
    OUVERT --> POSTE : posté dans l'ERP
    PERIMETRE_A_DECLARER --> SCELLE : déclarer le périmètre SCELLE
    SCELLE --> SCELLE : réimport, référence recalculée
    SCELLE --> PERIMETRE_A_DECLARER : desceller, motif obligatoire

    POSTE --> [*] : passage en ANALYSE
    SCELLE --> [*] : passage en ANALYSE

    note right of OUVERT
        Un journal ouvert entre dans la
        vision globale de la campagne :
        résultat provisoire, mais compté.
    end note

    note right of SCELLE
        On ne scelle qu'un journal posté
        dans l'ERP : le réalignement est
        alors acquis par construction.
        Premier gel par objet du produit ;
        mutability_of garde le dernier mot
        pour interdire, le scellement ne
        fait que restreindre davantage.
    end note
```
