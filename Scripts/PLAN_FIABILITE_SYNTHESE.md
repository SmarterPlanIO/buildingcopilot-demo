# PLAN — Fiabilité du narratif des fiches de synthèse (copro_synthese)

> Date : 28/08/2026. Statut : PLAN — rien codé.
> Déclencheur : incident Delacour du 27-28/08 (fiche du 26/08). Le narratif d'une fiche
> affirmait « l'assemblée a approuvé sans réserve les comptes 2023 (170 215,78 €) » alors
> que le PV du 30/03/2026 montre la résolution 3 REJETÉE (2 606 tantièmes pour, 4 867
> contre, 58 abst.) ; il rattachait de plus ce vote à une AG du 16/09/2025 avec un contenu
> non vérifié, et se contredisait lui-même sur les comptes de dossiers (narratif : 25
> ouverts + 4 clos ; couche faits de la MÊME fiche : 37 dont 32 en cours + 5 clos).
> L'assistant client a correctement diagnostiqué : erreur d'INTERPRÉTATION (dispositif
> soumis au vote pris pour la décision), pas erreur de lecture.

---

## 0. Root cause — CONFIRMÉE dans le code (09_copro_synthese.py, relu le 28/08)

| Symptôme | Cause exacte dans le code |
|---|---|
| Dispositif pris pour la décision | `_pv_text_block` : les chunks des 2 PV récents sont accumulés puis **tronqués brutalement** par `"\n".join(body)[:PV_TEXT_BUDGET]` (3 500 chars/PV). Le chunking PV_AG par résolution met dispositif + décompte dans le MÊME chunk, mais cette coupe au caractère peut les séparer : Haiku reçoit « l'assemblée approuve… » sans le décompte qui suit. Même sans coupe en plein milieu, 3 500 chars ≈ les 2-4 premières résolutions d'un PV : le narratif généralise depuis un extrait très partiel, sans le savoir. |
| Vote attribué à la mauvaise AG | Le bloc concatène 2 PV (`[date] fichier` + résolutions tronquées) sans contrainte d'attribution dans le prompt : Haiku peut mélanger les AG. |
| Comptes de dossiers contradictoires | Le prompt injecte le DÉTAIL de 40 dossiers max (`_dossiers_block`) mais PAS les agrégats (`par_statut`/`par_type`, calculés dans `facts` juste au-dessus et jamais passés au prompt — le commentaire « les comptes couvrent tout » est faux). Rien n'interdit à Haiku de compter lui-même → il compte faux. |
| Rien ne détecte l'erreur | Aucun contrôle post-génération : le narratif est upserté tel quel, sans statut de fiabilité, et `PALIM_copro_overview` le sert sans mise en garde exploitable. |

Le prompt actuel a « Aucune invention » et « ne cite pas de montant que tu n'as pas vu » —
insuffisant : ici le montant ÉTAIT dans les éléments fournis ; c'est le STATUT de la phrase
(soumise au vote vs votée) qui a été inventé.

---

## P0 — Contenir (immédiat, zéro régénération, zéro deploy)

1. **Instructions clients (template + NCG + Delacour + CSG), Bloc 4** : le narratif d'une
   fiche de synthèse a le **statut de source le plus bas**. Il sert à s'orienter ; un sens
   de vote, une décision d'AG, un montant ou un comptage ne se CITENT jamais depuis lui —
   revalidation obligatoire par recherche documentaire scopée (le PV tranche). Bump des
   dates (numéro produit inchangé : doctrine d'usage, pas de contrat modifié).
2. **Signalement pilote** : consigner l'incident (copro, fiche du 26/08, PV du 30/03/2026,
   résolutions 3 et 4) comme **golden case n°1** du chantier self-learning
   (PLAN_SELF_LEARNING.md, triage F1). La réponse corrective de l'assistant client montre
   que la doctrine « le PV tranche » fonctionne en aval — c'est la fiche qu'on répare ici.

## P1 — Corriger le générateur (09_copro_synthese.py, 1 session)

1. **Jamais couper un chunk.** La résolution est l'unité atomique : suppression de la
   troncature au caractère ; le budget s'applique en CHUNKS ENTIERS (on s'arrête avant le
   chunk qui déborde). Un dispositif n'est jamais séparé de son décompte.
