-- =============================================================================
-- 017 — Le statut de feuille disparaît, la zone porte sa clôture
-- -----------------------------------------------------------------------------
-- Une feuille de comptage avait quatre états — en attente, comptage en cours,
-- encodage en cours, terminée — qu'il fallait faire avancer à la main, une par
-- une, deux fois par zone. Aucune écriture n'en dépendait : le papier partait au
-- comptage que le bouton ait été cliqué ou non, et les quantités s'enregistraient
-- dans tous les cas. Quatre clics par zone pour tenir à jour une donnée dont la
-- seule lecture était « cette zone est-elle finie ? ».
--
-- Cette question-là reste, et elle appartient à la zone. Elle se pose une fois,
-- et sa réponse est ici : `closed_at`.
--
-- **Pourquoi une décision et pas une déduction.** Les deux autres états d'une
-- zone — rien de compté, comptage en cours — se déduisent des quantités et ne
-- peuvent donc pas mentir. « Terminée » ne peut pas se déduire de la même façon :
-- dérivé de « toutes les lignes comptées », il laisserait ouverte pour toujours
-- une zone portant une ligne qu'on ne peut légitimement pas compter — l'article
-- a disparu, l'emplacement est inaccessible — et avec elle le passage de la
-- campagne en analyse, qui exige que toutes les zones soient terminées.
--
-- **La reprise des campagnes en cours.** Une zone dont les feuilles étaient
-- toutes terminées devient une zone close, à la date de la dernière feuille
-- terminée. Les autres redeviennent ouvertes, ce qui est exact : leur comptage
-- n'était pas fini.
--
-- La colonne `count_sheet.status` est supprimée. La garder aurait laissé en base
-- une donnée que plus rien ne met à jour, et qui afficherait « en attente » sur
-- une feuille pleine de quantités — l'audit conserve l'historique de ses
-- transitions passées.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE zone
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS closed_by TEXT NOT NULL DEFAULT '';

COMMENT ON COLUMN zone.closed_at IS
    'Quand la zone a été déclarée terminée. NULL = comptage encore ouvert.';

-- Reprise : une zone dont toutes les feuilles requises étaient terminées était,
-- de fait, close. On lui donne la date de la dernière feuille terminée plutôt
-- que now(), pour ne pas antidater la clôture au jour de la migration.
UPDATE zone z
   SET closed_at = COALESCE(s.last_done, now()),
       closed_by = 'migration-017'
  FROM (
        SELECT zone_id,
               count(*) FILTER (WHERE status = 'DONE') AS done,
               max(updated_at) FILTER (WHERE status = 'DONE') AS last_done
          FROM count_sheet
      GROUP BY zone_id
       ) s
 WHERE s.zone_id = z.id
   AND z.closed_at IS NULL
   AND s.done >= z.passes;

ALTER TABLE count_sheet DROP COLUMN IF EXISTS status;
