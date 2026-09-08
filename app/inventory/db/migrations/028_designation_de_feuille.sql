-- La désignation portée par la ligne de feuille, quand elle diffère du référentiel.
--
-- Les listes qui alimentent les feuilles B06VRAC viennent des ateliers, et
-- elles nomment les pièces comme l'atelier les nomme — « CARTER AR M3 GEN2 »
-- là où le référentiel ERP dit « HOUSING REAR ». Le compteur cherche sur le
-- papier le nom qu'il connaît ; lui imprimer l'autre, c'est lui demander de
-- traduire quatre-vingts lignes à six heures du matin.
--
-- Portée **par la ligne**, jamais par l'article : le référentiel reste ce qu'il
-- est, et l'écrasement ne sort pas des feuilles. Les écarts, la consolidation,
-- les analyses et les exports continuent de nommer l'article comme l'ERP le
-- nomme — sans quoi un rapprochement avec l'ERP deviendrait illisible.
--
-- Vide est l'état normal : la ligne prend alors la désignation du référentiel.
-- Non nul et non vide veut dire « quelqu'un a voulu autre chose ici ».

ALTER TABLE count_sheet_line
    ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';
