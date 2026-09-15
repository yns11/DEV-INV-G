-- 027 — La feuille de comptage retrouve sa mise en page
--
-- Les feuilles Excel qu'on remplace ne sont pas des listes : ce sont des
-- **documents**. « Stock physique B6EST », trois articles, une ligne vide,
-- « Stock physique B15 », les mêmes trois articles, une ligne vide, « Stock
-- physique chez Maldaner »… Le même article revient sous trois intertitres, et
-- c'est ce découpage qui dit au compteur *où aller*.
--
-- L'application ne savait poser qu'une suite d'articles par section. Le
-- découpage se perdait à l'impression, et le même article dans deux
-- sous-sections était refusé comme un doublon — alors que ce sont deux
-- comptages, à deux endroits.
--
-- Trois colonnes et une clé changent donc :
--
-- * `line_kind` distingue un article d'un **intertitre** et d'une **ligne
--   vide**. Les deux derniers ne portent aucune quantité : ce sont des objets
--   de mise en page, rangés dans la même table parce que leur place dans la
--   feuille est exactement ce qu'il faut conserver ;
-- * `label` porte le texte d'un intertitre ;
-- * `subsection` porte, sur une ligne d'article, l'intertitre sous lequel elle
--   se trouve. Dupliqué depuis l'intertitre plutôt que déduit de l'ordre : la
--   clé d'unicité doit pouvoir se calculer sur une ligne seule, à l'import,
--   où l'ordre du fichier n'a pas encore de sens.
--
-- `section_labels` sur la zone porte les en-têtes personnalisés. Sur la zone et
-- non sur la feuille : les deux passages d'une zone sont le même document
-- imprimé deux fois, et les voir diverger n'aurait aucun sens.

ALTER TABLE count_sheet_line
    ADD COLUMN IF NOT EXISTS line_kind  TEXT NOT NULL DEFAULT 'ARTICLE'
        CHECK (line_kind IN ('ARTICLE','SUBSECTION','SPACER')),
    ADD COLUMN IF NOT EXISTS label      TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS subsection TEXT NOT NULL DEFAULT '';

-- Un intertitre et une ligne vide n'ont pas d'article. La colonne reste NOT
-- NULL — c'est le cas de toutes les autres — et vaut la chaîne vide, ce que le
-- modèle traite déjà comme « pas d'article ».

ALTER TABLE zone
    ADD COLUMN IF NOT EXISTS section_labels JSONB NOT NULL DEFAULT '{}'::jsonb;

-- L'index de lecture d'une feuille porte déjà sur (sheet_id, display_order) :
-- c'est exactement l'ordre du document, intertitres et lignes vides compris.
-- Rien à ajouter.
