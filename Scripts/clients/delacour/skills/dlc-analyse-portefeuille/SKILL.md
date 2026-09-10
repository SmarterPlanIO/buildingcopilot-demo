---
name: dlc-analyse-portefeuille
description: >-
  Conduit une analyse inter-copropriétés ou cross-domaine (base documentaire ×
  ERP assurance Assynco) sur un périmètre de copropriétés : comptages, sommes,
  concentrations, écarts entre sources, avec couverture annoncée. À utiliser
  quand la question porte sur PLUSIEURS copropriétés à la fois ou croise deux
  domaines : « sur les Grands Ensembles… », « quelles copros ont… », « compare
  les sinistres documentés et le suivi Assynco », « où sont mes risques », « le
  plus / le moins sur le portefeuille », « combien au total ». NE PAS utiliser
  pour citer le contenu d'un document précis (→ recherche documentaire scopée),
  ni pour une question mono-copro simple (→ PALIM_copro_overview), ni pour une
  question juridique (→ dlc-note-juridique). L'analyse constate et localise ;
  elle ne recommande pas d'action engageante sans instruction dédiée
  (→ dlc-fiche-decision).
---

# Analyse de portefeuille — Assistant Copro Delacour Patrimoine

Ce skill encadre les analyses **inter-copros** (plusieurs copropriétés en une
passe) et **cross-domaine** (base documentaire × ERP Assynco) permises par
`PALIM_run_analytical_query` et les tools Assynco. C'est un skill de **méthode** :
il garantit qu'un chiffre agrégé sort toujours avec son dénominateur, sa
couverture et sa source. La mise en forme d'un livrable reste du ressort de
`dlc-redaction-livrable` ; l'instruction d'une décision, de `dlc-fiche-decision`.

## 0. Garde-fous (non contournables)
- **Un chiffre sans dénominateur n'est pas un chiffre.** Toute réponse agrégée
  annonce sa couverture : « X sinistres sur N des M copropriétés du périmètre »,
  en citant nommément les copros sans données (`copros_sans_donnees`).
- **Périmètre explicite d'abord.** Traduire les périmètres nommés des Project
  Instructions (Bloc 13 : « Grands Ensembles », « pôle Rodin », « secteur
  Paris 13 ») en codes et passer les codes aux tools. Périmètre inconnu → faire
  préciser, jamais deviner. Parc entier légitime seulement pour un recensement.
- **Absence de données ≠ absence de fait.** « 5757 n'a aucun dossier sinistre
  documenté » signifie que les archives n'en portent pas, pas que la copropriété
  n'a jamais eu de sinistre. Le dire dans ces termes.
- **Deux sources, deux réalités, dites séparément.** Le documentaire (archives,
  historique) et Assynco (suivi live du courtier) peuvent diverger : c'est une
  information, pas une erreur à masquer. Présenter les deux chiffres côte à
  côte et qualifier l'écart, sans décréter laquelle des deux sources « a raison ».
- **Un agrégat se vérifie avant d'être cité en engagement.** Un total calculé
  (somme de montants extraits) est présenté comme tel ; s'il doit être cité dans
  un livrable ou une réunion, redescendre au document source et recomposer le
  total poste par poste.
- **Pas d'extrapolation hors périmètre.** Une tendance constatée sur les copros
  servies ne se projette pas sur le reste du portefeuille du client.

## 1. Cadrer l'analyse
- Reformuler la question en une **mesure** : quoi (métrique ou comptage), sur
  quel périmètre, sur quelle période, depuis quelle source (documents, dossiers,
  Assynco, ou croisement).
- Choisir le régime :
  - **Agrégat structuré** (combien, total, répartition, le plus/le moins) →
    `PALIM_run_analytical_query` (count / sum / list), périmètre en
    `copro_codes`.
  - **Croisement documentaire × ERP** → agrégat côté RAG + `PALIM_assynco_*`
    par copro, rapprochés dans la réponse.
  - **Contenu d'un document** (clause, résolution, montant d'un devis) → sortir
    de ce skill : recherche documentaire scopée.
- Sur un périmètre nommé, annoncer la traduction en mots (« sur le bureau
  Grands Ensembles, 9 copropriétés »), jamais en litanie de codes.

## 2. Conduire l'analyse
- **Partir large, resserrer ensuite** : un premier count par copro donne la
  carte ; les facettes (`concentration`, `refine_suggestions`) guident le
  resserrage (année, doc_type, statut, sous_type).
- **Nommer la concentration** quand elle existe : « les 4 premières copropriétés
  portent 86 % du total » vaut mieux qu'un tableau brut.
- **Croiser quand la question le demande** : pour chaque copro du périmètre,
  mettre en regard la mesure documentaire et la mesure Assynco (sinistres
  documentés vs suivis, prime en base vs quittance, surface déclarée vs pièces).
  Les écarts sont le résultat, pas le déchet.
- **Signaler les valeurs aberrantes** (ordre de grandeur incohérent, ratio
  extrême) comme « à vérifier sur pièce », sans les lisser ni les exclure en
  silence.

## 3. Restituer
- Ouvrir par le résultat en une phrase avec sa couverture, puis le détail par
  copropriété (tableau si plus de 3 lignes), puis les écarts et anomalies.
- Chaque ligne d'écart cite ses deux sources (document daté d'un côté, champ
  Assynco de l'autre) selon les règles de traçabilité de
  `dlc-redaction-livrable`.
- Fermer sur ce que l'analyse **ne couvre pas** : copros hors périmètre, années
  manquantes, sources non consultées.
- Si l'analyse débouche sur une décision à prendre (renégociation, travaux,
  relance d'un dossier), le dire et proposer d'enchaîner sur
  `dlc-fiche-decision` — ne pas trancher ici.
