# PLAN — Fiche de synthèse v3 : de l'annuaire à la carte de navigation

> Étude du 01/09/2026, **sans code** (décision Thai). Revue critique de la refonte v2
> (PLAN_FIABILITE_SYNTHESE.md, C1→C6 livrés et déployés v12) et plan d'amélioration.
> Règle de l'étude : **aucune hypothèse n'est tenue pour vraie sans avoir été testée sur
> les données**. Chaque affirmation ci-dessous porte sa mesure (T-n) ; les hypothèses
> réfutées sont conservées — elles valent autant que les confirmées.
>
> Datasets : 143 traces Langfuse (vrais appels MCP Claude Teams NCG, 18/08→01/09/2026),
> 140 prompts du harness Streamlit (03→08/2026), base NCG (19 copros, 18 634 documents,
> 334 147 chunks, 12 625 résolutions, 297 dossiers). Scripts d'étude rejouables dans
> `ops/tools/etude_fiche_v3/` (README : quelle mesure vient de quel script).

---

## 0. Résumé exécutif

1. **Le principe de la v2 est validé** — « la fiche oriente, les sources tranchent » : zéro
   prose générée, 1 463 pointeurs tous valides, questions clés honnêtes (le cas 5750 est un
   vrai positif de prudence, vérifié sur pièce). Rien à renier.
2. **Sa forme est calibrée pour un usage minoritaire.** La v2 est un digest de « reprise de
   dossier » en 5 sections fixes. Or les 193 vraies questions mesurées sont à ~80 % des
   questions **ciblées** (« où en est le sinistre X », « dernière AG et budget voté », « contrats
   en cours », « travaux votés en 2025 »). Pour elles, une fiche doit être un **routeur** —
   dire où regarder en un appel — pas un résumé. Et les traces montrent que le LLM appelle
   `copro_overview` 7 fois pour 45 `search_chunks` : la fiche n'est pas son premier réflexe.
3. **La base contient déjà de quoi construire ce routeur, sans LLM** : `sous_type` renseigné à
   69 % (vocabulaire de ~40 thèmes), dates à 87,5 %, résolutions budget/comptes/syndic
   couvertes sur 19/19 copros, échéance de mandat de syndic extractible par règle (10/12),
   dernière activité d'un dossier dérivable pour 81 % d'entre eux. Un index thématique ×
   temps pèse ~1,7 Ko par copro (mesuré sur 5757).
