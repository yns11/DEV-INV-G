-- =============================================================================
-- 023 — Ce que le compteur a écrit, à côté de ce que la machine en a conclu
-- -----------------------------------------------------------------------------
-- Sur le papier, une quantité n'est pas toujours un nombre. Devant trois
-- palettes de quarante-huit et un fond de bac de sept, le compteur écrit :
--
--     3*48+7
--
-- C'est la bonne façon de compter : le calcul reste devant les yeux de qui
-- relira, alors qu'un « 151 » nu ne se recompte pas. L'application ne savait
-- lire qu'un nombre — la saisie refusait la ligne, le scan rendait une case
-- vide sur une feuille pourtant ni vierge ni douteuse.
--
-- Le réglage « Accepter des formules dans les comptages » ouvre cette lecture.
-- Cette colonne est ce qui l'empêche d'être une simple commodité de saisie :
-- **elle garde le texte d'origine**. Sans elle, une quantité écrite « 3*48+7 »
-- serait indistinguable d'une quantité tapée « 151 », et six mois plus tard un
-- contrôle ne pourrait plus relire ce que le compteur a vraiment noté — ni
-- s'apercevoir qu'une palette n'en contenait que quarante-six.
--
-- Vide pour toute quantité déjà saisie comme un nombre : garder « 151 » comme
-- « formule de 151 » n'apprendrait rien et remplirait une colonne de doublons.
-- C'est donc l'écriture, pas le résultat, qui décide du remplissage.
--
-- Pas de colonne symétrique sur `count_journal_line` : les journaux sont
-- alimentés par l'ERP et par une saisie assistée à l'écran, jamais recopiés
-- d'un papier manuscrit. La question ne s'y pose pas.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE count_sheet_line
    ADD COLUMN IF NOT EXISTS qty_formula TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN count_sheet_line.qty_formula IS
    'L''opération écrite sur la feuille quand la quantité en était une '
    '(« 3*48+7 »), vide quand un nombre a été saisi directement. Le résultat '
    'est dans qty_manual ou qty_imported, comme pour toute autre quantité.';
