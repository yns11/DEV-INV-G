-- =============================================================================
-- 030 — Une zone vierge dit combien de lignes elle imprime, section par section
-- -----------------------------------------------------------------------------
-- Une feuille en saisie libre n'offrait que le bord de ligne : le nombre de
-- lignes demandé à l'impression y allait tout entier, et les deux sections
-- d'en-cours n'étaient pas imprimées du tout. Une zone qui compte des en-cours
-- sur papier vierge n'avait donc aucun moyen de le dire.
--
-- Le réglage appartient à la **zone** et non à l'impression : c'est une
-- propriété de ce qu'on va compter là-bas, pas une décision qu'on reprend à
-- chaque sortie d'imprimante. C'est aussi ce qui permet de le renseigner en
-- créant les zones — à l'unité ou par lot collé.
--
-- Le grain est le même que `section_labels`, et pour la même raison : les deux
-- passages d'une zone sont le même document imprimé deux fois, et les voir
-- diverger n'aurait aucun sens.
--
-- **Zéro ne se stocke pas.** « Cette section ne s'imprime pas » et « cette
-- section n'a rien de déclaré » sont le même état ; en garder deux écritures
-- ferait diverger deux lectures. Un objet vide est donc l'état normal, et c'est
-- celui des zones créées avant ce réglage : le nombre demandé à l'impression va
-- alors au bord de ligne, exactement comme avant.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE zone
    ADD COLUMN IF NOT EXISTS blank_rows JSONB NOT NULL DEFAULT '{}'::jsonb;