4. **Le détecteur de résolutions (C1) est bon là où le format est propre, faible ailleurs** :
   96 % établi sur les PV tabulaires ATHOME, **21 %** sur les autres formats. Et 34 % des
   « indéterminées » ne sont pas des échecs mais des **non-votes légitimes** (points
   d'information, sommaires) que le modèle de statuts ne sait pas nommer.
5. **Cinq défauts de la v2 sont mesurés et corrigeables** : fraîcheur marquée périmée à tort
   (4/5 copros), PV en doublon (36 % des AG), questions R2 pointant des fragments, « dossiers
   chauds » classés par un montant renseigné à 25 % (p99 = 680 k€, données absurdes),
   `lots`/`superficie` absents alors qu'Assynco les a pour une partie des copros.
6. Proposition : une **carte de navigation par copro** en couches (identité + faits à
   provenance, index thématique × temps, chronologie des AG, index des dossiers, questions
   clés), servie avec un paramètre `focus` pour ne renvoyer que la couche utile. Zéro LLM,
   même doctrine, même recette. Un protocole d'évaluation sur les vraies questions **avant**
   de coder la couche suivante.

---

## 1. Ce que les utilisateurs demandent réellement (T1)

193 questions (53 requêtes de recherche Langfuse + 140 prompts harness), classification
lexicale multi-label, transparente :

| Type de question | Part | Exemples réels |
|---|---|---|
| Sinistre / dossier / « où en est » | **35 %** | « où en est le sinistre Lemeau », « liste tous les sinistres, inclus les DO », « que reste-t-il à faire ? » |
| AG / PV / vote / résolution | **23 %** | « date de la dernière AG et budget voté », « qui a été élu au CS », « résolutions votées en 2017 » |
| Inventaire / liste / combien | 19 % | « liste les contrats de maintenance signés depuis 2010 », « quelles copros ont eu des travaux de plomberie » |
| Travaux / devis | 13 % | « travaux votés de 2014 à 2017 », « compare les devis ascenseurs depuis 2019 » |
| Finances / comptes / budget / charges | 13 % | « évolution des charges sur 5 ans », « montant des charges impayées » |
| Temporel : dernier / en vigueur / depuis | 10 % | « dernière assemblée générale », « contrats de maintenance en cours actuellement » |
| Organes / personnes | 8 % | « qui a le contrat des espaces verts », « copropriétaire lésé » |
| Contrat / prestataire | 7 % | « contrats et factures de chauffage » |
| Assurance | 5 % | « les tags sont-ils couverts par la police » |
| Portefeuille multi-copro | 4 % | démo GE (Enedis, décret tertiaire, IGH) |
| Juridique / RCP | 3 % | « les terrasses sont-elles privatives » |

Ce que ça change : la v2 répond bien à « fais-moi le point sur cette copro » (reprise de
dossier), qui n'apparaît **presque jamais** dans les données. Les questions dominantes ont
une forme commune : *un sujet + parfois une période*, et attendent **le bon document** (le
PV de telle AG, le contrat en vigueur, les pièces du sinistre X). C'est un problème de
routage, et le sémantique pur le résout mal (« dernière AG » = 4 requêtes successives dans
Langfuse le 25/08 avant d'aboutir).

**Usage observé de la fiche (T19)** : 7 appels `copro_overview` sur 143 traces. Quand elle
est appelée, elle précède bien des recherches (25/08 : `list_copros → overview →
search_dossiers → assynco → search_chunks`) — le pattern voulu. Mais elle n'est pas le
premier réflexe (le 24/08, elle arrive après 3 analytiques et 3 recherches). Langfuse
tronque la sortie du tool (métadonnées seules) : **on ne sait pas comment son contenu est
consommé** — c'est un trou d'instrumentation à combler (§7).

## 2. Hypothèses testées — verdicts

| # | Hypothèse | Verdict | Mesure |
|---|---|---|---|
| H1 | Les questions sont surtout des « reprises de dossier » que la v2 sert bien | **Réfutée** | ~80 % de questions ciblées (T1) |
| H2 | Le chunker tronque les résolutions (cause des indéterminées) | **Réfutée** | Texte complet en base ; c'était mon affichage à 700 c. (T3b) |
| H3 | Les indéterminées sont du bruit | **Réfutée** | 92 % portent un numéro ; ce sont de vraies résolutions (T3) |
| H4 | Les indéterminées sont des échecs du détecteur | **Partiellement** | 34 % = non-votes légitimes (12,8 % explicites + 21,6 % sommaires) ; ~15 % = gap récupérable (T20) |
| H5 | Le détecteur est homogène selon les formats | **Réfutée** | tabulaire 96 % établi vs non-tabulaire 21 % (T16b) |
| H6 | `chunks.themes` peut porter un index thématique | **Réfutée** | colonne vide à 100 % (T2b) |
| H7 | `documents.sous_type` + dates suffisent pour un index thématique | **Confirmée** | 69 % / 87,5 % ; ~40 thèmes ; 1,7 Ko/copro (T2a, T24) |
| H8 | Les résumés Haiku de documents peuvent nourrir la fiche | **Nuancée** | 99,9 % présents, factuels sur contrats/courriers ; **inutiles sur les PV** (ne voient que la 1ʳᵉ page : « élection du président ») (T2c, T23) |
| H9 | `statut` LLM des contrats = « en vigueur » | **Réfutée** | 828 NULL / 710 actif / 267 en_cours / 253 expiré ; 22 « actifs » sur 142 contrats sécurité incendie 5750 (T8) |
| H10 | Un « dernier contrat daté par sous-type » est un proxy exploitable | **Confirmée (proxy)** | 5757 : SYNDIC 2026, BAIL 2025, MRI 2020 — plausible, à présenter comme pointeur daté (T25) |
| H11 | L'échéance du mandat de syndic est extractible par règle | **Confirmée** | « prendra fin le 30/06/2025 » présent dans 10/12 résolutions (T9) |
| H12 | Les questions AG sont indexables depuis `resolutions` | **Confirmée** | budget/comptes/syndic 19/19 copros, travaux 18/19, CS 17/19 (T11) |
| H13 | `dossiers.statut` distingue les dossiers ouverts | **Réfutée** | EN_COURS par défaut à 75 % ; `at_situation` renseigné seulement côté Assynco (T4) |
| H14 | `dossiers.etapes` décrit l'avancement réel | **Réfutée** | gabarit générique (`delai_j`), pas un suivi (T13) |
| H15 | Une « dernière activité » de dossier est dérivable | **Confirmée** | max(date_document) des documents liés pour 81 % des dossiers (T13) |
| H16 | Les dates de suivi Assynco permettent un vrai « où en est » | **Réfutée (faible)** | 51 dossiers Assynco : déclaration 65 %, clôture 41 %, expert/rapport/règlement < 20 % (T26) |
| H17 | Les PV récents de la fiche sont uniques par AG | **Réfutée** | 36 % des AG portées par 2 à 10 fichiers (T5) |
| H18 | La fraîcheur de la fiche v2 est fiable | **Réfutée (bug)** | watermark v1 non mis à jour par 09 v2 → « périmée » à tort sur 4/5 copros (T6) |
| H19 | Un index complet des dossiers tient dans une fiche | **Confirmée avec réserve** | médiane 13,5 dossiers/copro ; 8050 = 102 (~15 Ko) → pagination ou `focus` (T22) |
| H20 | Le montant estimé est un bon critère de « dossier chaud » | **Réfutée** | renseigné à 25 %, p50 2,3 k€, p99 680 k€, 4 valeurs > 100 k€ absurdes (T4) |

## 3. Ce qui marche dans la v2 et ce qui ne marche pas (mesuré)

**Acquis à conserver.** Zéro affirmation générée ; intégrité référentielle prouvée (recette) ;
pointeurs de gouvernance justes après correction ; questions clés qui *demandent* au lieu
d'affirmer — validé sur pièce (5750, comptes 2021 : « Votent POUR 6 Associés totalisant
84 025 / 84 025 » puis énumération des votants, et une proclamation que le détecteur a
manquée, cf. §5 — la question posée était donc légitime et le lecteur tranche).

**Défauts mesurés.**
- **D1 Fraîcheur** (T6) : `09_copro_synthese.py` v2 n'écrit plus `nb_documents` /
  `dernier_pv_date` ; `_freshness` compare au watermark v1 → `stale=true` à tort.
- **D2 Doublons de PV** (T5) : « Courrier-complet.pdf » (convocation + PV) et « PV signé »
  de la même AG apparaissent deux fois ; 36 % des AG sont multi-fichiers.
- **D3 Questions R2 bruitées** : pointent des fragments dont l'objet est un débris
  (« RE [] ABSTENTION [] ») — aucun filtre de qualité sur `numero`/`objet_court`.
- **D4 Dossiers chauds mal classés** (T4) : tri par `montant_estime` (25 % renseigné, valeurs
  absurdes en tête), « ouvert » = statut par défaut, 20 % de lésés inconnus, 17 % sans
  document lié.
- **D5 Identité incomplète** : `lots`/`superficie` déclarés absents alors qu'Assynco fournit
  `surface_m2` pour une partie des copros (5390 : 16 041 m²) — non lu à la génération.
- **D6 Extraction** : 60 % des résolutions sans résultat, dont ~15 % récupérables (§5).
- **D7 Instrumentation** : Langfuse ne conserve pas la sortie de `copro_overview` → impossible
  de mesurer l'usage réel de la fiche.

## 4. Proposition d'architecture : la carte de navigation par copro

Le modèle mental change : **la fiche n'est pas un résumé, c'est la carte du territoire**
documentaire de la copro. Elle répond à « où regarder pour ce sujet, à cette période ? »
et laisse la lecture au LLM. Mêmes principes qu'en v2 (calculé ou pointé, jamais généré ;
provenance et date sur chaque entrée ; recette d'intégrité), forme différente :

**Couches** (toutes SQL, toutes en pointeurs) :

- **L0 — Identité et faits à provenance** (toujours renvoyée, < 1 Ko) : code, immatriculation,
  Assynco live (adresse, surface, année, polices) ; **mandat de syndic avec échéance
  calculée par règle** (H11) et pointeur ; pointeur CS ; « à confirmer » partout où c'est
  extrait d'un document.
- **L1 — Index thématique × temps** (H7, ~2 Ko) : par `sous_type` (≈ 40 thèmes), nombre de
  documents, période couverte, **dernier document daté** (pointeur) et, pour les types
  contractuels, le proxy « dernier contrat daté » (H10) présenté comme *pointeur daté*,
  jamais comme « en vigueur ». C'est le routeur des questions contrat / travaux / charges /
  assurance / prestataires (≈ 40 % des questions).
- **L2 — Chronologie des AG** (H12, H17) : une entrée par **date d'AG dédupliquée** (fichier
  de référence choisi : PV signé > diffusion > courrier complet), nombre de résolutions
  établies / non établies, et les résolutions **clés** de chaque AG (budget, comptes, syndic,
  CS, travaux, fonds) avec résultat et pointeur. Routeur des questions AG/vote/temporel
  (≈ 33 %). « Dernière AG et budget voté » devient un appel, pas quatre.
