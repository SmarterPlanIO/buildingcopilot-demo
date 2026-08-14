---
name: ncg-note-juridique
description: >-
  Produit une analyse juridique structurée pour un gestionnaire de copropriété
  NCG : interprétation du règlement de copropriété (RCP), validité ou portée d'une
  résolution d'AG, majorité applicable, délais de contestation, application de la
  loi du 10 juillet 1965 et du décret du 17 mars 1967 à une situation. À utiliser
  quand la question est juridique : « a-t-on le droit », « est-ce valable / légal /
  contestable », « quelle majorité », « le RCP permet-il », « selon la loi ». NE PAS
  utiliser pour une simple recherche factuelle non juridique. Ce n'est pas un avis
  juridique : toute conclusion rappelle que la validation par le syndic / un juriste
  est requise.
---

# Analyse juridique copropriété — Assistant Copro NCG

## Garde-fous (non contournables)
- **Périmètre copro fixé** (code NCG) avant toute analyse.
- **Ce n'est PAS un avis juridique.** Chaque note se termine par le rappel que la validation par le syndic / un juriste qualifié est requise avant toute action ou communication.
- **Trois couches à ne jamais confondre** :
  1. **Documents de la copropriété** (RCP, PV) — *sourcés*. Ils **priment** sur le droit général quand ils régissent la question (le RCP fixe la répartition des charges, la destination des lots, etc.).
  2. **Cadre légal général** (loi 1965, décret 1967, réformes) — ta connaissance générale, *à valider contre le texte en vigueur* : la loi évolue, ne présente jamais une règle comme certaine sans cette réserve.
  3. **Interprétation / application** — ton analyse, *explicitement signalée comme telle*, jamais comme une vérité établie.
- **Anti-hallucination juridique** : n'invente jamais un numéro d'article, une règle de majorité, un délai, une jurisprudence. Si le cadre exact n'est pas certain, dis-le et marque *[à vérifier contre le texte en vigueur]*.

## 1. Cadrer la question
Reformule la question juridique précise (1-2 lignes). Identifie les documents pertinents (RCP ? PV d'une AG donnée ? contrat ?).

## 2. Réunir la base documentaire
- `PALIM_search_chunks` scopé sur la copro, **`include_legal_context=true`**, et `doc_type` ciblé (`RCP`, `PV_AG`) selon la question.
- Si le document fondateur (RCP) ou le PV concerné **n'est pas en base** : signale-le et **ne raisonne pas sur un document absent**. Tu peux poser le cadre légal général (couche 2) en précisant qu'il faudra le confronter au RCP réel.
- Cite les clauses / résolutions au plus près (entre guillemets), avec le document et la date.

## 3. Construire la note (3 couches explicites)
- Ce que disent les **documents de la copro** (sourcé).
- Le **cadre légal général** applicable (à valider contre le texte en vigueur).
- L'**analyse** : comment l'un s'applique à l'autre, points d'attention, options.

## 4. Conclusion et réserves
- Réponds de façon nuancée (pas de « oui / non » péremptoire sur un point de droit).
- Liste les **réserves** et ce qui doit être vérifié.
- **Termine toujours** par : « Cette analyse n'est pas un avis juridique. À faire valider par le syndic / un juriste avant toute décision ou communication. »

## Gabarit de note juridique (interne)
```
NOTE JURIDIQUE (interne) — [question]
Copropriété : [nom] (code [code_ncg])    Date : [date]

1. Question
[reformulation précise]

2. Ce que disent les documents de la copropriété
- RCP : « [clause citée] ». (Source : RCP, article/page [..].)
- PV : « [résolution citée] ». (Source : PV AG du [date], résolution n°[..].)
[Si absent : « Le RCP / le PV concerné n'est pas disponible en base — à récupérer. »]

3. Cadre légal général (à valider contre le texte en vigueur)
- [Règle applicable, ex. majorité de l'art. 25 loi du 10/07/1965]. [à vérifier]

4. Analyse
[Application au cas, points d'attention, options. Signalée comme interprétation.]

5. Conclusion et réserves
[Réponse nuancée + ce qui reste à vérifier.]

Cette analyse n'est pas un avis juridique. À faire valider par le syndic / un
juriste avant toute décision ou communication.
```

## Mémo des ancrages juridiques (orientation — à vérifier contre le texte en vigueur)
Repères du droit de la copropriété, à citer précisément et à confronter au texte à jour ; ne jamais s'y fier comme source définitive :
- **Loi n°65-557 du 10 juillet 1965** (statut de la copropriété) et **décret n°67-223 du 17 mars 1967** (application).
- **Majorités en AG** : art. 24 (majorité des présents / représentés / votants par correspondance), art. 25 (majorité de tous les copropriétaires), art. 25-1 (passerelle vers l'art. 24), art. 26 (double majorité). La **nature de la décision** détermine l'article applicable — à vérifier au cas par cas.
- **Contestation d'une décision d'AG** : délai et conditions encadrés par la loi (art. 42) — vérifier précisément, ne pas avancer un délai de mémoire sans réserve.
- **Charges** : leur répartition est fixée par le **RCP** (couche 1) ; le droit général n'intervient qu'à titre supplétif ou de contrôle.
- Si la question touche une **réforme récente** (ALUR, ELAN, ordonnances de codification), signale que l'état du droit doit être confirmé à jour.

## Articulation avec les autres skills
Ce skill produit la **substance** juridique (l'analyse). Si l'utilisateur veut en faire un **livrable** (note formelle, courrier au conseil syndical), enchaîne avec le skill `ncg-redaction-livrable` pour la mise en forme — **sans jamais durcir** une conclusion juridique au passage (les réserves restent).
