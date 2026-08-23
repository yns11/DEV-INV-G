# Top 20 des améliorations à apporter

> Revue critique de la solution livrée, du point de vue d'un key-user expert en
> gestion de stock et d'un architecte data senior. Chaque point indique ce qui
> manque **aujourd'hui**, pourquoi cela compte en euros ou en risque, et comment
> le traiter.
>
> Classement par **valeur métier décroissante à effort maîtrisé**. Les cinq
> premiers points sont, à mon avis, ceux qui doivent partir en premier sprint.

Légende — **Effort** : S (< 1 semaine), M (2–4 semaines), L (> 1 mois).
**Impact** : ⭐ à ⭐⭐⭐⭐⭐.

---

## Bloc 1 — Fermer la boucle avec l'ERP (le chaînon encore manuel)

### 1. Écriture directe des journaux dans D365 (OData / Dataverse)
**Impact ⭐⭐⭐⭐⭐ · Effort M · Risque si non fait : élevé**

Aujourd'hui, l'application produit un fichier au format d'import ERP — un
progrès considérable sur le copier/coller, mais il reste un geste humain entre
le calcul et le système maître. Tant que ce geste existe, une liasse peut être
importée deux fois, ou pas du tout.

**À faire.** Un connecteur d'écriture `InventJournalTrans` via l'API OData de
D365, avec :
- une **clé d'idempotence** par journal (`campaign_code:warehouse:location:run_id`)
  pour qu'un rejeu ne double jamais les lignes ;
- un statut `SENT / ACKNOWLEDGED / REJECTED` sur `count_journal`, avec le message
  d'erreur ERP stocké ;
- un rapprochement automatique : le numéro de journal ERP revient et se fixe sur
  la ligne, ce qui ferme la boucle de traçabilité.

**Bénéfice.** Le dernier point de rupture disparaît. Le journal GENERIQUE de la
campagne de juin comptait 244 lignes recopiées à la main.

---

### 2. Lecture automatique du stock ERP et des mouvements (pull ERP)
**Impact ⭐⭐⭐⭐⭐ · Effort M**

Le snapshot et les mouvements sont encore chargés depuis des exports faits à la
main. Le jour J, une personne passe une heure à produire trois fichiers, et rien
ne garantit que le snapshot a été pris **avant** le premier scan.

**À faire.** Une tâche Lakeflow qui, à l'heure programmée de la campagne :
1. déclenche la requête OData « stock physique par emplacement » ;
2. horodate le résultat **à la seconde** ;
3. le charge et le gèle automatiquement ;
4. refuse de démarrer si un journal de comptage a déjà été posté.

Puis un rafraîchissement des mouvements toutes les 15 minutes pendant la phase
d'analyse, au lieu d'un rechargement manuel.

**Bénéfice.** Le snapshot devient une donnée horodatée et opposable, pas un
fichier qu'on espère avoir pris au bon moment. C'est la condition d'un
inventaire auditable.

---

### 3. Comptage mobile : PWA hors ligne avec scan de code-barres
**Impact ⭐⭐⭐⭐⭐ · Effort L**