2. **Sélection décisionnelle, pas positionnelle.** Remplacer « les N premiers chars du PV »
   par une priorisation des résolutions : (a) chunks porteurs d'un décompte
   (regex POUR/CONTRE/tantièmes/abstention), (b) `resolution_category` décisives (comptes,
   budget, travaux, mandat de syndic, contentieux), (c) reste si budget. Objectif : le
   modèle voit des résolutions COMPLÈTES et décisives, pas le début du document.
3. **Prompt durci** (3 règles nouvelles) :
   - *Statut du dispositif* : « la phrase "l'assemblée approuve/décide…" est le texte SOUMIS
     au vote ; seul le décompte (pour/contre/abstentions) établit le résultat ; sans
     décompte visible dans les éléments, ne pas affirmer le sens du vote » ;
   - *Interdiction de compter* : « n'énonce aucun total (dossiers, documents) : les comptes
     exacts sont fournis ci-dessous » + **injection des agrégats** `par_statut`/`par_type`
     dans le prompt (ils existent déjà dans `facts`) ;
   - *Attribution* : toute décision citée est rattachée à la date d'AG de son en-tête,
     jamais à une autre.
4. **Gate post-génération avant upsert** (déterministe, zéro LLM) :
   - toute affirmation de sens de vote (approuv|adopt|rejet|vot) dans le narratif sans
     décompte présent dans le pv_block injecté → 1 régénération, puis échec = dégradé ;
   - tout nombre du narratif absent des inputs (pv_block + dossiers_block + agrégats) →
     dégradé (attrape aussi les montants inventés) ;
   - résultat stocké : colonne `narratif_statut` (`ok` / `degrade` / `skip`) dans
     `copro_synthese` (+ motif). Un narratif dégradé est tronqué à sa partie vérifiable ou
     remplacé par « synthèse indisponible — consulter les PV ».
5. **Traçabilité** : stocker les `chunk_id` injectés dans `faits` (audit : « qu'a vu le
   générateur »), ce qui aurait tranché l'hypothèse du client en 30 secondes.

## P2 — Exposition MCP (avec le deploy v12 déjà prévu pour log_feedback v2)

- `PALIM_copro_overview` : renvoyer `narratif_statut` + note machine-lisible (« narratif =
  orientation ; toute décision d'AG se revalide par recherche documentaire ») ; docstring
  durcie dans le même sens. Champ de sortie ajouté = **bump produit majeur** des
  Instructions au moment du deploy (conforme politique versioning du 27/08).
- Le harness Streamlit agentique hérite via le MCP backend unique : rien à coder côté app.

## P3 — Recette + golden cases (1/2 session)

- **Golden case n°1** : régénérer la fiche de la copro incident → la fiche ne doit plus
  affirmer l'approbation des comptes 2023, doit dater rejet (30/03/2026) et approbation
  2025 (même AG), et ses comptes de dossiers doivent égaler la couche faits.
- Tests unitaires du gate (module pur) : dispositif sans décompte → dégradé ; décompte de
  rejet présent + narratif « approuvé » → dégradé ; nombre orphelin → dégradé ; narratif
  propre → ok.
- **Batch de requalification** : régénérer les fiches des copros servies (NCG + Delacour,
  coût = ~0,01 $/fiche), publier le taux de `degrade` — c'est la mesure du problème.
- Brancher au chantier self-learning : golden case rejoué à chaque deploy.

## Backlog (hors périmètre, avec déclencheur)

- **Extraction structurée des votes** (table `resolutions` : n°, objet, décompte, résultat,
  article de majorité) : supprime la génération libre sur les votes — le narratif citerait
  des résultats CALCULÉS. Déclencheur : si le gate dégrade plus de 20 % des fiches, ou dès
  que la fiche de décision (`*-fiche-decision`) a besoin de l'historique décisionnel fiable
  en structuré (même besoin, même table).

## Ordre et effort

| Étape | Effort | Dépendance |
|---|---|---|
| P0 instructions + signalement | 1 h | aucune |
| P1 générateur + gate + tests | 1 session | aucune |
| P3 recette + batch | 1/2 session | P1 |
| P2 exposition MCP | portée par le deploy v12 | P1 |
