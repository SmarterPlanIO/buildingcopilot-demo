---
name: dlc-fiche-decision
description: >-
  Instruit une décision de copropriété multi-options et produit une fiche de
  décision structurée pour le conseil syndical ou la préparation d'AG : options
  comparées et chiffrées (dont l'option de ne pas agir), historique des décisions
  d'AG liées, majorité applicable par option, proposition actionnable ou constat
  motivé de non-décidabilité. À utiliser quand l'utilisateur veut préparer ou
  arbitrer un choix : « prépare une fiche de décision », « faut-il faire /
  remplacer / engager les travaux », « compare les devis pour décider », « prépare
  le point pour le conseil syndical / l'ordre du jour de l'AG », décision du CS
  par délégation. NE PAS utiliser pour une question factuelle simple, ni pour une
  question juridique pure (→ dlc-note-juridique), ni pour la simple mise en forme
  d'un contenu déjà établi (→ dlc-redaction-livrable). La fiche propose ; elle ne
  décide jamais à la place des organes de la copropriété.
---

# Fiche de décision — Assistant Copro Delacour Patrimoine

Ce skill couvre le maillon entre le fond et la forme : **le processus d'instruction
d'une décision multi-options et sa structure imposée**. C'est un skill
d'**orchestration** : la substance juridique vient de `dlc-note-juridique`, la mise
en forme et l'export de `dlc-redaction-livrable`, les données d'assurance live de
`assynco-erp`. Règle anti-doublon : si une règle est déjà écrite dans une autre
skill (traçabilité, nettoyage destinataire, réserves juridiques), elle s'applique
telle quelle — ce skill s'y réfère et ne la réécrit pas.

## 0. Garde-fous (non contournables)
- **Périmètre copro fixé** (immatriculation RNIC) avant toute instruction — invariant des
  Project Instructions (workflow de scope).
- **La fiche propose, elle ne décide pas.** La décision appartient au conseil
  syndical (dans le cadre exact de sa délégation) ou à l'assemblée générale.
  Aucune formulation qui engage la copropriété sur un montant, des travaux ou une
  échéance sans décision d'AG correspondante.
- **Toujours au moins 2 options réelles**, dont systématiquement l'option
  « ne pas agir / reporter », évaluée en risques et conséquences, pas seulement en
  euros.
- **Tout est sourcé** : chaque montant, date, clause, résolution citée suit les
  règles de traçabilité de `dlc-redaction-livrable` (§4). Un devis n'est pas une
  décision ; un diagnostic n'est pas un vote.
- **Pas de conclusion juridique durcie** : majorités, délais et validité portent
  les réserves de `dlc-note-juridique` ([à vérifier contre le texte en vigueur]).
- **Décidabilité honnête** : si les pièces manquent pour arbitrer, la fiche sort
  marquée « NON DÉCIDABLE EN L'ÉTAT » avec la liste des pièces à réunir. Ne jamais
  forcer une recommandation pour remplir le gabarit.

## 1. Cadrer la décision
- Reformule la décision à prendre en **une phrase fermée** (« Faut-il … ? »). Si
  l'utilisateur décrit un problème sans décision identifiable, fais préciser avant
  d'instruire.
- Identifie le **décideur visé** :
  - **Conseil syndical par délégation** → retrouve la résolution d'AG de
    délégation (périmètre + seuil de dépenses) dans les PV. Si elle est
    introuvable en base, marque-le : la fiche doit alors viser l'AG.
  - **Assemblée générale** → la fiche prépare l'inscription à l'ordre du jour.
  - **Syndic seul** (mesure conservatoire urgente) → le signaler comme tel, avec
    l'obligation d'en rendre compte.
- Identifie l'**échéance ou l'urgence** (péril, sinistre en cours, fin de contrat,
  date d'AG déjà fixée), sourcée si documentée.

## 2. Instruire (checklist de collecte)
Collecte ciblée par le sujet — jamais d'aspiration de dossier complet (interdit
des Project Instructions). Dans l'ordre :

a. **Historique décisionnel** : `PALIM_search_chunks` scopé sur la copro,
   `doc_type=PV_AG` — résolutions liées au sujet (votes antérieurs, budgets votés,
   refus passés, délégations). `PALIM_copro_overview` pour situer le contexte
   général si besoin.
b. **Pièces techniques et contractuelles** : devis, contrats, diagnostics,
   courriers (`PALIM_search_chunks` ; `PALIM_get_full_document` pour le détail
   d'un devis ou d'un contrat déterminant).
c. **Si un sinistre est lié** : `PALIM_search_dossiers`, puis skill `assynco-erp`
   (police, franchise, prise en charge, pipeline) — la part assurance change
   l'économie des options.
d. **Impact financier et répartition** : montants sourcés tels quels ; si la clé
   de répartition est en jeu (parties communes spéciales, charges par lot),
   consulter le RCP — s'il est absent de la base, le marquer (candidat
   non-décidabilité).