Le papier reste au cœur du processus GENERIQUE. Il génère des ruptures
(transcription, perte de feuille, délai d'encodage) et une extraction IA qui,
si bonne soit-elle, ne fait que réparer un problème qu'on pourrait supprimer.

**À faire.** Une PWA installable sur les terminaux durcis déjà présents en
atelier :
- **fonctionnement hors ligne** avec file de synchronisation (le réseau Wi-Fi
  est mauvais en bord de ligne — c'est non négociable) ;
- **scan du code-barres article** : la référence n'est plus jamais transcrite ;
- saisie numérique avec **contrôle immédiat** (unité, ordre de grandeur par
  rapport au stock ERP, article hors périmètre) ;
- le compteur voit la liste de sa zone, coche au fur et à mesure, et ne peut pas
  clore une feuille en laissant une ligne ambiguë.

**Bénéfice.** Suppression de la moitié des causes « écarts de comptage » du
référentiel de causes (codes 5 et 6). Sur juin 2026, la ligne « Physical count
deviations » pesait 1 % de fiabilité perdue.

**Attention.** C'est le point le plus lourd de la liste, et le seul qui demande
un vrai accompagnement du terrain. Ne le lancez pas avant que les points 1 et 2
soient acquis.

---

### 4. Inventaire tournant (cycle counting) piloté par le risque
**Impact ⭐⭐⭐⭐⭐ · Effort M**

L'application traite l'inventaire général. Or l'état de l'art WMS est clair :
**l'inventaire général annuel est le pire moyen de fiabiliser un stock.** Il
mobilise tout le site pendant deux jours, deux fois par an, et laisse dériver
les enregistrements entre les deux.

**À faire.** Un mode « campagne tournante » :
- fréquence de comptage dérivée du segment ABC/XYZ **déjà calculé** par la
  solution (AZ : mensuel, AX : semestriel, C : annuel) ;
- génération automatique d'une liste de comptage quotidienne de taille bornée
  (« 20 emplacements par jour ») ;
- suivi de l'IRA dans le temps, par segment, avec alerte sur dérive ;
- déclenchement d'un comptage exceptionnel sur évènement : stock négatif,
  écart de picking, retour client anormal.

**Bénéfice.** C'est le levier le plus puissant de la liste. Les sites qui
passent au comptage tournant piloté par le risque atteignent 99 %+ d'IRA et
finissent par supprimer l'inventaire général — ou du moins par le transformer en
formalité.

---

### 5. Réconciliation par identité comptable entre deux campagnes
**Impact ⭐⭐⭐⭐ · Effort S**

La comparaison inter-campagnes existe (`compare_campaigns`) et vérifie
`stock_livre_now == stock_livre_avant + mouvements`. Mais elle n'est ni
automatisée, ni exploitée dans un écran dédié.

**À faire.** Un rapport permanent qui, pour chaque article :
- calcule la dérive `book_now − (book_then + Σ mouvements)` ;
- l'attribue quand c'est possible (backflush, rebut non déclaré, réception non
  saisie) en croisant avec les types de mouvements ERP ;
- signale les articles dont la dérive est **récurrente et de même signe** — la
  vue `v_variance_recurrence` produit déjà cette information, il ne manque que
  l'écran et l'alerte.

**Bénéfice.** Un écart récurrent de même signe est une **fuite structurelle**,
pas un accident de comptage. Sur juin 2026, la cause n°1 identifiée était
« backflush related issues » : exactement ce que cette réconciliation détecte
en continu plutôt qu'une fois par semestre.

---

## Bloc 2 — Fiabilité et gouvernance de la donnée

### 6. Rôles et séparation des tâches
**Impact ⭐⭐⭐⭐ · Effort S**

Aujourd'hui, tout utilisateur ayant accès à l'application peut tout faire :
créer une campagne, corriger un comptage, arbitrer, clôturer. Le journal d'audit
dit *qui* a fait quoi, mais rien n'empêche la personne qui a compté d'arbitrer
son propre comptage.

**À faire.** Quatre rôles adossés aux groupes SCIM du workspace :

| Rôle | Peut |
|---|---|
| Compteur | Saisir et encoder ses feuilles |
| Gestionnaire d'inventaire | Tout le processus, sauf clôturer |
| Contrôleur / finance | Lecture + affectation de causes + clôture |
| Administrateur | Configuration, seuils, référentiels |

Avec une règle de séparation : **l'arbitre d'une zone ne peut pas être l'encodeur
de la feuille arbitrée.**

**Bénéfice.** C'est une exigence de contrôle interne classique (SOX, ISO 9001)
que les commissaires aux comptes demandent sur un inventaire physique.

---

### 7. Signature électronique des feuilles et des arbitrages
**Impact ⭐⭐⭐ · Effort S**

La feuille papier porte un bloc signature ; la version numérique n'a pas
d'équivalent opposable. Or c'est le papier qui reste la preuve légale.

**À faire.** À la validation d'une feuille ou d'un arbitrage, enregistrer une
attestation signée : identité, horodatage, **empreinte SHA-256 du contenu
validé**, et lien vers le scan archivé dans le volume UC. Toute modification
ultérieure invalide l'empreinte et l'affiche comme telle.

**Bénéfice.** Le dossier numérique devient auto-portant. Le scan papier reste
l'archive, pas la preuve de premier rang.

---

### 8. Détection de doublons d'articles par similarité
**Impact ⭐⭐⭐⭐ · Effort M**

La normalisation en majuscules a déjà fusionné 5 doublons de casse sur les 478
articles reconstitués des fichiers réels. Mais les vrais doublons du référentiel
ERP — deux références distinctes désignant la même pièce — restent invisibles et
produisent des écarts symétriques permanents (un manquant sur l'une, un excédent
sur l'autre).

**À faire.**
- Similarité sur la désignation (embeddings via Vector Search sur Databricks, ou
  simple TF-IDF + distance cosinus pour commencer) ;
- **corrélation des écarts** : deux articles dont les écarts sont
  systématiquement opposés en quantité et proches en valeur sont très
  probablement le même objet physique ;
- un écran « doublons suspectés » avec le montant en jeu.

**Bénéfice.** Le bilan de juin cite explicitement une « confusion probable avec
M3G1 et/ou M4 » sur `mass-00040923`. Ce genre de confusion est détectable
automatiquement.

---

### 9. Réconciliation des unités de mesure
**Impact ⭐⭐⭐ · Effort S**

Le contrôle `UNIT_MISMATCH` signale une divergence d'unité, mais ne la
**convertit** pas. Sur la campagne de juin, on trouve de la colle en `Kg` sur la
feuille et en `KG` au référentiel, et des grammes dans les mouvements
(`-8,67 g`) face à des kilogrammes au stock.

**À faire.** Un référentiel de conversion (`g ↔ kg`, `m ↔ mm`, `PCE ↔ conditionnement`)
avec :
- conversion automatique et **tracée** à l'import, la valeur d'origine restant
  visible ;
- refus explicite quand aucune conversion n'est définie, plutôt qu'une
  comparaison de nombres sans dimension.

**Bénéfice.** Une erreur d'unité sur un article coûteux produit un écart d'un
facteur 1 000. C'est peu fréquent et très cher.

---

### 10. Contrôle de plausibilité en temps réel à la saisie
**Impact ⭐⭐⭐⭐ · Effort S**

Les contrôles s'exécutent après coup. Un compteur qui saisit 9 500 au lieu de
950 l'apprend le lendemain, quand la zone est rangée et l'équipe partie.

**À faire.** Au moment de la saisie ou de l'extraction :
- comparaison à l'ordre de grandeur du stock ERP de l'emplacement ;
- comparaison à l'historique de l'article sur les campagnes précédentes ;
- alerte immédiate « cette quantité est 10× supérieure à ce que l'ERP attend —
  confirmer ? » avec obligation de commenter au-delà d'un seuil.

**Bénéfice.** Corriger à la source coûte trente secondes ; corriger après coup
coûte un recomptage complet de la zone.

---

## Bloc 3 — Analyse et pilotage

### 11. Tableau de bord AI/BI et modèle sémantique gouverné
**Impact ⭐⭐⭐⭐ · Effort S**

Les vues Delta existent (`v_variance`, `v_campaign_kpi`, `v_variance_recurrence`,
`v_wip_contribution`) mais rien ne les expose au-delà de l'application. La
direction et le contrôle de gestion n'ont pas de vue autonome.

**À faire.**
- Un **Metric View** Unity Catalog qui fige les définitions de fiabilité nette,
  brute et d'IRA — pour que « la fiabilité » veuille dire la même chose dans
  l'app, dans un tableau de bord et dans une réponse Genie ;
- un tableau de bord AI/BI (Lakeview) : tendance de l'IRA, contribution par
  entrepôt et par programme, récurrence des écarts ;
- un **espace Genie** sur ces vues pour les questions en langage naturel, avec
  le SQL généré systématiquement affiché.

**Bénéfice.** Supprime la production manuelle du PowerPoint *Inventory Executive
Summary* — 30 diapositives refaites à chaque campagne.

---

### 12. Prédiction du risque d'écart avant l'inventaire
**Impact ⭐⭐⭐⭐ · Effort M**

Les analyses actuelles sont **post-hoc** : elles expliquent des écarts constatés.
Après trois ou quatre campagnes, il y aura assez d'historique pour anticiper.

**À faire.** Un modèle supervisé (gradient boosting, entraîné et suivi dans
MLflow) prédisant `P(écart matériel)` par article/emplacement à partir de :
rotation, nombre de mouvements depuis le dernier comptage, part de WIP,
historique d'écarts, ancienneté de la nomenclature, nombre d'emplacements.

Usage : concentrer l'effort de comptage là où le risque est, et **passer les
emplacements à faible risque en comptage par échantillonnage**.

**Bénéfice.** Réduction du temps d'inventaire à qualité constante. C'est aussi ce
qui rend le point 4 (inventaire tournant) réellement efficace.

---

### 13. Chiffrage de l'impact financier des causes
**Impact ⭐⭐⭐⭐ · Effort S**

L'onglet `Initiatives summary` du classeur legacy tentait ce calcul à la main,
avec des coefficients saisis en dur (`80% × 100% × contribution`). L'application
affiche la répartition par cause mais ne relie pas cause → action → euros.

**À faire.** Une table `initiative` reliant une cause à une action corrective,
son porteur, son échéance et son impact attendu ; puis un suivi de l'impact
**réalisé** en comparant la contribution de la cause d'une campagne à l'autre.

**Bénéfice.** Transforme le bilan d'inventaire en plan d'action mesuré, et
permet enfin de dire si une action corrective a fonctionné.

---

### 14. Analyse du WIP par ordre de fabrication
**Impact ⭐⭐⭐ · Effort M**

Le WIP est éclaté par nomenclature, ce qui est correct. Mais la question qui
intéresse la production est : *quels ordres de fabrication sont ouverts, à quel
stade, et avec quels composants déjà consommés ?*

**À faire.** Importer les OF ouverts et leurs consommations déclarées, puis
rapprocher le WIP compté du WIP théorique par OF. Un écart entre les deux
identifie précisément un problème de backflush — la cause n°1 du site.

**Bénéfice.** Fait passer de « on a un problème de backflush » à « l'OF 4711 a
consommé 300 stators pour 40 MEL déclarées ».

---

### 15. Valorisation multi-méthodes et impact comptable
**Impact ⭐⭐⭐ · Effort M**

Tout est valorisé au coût standard figé au snapshot. C'est le bon choix par
défaut, mais la clôture comptable demande d'autres angles.

**À faire.** Permettre PMP et coût réel en parallèle du coût standard, et
produire l'écriture de régularisation proposée (compte de variation de stock,
centre de coût, section analytique) — en lecture seule, à valider par la finance.

**Bénéfice.** Supprime le retraitement manuel entre l'inventaire et la clôture.

---

## Bloc 4 — Robustesse et exploitation

### 16. Verrouillage collaboratif et présence
**Impact ⭐⭐⭐ · Effort S**

Le verrouillage optimiste (`row_version`) protège l'intégrité mais produit une
expérience frustrante le jour J : deux encodeurs sur la même feuille découvrent
le conflit au moment d'enregistrer.

**À faire.** Un canal temps réel (Server-Sent Events, déjà compatible avec le
proxy) diffusant : qui est sur quelle feuille, quelles lignes sont en cours
d'édition, et la mise à jour des jauges d'avancement sans rechargement.

**Bénéfice.** Le jour de l'inventaire, dix personnes travaillent en parallèle.
C'est le moment où l'ergonomie collaborative compte le plus.

---

### 17. Traitements longs en tâches de fond
**Impact ⭐⭐⭐ · Effort S**

Le proxy Databricks Apps coupe à 120 secondes, sans rien écrire dans les
journaux de l'application. Aujourd'hui, les imports volumineux et les analyses
ML restent sous ce budget par construction (pagination, bornes), mais un site
plus gros ou une campagne à 500 000 lignes changerait la donne.

**À faire.** Une file de tâches persistée en Lakebase : l'API accepte, renvoie un
identifiant de tâche, et l'interface suit la progression. Les traitements
concernés : import de plus de 50 000 lignes, extraction d'un scan de plus de
10 pages, pack analytique complet, génération de la synthèse.

**Bénéfice.** Supprime la classe entière des « 504 sans explication », et permet
d'accepter des volumes arbitraires.

---

### 18. Observabilité métier et alerting
**Impact ⭐⭐⭐ · Effort S**

Les journaux JSON sont structurés, ce qui est le prérequis. Mais aucune métrique
métier n'est exposée ni surveillée.

**À faire.**
- Métriques : durée des imports, taux de rejet, taux de correction manuelle des
  extractions IA, latence des écrans, profondeur du pool Lakebase ;
- alertes Databricks SQL sur : aucun journal posté depuis 2 h **le jour J**,
  taux de rejet d'import > 5 %, confiance moyenne d'extraction < 80 % ;
- une table `system.access.audit` croisée avec le journal applicatif pour les
  revues de contrôle interne.

**Bénéfice.** On sait que l'inventaire dérape pendant qu'il dérape, pas le
lendemain.

---

### 19. Boucle d'apprentissage sur l'extraction des scans
**Impact ⭐⭐⭐ · Effort M**

L'extraction IA fonctionne et sa confiance est affichée, mais les corrections
humaines ne servent à rien : elles ne remontent nulle part.

**À faire.**
- Constituer un jeu d'évaluation à partir des scans réels et des valeurs
  finalement retenues ;
- suivre dans MLflow le taux d'exactitude par zone, par type d'écriture et par
  version de modèle ;
- ajuster le prompt (et, à terme, considérer un modèle affiné) sur cette base ;
- **calibrer** le seuil de confiance sur des données réelles plutôt que sur la
  valeur de 75 % choisie a priori.

**Bénéfice.** Sans mesure, on ne sait pas si l'extraction s'améliore ou se
dégrade d'une version de modèle à l'autre. Avec, on peut décider de relever le
seuil de confiance et réduire la relecture humaine.

---

### 20. Internationalisation et accessibilité
**Impact ⭐⭐ · Effort S**

L'interface est intégralement en français, ce qui est le bon choix pour ce site.
Mais les *Executive Summary* sont rédigés en anglais pour le groupe, et les
chaînes sont aujourd'hui codées en dur dans les composants.

**À faire.**
- Externaliser les chaînes (`react-i18next`), avec le français comme locale par
  défaut et l'anglais pour le reporting groupe ;
- audit d'accessibilité complet : navigation clavier de la grille éditable,
  annonces des changements de statut aux lecteurs d'écran, vérification des
  contrastes en thème sombre ;
- vérifier que la couleur n'est jamais **seule** porteuse d'information — les
  badges portent déjà un texte, mais les barres d'écart reposent sur la teinte.

**Bénéfice.** Permet le déploiement sur les autres sites du groupe, et lève un
risque de conformité (RGAA / EN 301 549) sur un outil interne.

---

## Ce que je ne recommande pas

Par honnêteté, trois choses qu'on va vous proposer et qui seraient, à mon avis,
des erreurs :

**Un agent IA autonome qui poste les journaux.** La tentation est réelle et la
technologie le permet. Mais un inventaire physique est un acte comptable
opposable : la valeur de l'outil vient de ce qu'une décision est attribuable à
une personne. Gardez l'IA en proposition.

**Migrer les écritures vers Delta pour « tout unifier ».** Delta n'est pas fait
pour deux cents changements de statut par heure et dix éditeurs concurrents. La
séparation Lakebase / Delta n'est pas une complexité accidentelle, c'est le bon
choix d'outil pour deux problèmes différents.

**Rendre les seuils configurables par utilisateur.** Cela paraît souple ; en
pratique, deux personnes obtiennent deux listes d'exceptions différentes sur la
même campagne, et la discussion porte sur les seuils au lieu des écarts. Les
seuils sont une décision de site, figée par campagne. C'est déjà le cas — ne
revenez pas dessus.

---

## Séquencement proposé

| Sprint | Contenu | Pourquoi dans cet ordre |
|---|---|---|
| **1** (4 sem.) | 1, 2, 6 | Fermer la boucle ERP et poser la gouvernance : tout le reste en dépend |
| **2** (4 sem.) | 5, 10, 11, 13 | Exploiter la donnée déjà collectée — coût faible, valeur immédiate |
| **3** (6 sem.) | 4, 8, 9, 18 | Passer au pilotage par le risque et fiabiliser le référentiel |
| **4** (8 sem.) | 3, 12, 19 | Le mobile et la prédiction, une fois l'historique constitué |
| **Continu** | 7, 14, 15, 16, 17, 20 | À insérer selon les contraintes d'audit, de finance et de déploiement groupe |

Le point 3 (comptage mobile) est délibérément placé en sprint 4 : c'est le plus
coûteux, le plus dépendant de l'adhésion du terrain, et il ne donne sa pleine
valeur qu'une fois les points 1 et 2 acquis. Le lancer en premier serait le
meilleur moyen de le rater.