- **L3 — Index des dossiers** (H15, H19) : **tous** les dossiers (pas seulement « chauds »),
  avec `derniere_activite` dérivée, source (RAG / Assynco), nombre de pièces, pointeurs
  d'entrée ; classement par activité récente, pas par montant ; les montants portent leur
  provenance. Pagination au-delà de ~40. Routeur des 35 % de questions sinistre.
- **L4 — Questions clés** : conservées, filtrées (numéro ou en-tête reconnaissable exigé),
  enrichies de deux règles à forte valeur : *mandat de syndic arrivant à échéance avant la
  prochaine AG* (calculé) et *AG sans PV signé en base*.

**Contrat MCP** : `PALIM_copro_overview(code, focus=None)` ; `focus ∈ {ag, dossiers,
contrats, finances, identite}` renvoie L0 + la couche demandée en entier ; sans `focus`, L0 +
version compacte de chaque couche (≤ 8 Ko). Le LLM choisit le focus d'après la question —
c'est exactement le geste qu'il fait déjà avec `doc_type`/`year_min` dans `search_chunks`.
Bonus : la docstring peut dire « pour "dernière AG" → focus=ag », ce qui rend la fiche le
**premier** réflexe (D7 mesurera si c'est le cas).

**Ce que je ne propose PAS, et pourquoi (données à l'appui)** :
- Résumés LLM hiérarchiques (RAPTOR / communautés GraphRAG) : les résumés Haiku existants ne
  voient pas les décisions des PV (T23) ; c'est la classe d'erreur de l'incident, à un coût
  qui croît avec 150 copros.
- Extraction LLM généralisée de « faits » : seulement là où une **règle** suffit (échéance de
  mandat, dates) — sinon pointeur.
- Index sur `chunks.themes` : colonne morte.
- Base graphe : les identifiants existants (`source_file`, `chunk_id`, `dossier_id`,
  `resolution_id`) portent déjà le graphe ; la recette prouve qu'il tient.

## 5. Le détecteur de résolutions : ce que les données demandent

- **Statuts manquants** (T20) : `non_soumise` (12,8 % des indéterminées : « n'a pas fait
  l'objet d'un vote », « point d'information », « pour information ») et `sommaire` (21,6 % :
  sous-titres seuls, < 400 c., aucun verbe de vote). Sortis du sac « indéterminé », le KPI
  honnête devient : établi 40 % → **~55 % des résolutions réellement soumises au vote**.
- **Gap récupérable** : 6,5 % contiennent « adoptée » non captée (493 cas), 8,9 % un décompte
  chiffré non exploité. Deux causes reproduites sur pièces :
  1. *Énumération des votants* — « Ont voté pour SDC AIGLE (5441), SDC BELIER (20746)… » : le
     dernier mot-clé fort n'a pas de valeur (ou une valeur parasite) ; règle : préférer le
     dernier mot-clé fort **qui produit une valeur**.
  2. *« voix » de la proclamation* — « adoptée à la majorité des **voix** » est compté comme
     ancre de décompte et repousse la zone de constat *après* la proclamation ; règle : les
     ancres `tantiemes/voix` ne délimitent pas la zone, seules pour/contre/abstention le font.
  Et le format « CETTE RESOLUTION EST ADOPTEE A L'UNANIMITE DES PRESENTS » précédé de « N
  copropriétaires totalisent T tantièmes au moment du vote » (5548, 2020) à instrumenter.
- **Priorité par format** : les PV non tabulaires (90 % des chunks) sont à 21 % — c'est là que
  chaque point gagné compte ; les tabulaires (96 %) sont finis.
- Méthode inchangée : audit de prévalence corpus avant chaque règle, sondes réelles en tests,
  matrice avant/après, zéro `adoptee→rejetee` toléré.

## 6. Défauts v2 à corriger (P0, sans changement d'architecture)

| Défaut | Correctif | Preuve attendue |
|---|---|---|
| D1 fraîcheur | 09 v2 met à jour `nb_documents`/`dernier_pv_date`/`nb_sinistres_assynco` | recette : `stale=false` juste après génération |
| D2 doublons PV | dédup par `(copro, date_ag)`, fichier de référence par règle de priorité | 1 entrée par AG dans `pv_recents` |
| D3 R2 bruitée | filtre `numero IS NOT NULL OR objet ~ en-tête` | 0 pointeur vers un débris |
| D4 dossiers chauds | tri par `derniere_activite` desc, montant en simple attribut à provenance, exclusion des lésés inconnus du top | top 10 = dossiers récents et nommés |
| D5 identité | lire Assynco (`surface_m2`, `annee_construction`, `nb_coproprietaires`) à la génération | `champs_absents` réduit aux vrais inconnus |
| LEMEAU | fusion RAG/Airtable quand même lésé + même copro et dates à < 12 mois | 1 dossier LEMEAU |

## 7. Évaluation : mesurer avant de construire la suite

La recette C6 prouve l'intégrité, pas l'utilité. Il manque une **évaluation orientée
questions** :
1. **Golden set** : 30 questions tirées des 193 réelles (stratifiées par type du §1), avec
   pour chacune la *source décisive attendue* (fichier + résolution/dossier) établie à la main.
2. **Harness** : le mode agent Streamlit (v0.8.0, boucle Converse + tools MCP live) rejoue
   les 30 questions avec la fiche v2, puis avec la carte v3 ; on mesure par question : nombre
   d'appels de tools, la source décisive atteinte ou non, présence d'une citation correcte.
3. **Critère de passage v3** : la source décisive est atteinte en ≤ 2 appels pour ≥ 70 % des
   questions (contre une base v2 à mesurer d'abord).
4. **Instrumentation** (D7) : tracer la sortie de `copro_overview` (taille + `focus` + sections
   renvoyées) et l'appel suivant, pour lire l'usage réel en prod.

## 8. Feuille de route proposée (rien n'est codé)

| Phase | Contenu | Effort | Dépendances |
|---|---|---|---|
| **P0** | Défauts D1-D5 + fusion LEMEAU ; recette étendue | 1 session | aucune |
| **P1** | Détecteur : statuts `non_soumise`/`sommaire`, 2 règles d'énumération/voix, revue des 493 « adoptée non captée » ; matrice avant/après | 1-2 sessions | P0 (recette) |
| **P2** | Golden set 30 questions + mesure de la base v2 dans le harness ; instrumentation Langfuse | 1 session | aucune (parallèle à P1) |
| **P3** | Carte v3 : L1 index thématique, L2 chronologie AG dédupliquée, L3 index dossiers, paramètre `focus` ; deploy v13 ; instructions v5.0 | 2-3 sessions | P0, P1, P2 (critère de passage) |
| **P4** | Rollout Delacour puis CSG (06a + 09b + 09 + recette) — reste d'abord à faire sur la v2 | ½ session/tenant | indépendant |

Ordre recommandé : P0 → P2 (mesurer avant de construire) → P1 → P3. P4 dès maintenant sur
la v2 pour Delacour (le golden case de l'incident y attend sa vraie donnée).

## 9. Ce que cette étude ne sait pas encore

- Comment le LLM consomme le contenu de la fiche (D7) — les traces ne le montrent pas.
- Si le paramètre `focus` sera utilisé spontanément par Claude Teams (à mesurer en P2/P3 ;
  repli : la docstring impose le focus par type de question, comme le Bloc 4 l'a fait pour la
  chronologie).
- La proportion exacte du gap « adoptée non captée » qui est de la faute du détecteur vs de
  l'OCR — la revue des 493 cas de P1 le dira.