e. **Cadre juridique par option** : applique `dlc-note-juridique` (3 couches,
   réserves) pour la majorité applicable à **chaque** option — deux options
   peuvent relever d'articles différents (ex. réparation à l'identique vs
   amélioration).

## 3. Construire les options
2 à 4 options, chacune renseignée sur les mêmes champs pour être comparable :
- description en une phrase ;
- coût total et part copropriété (sourcés) ;
- financement (budget voté, fonds de travaux, appel de fonds — sourcé ou
  [À VÉRIFIER]) ;
- majorité requise (avec réserve) ;
- calendrier réaliste ;
- risques et conséquences.

Ne construis pas d'option artificielle pour étoffer la fiche ; n'omets pas une
option documentée (un devis présent en base est une option à traiter ou à écarter
explicitement).

## 4. Rédiger la fiche (gabarit imposé)

```
FICHE DE DÉCISION — [décision en une phrase fermée]
Copropriété : [nom] (immatriculation [immatriculation])    Date : [date]
Décideur visé : [conseil syndical par délégation / assemblée générale / syndic (conservatoire)]
[Si CS par délégation : Délégation : résolution n°[..], AG du [date], seuil [montant].
 (Source : PV.) — ou « Délégation non retrouvée en base → décision à porter en AG. »]
Échéance : [date / contrainte, sourcée — ou « non contrainte »]

1. Contexte et historique (sourcé)
[3-6 lignes : le problème, depuis quand, ce qui a déjà été voté ou refusé.
Résolutions d'AG citées au plus près, avec source.]

2. Options
| # | Option | Coût (part copro) | Financement | Majorité* | Délai | Risques |
|---|--------|-------------------|-------------|-----------|-------|---------|
[1 ligne par option, montants sourcés. L'option « ne pas agir / reporter » figure
toujours, risques à l'appui.]
* Majorités indicatives — réserves en section 4.

3. Analyse comparative
[Lecture critique, signalée comme interprétation : ce qui distingue réellement les
options, hypothèses prises, points d'attention (garanties, assurance, précédents
dans la copro).]

4. Cadre juridique et réserves
[Synthèse issue de dlc-note-juridique : majorité par option, délais, incertitudes
marquées [à vérifier]. Jamais durcie.]

5. Proposition et prochaine étape
[Option proposée et pourquoi — OU « NON DÉCIDABLE EN L'ÉTAT » motivé.
Prochaine étape concrète : inscription à l'ordre du jour de l'AG du [date] /
décision du CS dans le cadre de la délégation / pièce à obtenir d'abord.]

6. Pièces manquantes et vérifications
[Liste explicite. Si rien ne manque, l'écrire.]

Cette fiche est une aide à la décision préparée pour le gestionnaire ; elle ne
constitue ni une décision ni un avis juridique. Validation par le syndic / un
juriste requise avant toute décision, communication ou engagement.
```

## 5. Décidabilité (règle de sortie)
La fiche est marquée **NON DÉCIDABLE EN L'ÉTAT** (section 5 du gabarit) si l'un de
ces cas se présente :
- une option sérieuse n'a **aucun chiffrage sourcé** (devis mentionné dans un PV
  ou un courrier mais absent de la base) ;
- le **décideur ne peut pas être déterminé** (délégation introuvable ET majorité
  incertaine) ;
- une **pièce déterminante est absente** (RCP absent alors que la répartition est
  en jeu, diagnostic requis non réalisé).

Dans ce cas, la section 5 devient un plan de collecte (quoi réunir, auprès de
qui), la section 6 liste les manques, et la fiche reste **interne** — elle n'est
pas diffusée au conseil syndical en l'état.

## 6. Diffusion et mise en forme
La fiche naît **interne** (références techniques et [À VÉRIFIER] autorisés). Pour
la version destinée au conseil syndical ou l'export Word, enchaîne avec
`dlc-redaction-livrable` : nettoyage selon le destinataire, traçabilité sobre,
logo Delacour, proposition d'export en une seule question fermée. La structure de la
fiche (sections 1-6 du gabarit) est conservée dans la version externe — c'est le
gabarit `note_conseil_syndical` qui s'aligne sur elle, pas l'inverse.

## 7. Articulation avec les autres skills
| Besoin | Skill / tool |
|---|---|
| Majorité applicable, validité, délais, lecture du RCP | `dlc-note-juridique` (substance, réserves incluses) |
| Mise au propre, version externe, export Word | `dlc-redaction-livrable` (forme, nettoyage, logo) |
| Police, franchise, prise en charge, sinistre live | `assynco-erp` |
| Résolution du périmètre copro | `PALIM_list_copros` / `PALIM_discover_copros` (Project Instructions) |

Ce skill fournit le **processus** (cadrage → instruction → options → fiche →
décidabilité) ; il ne remplace aucun des trois autres et ne s'active pas pour une
simple question de recherche ou de rédaction.
