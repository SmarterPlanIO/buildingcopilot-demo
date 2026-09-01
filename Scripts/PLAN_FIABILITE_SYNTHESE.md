# PLAN — Fiche de synthèse v2 : annuaire de la copro, pas narrateur

> v2 du 01/09/2026 (supersede la v1 du 28/08 : le fix du générateur narratif est ABANDONNÉ
> au profit d'un redesign — décision Thai, « pas de correctifs transitoires »).
> Statut : PLAN — rien codé.
> Déclencheur : incident Delacour 27-28/08 (fiche du 26/08) — narratif affirmant
> l'approbation de comptes 2023 en réalité REJETÉS (PV 30/03/2026, rés. 3 : 2 606 pour /
> 4 867 contre / 58 abst.), vote rattaché à la mauvaise AG, comptes de dossiers
> contredisant la couche faits de la même fiche (25+4 vs 37 réels).

---

## 0. Root cause (relue dans 09_copro_synthese.py le 28/08) et leçon de design

Trois défauts de mécanique : troncature du bloc PV **au caractère** (3 500 chars/PV — peut
séparer un dispositif de son décompte pourtant dans le même chunk, et ne montre que les
premières résolutions) ; agrégats dossiers calculés dans `facts` mais **jamais injectés au
prompt** (Haiku compte lui-même, faux) ; **aucun contrôle** post-génération.

Mais la leçon dépasse la mécanique : demander à Haiku de compresser une copro (jusqu'à
100 Go de documents) en 250 mots depuis 2 extraits tronqués, c'est une compression avec
perte dont la perte n'est pas signalée. Corriger le générateur réduit la probabilité
d'erreur ; il reste un composant qui AFFIRME des contenus qu'il n'a pas les moyens de
vérifier. On supprime la classe d'erreur au lieu de la raboter.

## 1. Décision de design (Thai, 01/09/2026)

**La fiche de synthèse est un ANNUAIRE, pas un narrateur.** Elle présente les chiffres
clés et les informations stables de la copro, et elle POINTE vers les dossiers chauds et
les documents décisifs. L'interprétation (sens d'un vote, chiffre précis d'un tableau mal
scanné, détail d'un dossier) est déléguée au LLM appelant, qui suit les pointeurs et
consulte les chunks — c'est le seul composant qui a à la fois l'accès aux sources et les
garde-fous de lecture (Bloc 4, sourçage, « le PV tranche »).

- Principe permanent, gravé dans les instructions : **« la fiche oriente, les sources
  tranchent »**.
- Esprit Graph Engineering, exécution pragmatique : le graphe est IMPLICITE, porté par les
  identifiants existants (fiche → `dossier_id` → `source_file` → `chunk_id`, plus
  `resolutions` ci-dessous). Pas de base graphe, pas de nouvelle infra : du JSON de
  pointeurs dans `copro_synthese`.
- Une affirmation ne peut figurer dans la fiche que si elle est CALCULÉE (SQL, RNIC,
  Assynco) ou PORTEUSE de son pointeur source avec date. Ce qui ne peut être ni l'un ni
  l'autre devient une QUESTION avec pointeurs — une fiche qui pose des questions ne peut
  pas affirmer faux (l'incident, retourné en feature : « Les comptes 2023 sont-ils
  approuvés ? → PV 30/03/2026, rés. 3, chunks … »).
- Le narratif de reprise de dossier n'est pas perdu : il devient un produit À LA DEMANDE
  du LLM appelant, composé en lisant les sources pointées (frais, sourcé, jetable).

## 2. Cible — schéma de la fiche v2 (JSON dans `copro_synthese.faits_v2`)

```json
{
  "identite": {
    "nom": "…", "adresse": "…", "immatriculation": "AB…",
    "lots_principaux": 48, "lots_total": 54, "superficie_m2": 7441,
    "periode_construction": "…",
    "gestionnaire": {"valeur": "…", "source": "airtable"},
    "mandat_syndic": {"valeur": "…", "echeance": "…",
                       "source": {"source_file": "…", "chunk_ids": ["…"], "date_source": "…"}},
    "conseil_syndical": [{"nom": "…", "role": "…",
                           "source": {"source_file": "PV AG …", "chunk_ids": ["…"],
                                       "date_source": "…", "statut": "extrait, à confirmer"}}]
  },
  "chiffres_cles": {
    "nb_documents": 0, "periode_couverte": [2013, 2026],
    "dossiers": {"total": 0, "par_statut": {}, "par_type": {}},
    "doc_types": {}
  },
  "dossiers_chauds": [
    {"dossier_id": "…", "titre": "…", "type": "SINISTRE", "statut": "EN_COURS",
     "montant_estime": 22218, "derniere_activite": "…", "ref_assynco": "A25…",
     "pointeurs": {"source_files": ["…"], "chunk_ids_entree": ["…"]},
     "motif_selection": "statut EN_COURS + montant > seuil"}
  ],
  "questions_cles": [
    {"question": "Les comptes 2023 sont-ils approuvés ?",
     "regle": "exercice sans approbation détectée",
     "pointeurs": {"resolutions": ["…"], "source_files": ["PV 30/03/2026"], "chunk_ids": ["…"]}}
  ],
  "pv_recents": [
    {"date": "2026-03-30", "source_file": "…",
     "resolutions_a_decompte": [{"resolution_id": "…", "objet_court": "…", "chunk_ids": ["…"]}]}
  ]
}
```

Règles de fabrication par section : `identite`/`chiffres_cles` = SQL + RNIC + Airtable,
zéro LLM (les champs extraits des documents — CS, mandat — portent pointeur, date et
statut « à confirmer ») ; `dossiers_chauds` = sélection PAR RÈGLES avec `motif_selection`
explicite ; `questions_cles` = dérivées PAR RÈGLES du structuré (§4) ; `pv_recents` =
liste + détection de décompte. Seul LLM admis : un TITRAGE court (`titre`, `objet_court`)
sur texte fourni entier — jamais un fait, jamais un résultat, jamais un compte.

## 3. Chantiers

### C1 — Détecteur de résultat de résolution (module pur, fondation)
`resolution_index.py` : sur les chunks PV_AG (déjà chunkés par résolution), détection à
**deux canaux indépendants + réconciliation**. Une résolution contient TROIS objets de
statuts différents — le distinguo est le cœur anti-incident :
- le **dispositif** (« L'assemblée approuve… », forme ACTIVE) : texte soumis au vote,
  JAMAIS utilisé pour le résultat (c'est lui qui a piégé le narratif) ;
- le **décompte** (POUR/CONTRE/abstentions/tantièmes, art. 24/25/26) → canal A :
  résultat CALCULÉ depuis les nombres ;
- la **proclamation** → canal B : résultat LU dans le texte du PV. Le critère qui la
  distingue du dispositif est **POSITIONNEL, pas grammatical** (correction Thai 01/09 :
  des PV réels concluent par un « l'assemblée approuve… » ACTIF après le décompte — la
  forme passive n'est qu'un signal, pas un critère). Règle : ce qui précède le décompte
  est la proposition soumise au vote ; ce qui le suit, ou clôt le bloc résolution, est le
  constat. Ce n'est pas une inférence : quand le tableau de votes est illisible mais que
  la conclusion est nette, le PV lui-même énonce le résultat — « les sources tranchent »
  inclut ce cas.

Mécanique positionnelle : le décompte sert d'**ANCRE même illisible** — un tableau
massacré par le scan laisse des traces localisables (POUR/CONTRE, « tantièmes », lignes de
chiffres cassées) qui partagent la résolution en avant (dispositif) / après (proclamation),
même quand les nombres sont incalculables. Sans ancre (unanimité sans chiffres), la phrase
de CLÔTURE du bloc est une proclamation probable, le lexique (adoptée/rejetée/approuve)
modulant la confiance. Deux flags dégradent au lieu de parier : `resolution_tronquee`
(ni décompte ni clôture visibles — le critère « fin de texte » exige de voir la vraie fin,
d'où les résolutions ENTIÈRES) et `ordre_anormal` (proclamation avant le décompte —
Textract peut réordonner une mise en page à colonnes).

Réconciliation : A+B concordants → `resultat` (source `decompte+proclamation`, confiance
haute) ; A seul → calculé (haute) ; **B seul → résultat proclamé (source `proclamation`,
confiance moyenne + flag `decompte_illisible`)** ; A et B **discordants** →
`resultat: "contradictoire"` + question clé générée (jamais tranché en silence) ; ni A ni
B → `indetermine`. Statut à part `retiree` pour les résolutions retirées / non soumises au
vote.
Sortie : {chunk_id, source_file, date, n° résolution, décompte, proclamation_detectee,
resultat, source_resultat, confiance, flags}. Tests unitaires sur cas réels : PV de
l'incident, unanimité sans chiffres, tableau illisible + conclusion nette, proclamation
ACTIVE post-décompte (« l'assemblée approuve » en clôture = résultat), discordance,
résolution tronquée.

Règle courte pour le Bloc 4 des instructions (C5) : « le texte d'une résolution se lit
dans l'ordre : ce qui précède le décompte des voix est la proposition soumise au vote ;
seule la conclusion qui suit le décompte, ou clôt la résolution, établit le résultat —
quelle que soit sa formulation. Sans décompte ni conclusion visibles, le résultat n'est
pas établi. » Protocole complet (5 cas) répliqué dans les skills `*-note-juridique` et
`*-fiche-decision` — pas de skill dédié « lecture PV » (compétence transverse, pas une
tâche : problème de déclenchement permanent).

**C1 LIVRÉ (01/09)** : `resolution_index.py` + `tests/test_resolution_index.py` (10 cas,
tous verts). Smoke prod sur 38 541 chunks PV_AG : 5 546 résolutions à résultat établi
(4 614 adoptées, 352 rejetées, 343 retirées, sources : 2 258 proclamation seule / 2 217
décompte+proclamation / 834 décompte seul), 236 contradictoires correctement REMONTÉS.
Leçons pour C2 : (a) le KPI se mesure PAR RÉSOLUTION, pas par chunk — 85,6 % des chunks
sont des fragments sans vote (feuilles de présence, annexes, suites de longues
résolutions « [Suite résolution N] ») ; C2 doit REGROUPER les chunks d'une même
résolution avant d'indexer (marqueurs de suite + chunk_index + resolution_category) ;
(b) les PV tabulaires modernes portent une « base de calcul : N tantièmes » (assiette,
pas un vote — garde-fou codé) et des votes détaillés par copropriétaire ; (c) revue
d'échantillon des 236 contradictoires à faire en C2 après regroupement.

### C2 — Table `resolutions` (le nœud décisionnel du graphe)
Alimentée par C1 à l'ingestion (extension 09 ou étape dédiée post-06b) :
`(resolution_id, code_ncg, source_file, chunk_ids[], date_ag, numero, objet_court,
decompte_pour, decompte_contre, decompte_abstention, article_majorite, resultat, confiance)`.
`objet_court` peut être titré par Haiku (risque faible, texte entier fourni) ; `resultat`
est TOUJOURS calculé. Sert : questions_cles (approbation des comptes par exercice),
`*-fiche-decision` (historique décisionnel fiable), et à terme un tool de requête dédié.

**C2 LIVRÉ (01/09)** : regroupement par résolution dans `resolution_index.py`
(`group_chunks`/`index_document` — marqueur « [Suite résolution …] » du chunker, numéro
extrait ordinaux/tabulaires, objet_court déterministe, flag groupe_orphelin) ; table
`resolutions` (06a) ; loader `09b_resolutions.py` (--copro/--all, DELETE+INSERT per-copro,
resolution_id content-addressed, filtre des groupes hors-vote). Garde-fous de plausibilité
issus de la revue des contradictoires : décompte pour+contre=0 invalide ; en PV TABULAIRE
(« base de calcul »/« type de vote », format ATHOME : vote détaillé PAR copropriétaire),
seules les formes fortes (« ont voté pour ») valent décompte — les POUR/CONTRE nus sont
des miettes de tableau. Run prod (19 copros NCG) : 12 620 résolutions en table (20 532
groupes hors-vote écartés), **4 876 résultats établis (38,6 %)** : 4 302 adoptées, 261
rejetées, 313 retirées ; contradictoires 223 → **66** après plausibilité (revue du solde
à poursuivre) ; 7 678 indéterminées À SIGNAL = matière des questions clés. 12+2 tests.
Anomalie data relevée : 5440 ASFL RODIN = 14 040 chunks PV_AG pour 400 m² (classification
à auditer, hors C2).

### C3 — Réécriture de 09_copro_synthese.py → générateur de fiche v2
Remplace le narratif Haiku par la construction déterministe du JSON §2 (SQL + RNIC via
l'attribut immatriculation + refs Assynco + C1/C2 + règles §4). Le champ `narratif` est
retiré (pas conservé « au cas où » : pas de transitoire). Watermark de fraîcheur conservé
tel quel. Coût de génération ≈ 0 (titrage optionnel seul).

### Déploiement multi-tenant (décision Thai 01/09)
L'ordre de rollout : **NCG (+ NGE) d'abord** — C1/C2 y sont déjà en prod — puis, une fois
les résultats validés, **Delacour** (via sa branche `PALIM_Delacour_Patrimoine` et sa RDS)
et **CSG Cabinet Saint Germain** (sa RDS, 1 copro Lacépède — migration de quelques
minutes). Pour chaque tenant : `06a_init_db.py` (table resolutions) + `09b_resolutions.py
--all` + fiche v2 (C3) + le deploy v12 qui touche déjà toutes les Lambdas. La migration DB
accompagne le deploy, jamais l'inverse (un tool v2 sans table = dégradé silencieux).

**C3 LIVRÉ (01/09)** : `09_copro_synthese.py` réécrit en générateur d'ANNUAIRE — narratif
Haiku supprimé, **zéro LLM, 0 $**. Écrit dans les colonnes NEUVES `faits_v2` /
`fiche_version` / `fiche_v2_generated_at` (06a) : `narratif` et `faits` v1 intacts, donc
**zéro impact prod avant le deploy v12** (rollback = ignorer les colonnes). Sections :
identité (immat + POINTEURS mandat/CS vers la dernière résolution adoptée, jamais de nom
extrait) ; chiffres SQL (docs, chunks, doc_types, dossiers, résolutions par résultat) ;
dossiers chauds (règles + `motif_selection` + pointeurs source_files/chunk_ids, repli sur
les documents liés quand `chunks.dossier_id` est vide) ; **questions clés** dérivées par
règles (R1 exercice sans approbation acquise = le cas de l'incident, R2 résolutions au
sens non établi, R3 dossiers > 18 mois, R4 AG manquante, R5 sinistre Assynco sans pièce) ;
PV récents (filtrés : seuls les documents portant ≥1 résolution établie — les feuilles de
présence et VPC classées PV_AG polluaient l'annuaire). `09b` câblé AVANT `09` dans
`ingest.py` (même gate PV_AG/SINISTRE). Run prod 19 copros : **54 questions clés, 110
dossiers chauds, 0 $**. Exemples réels : 5750 « comptes 2021 approuvés ? » + 6 dossiers
> 18 mois ; 5390 trois exercices sans approbation acquise + 1 sinistre Assynco sans pièce.

### C4 — Contrat MCP `PALIM_copro_overview` v2 (deploy v12)
Sortie = fiche v2 + merge Assynco live (inchangé) + `freshness` (inchangé). Docstring
réécrite : « la fiche est un GUIDE DE CONSULTATION : suivre les pointeurs (get_chunks,
search_dossiers, get_full_document) avant d'affirmer ; elle ne fonde aucune citation ».
Champ narratif supprimé + schéma de sortie nouveau = **bump produit MAJEUR** des
Instructions (politique du 27/08), embarqué avec le deploy v12 déjà prévu (log_feedback v2).

### C5 — Instructions (template + NCG + Delacour + CSG)
Bloc « fiche de synthèse » réécrit : pattern d'usage imposé — overview → suivre les
pointeurs pertinents → répondre sourcé depuis les chunks ; les `questions_cles` sont des
pistes d'instruction, jamais des réponses ; toute donnée `à confirmer` se revalide avant
citation externe. Même numéro produit que le deploy v12 (bump majeur commun).

### C6 — Recette
- **Golden case n°1 (l'incident)** : la fiche v2 de la copro Delacour doit produire la
  question « comptes 2023 approuvés ? » pointant rés. 3 ET l'entrée `resolutions` doit
  porter `resultat=rejetee` (calculé : 2 606 / 4 867 / 58) ; rés. 4 (comptes 2025) →
  `adoptee`. Puis test bout-en-bout : le LLM appelant (harness agentique) répond « rejetés
  le 30/03/2026 » en citant le PV.
- Tests unitaires C1 (décomptes variés, OCR dégradé → indetermine, unanimité, art. 24/25).
- Batch de régénération des fiches servies (NCG + Delacour) + spot-check de 5 fiches.
- Golden case branché au chantier self-learning (rejoué à chaque deploy).

**C4+C5 LIVRÉS (01/09, `3b5231c`)** — embarqués au deploy v12.
C4 : `get_overview` sert 3 régimes explicites via `fiche_version` — v2 (annuaire, champs
`fiche`+`usage`, plus de narratif), v1 (ancien narratif + champ `avertissement` : statut de
source le plus bas — pour Delacour/CSG non migrés), aucune (faits live). Repli explicite,
jamais silencieux : colonnes absentes → try/except → v1. Testé sur les 3 régimes en prod.
Docstring du tool réécrite (le contrat que lit le LLM) : la fiche oriente et pointe,
`questions_cles` = pistes d'instruction jamais des réponses, interdiction de citer un vote
/ montant / comptage depuis la fiche.
C5 : NCG **v4.0** (bump MAJEUR, contrat de sortie modifié) = bloc complet fiche v2 (5
sections, pattern d'usage imposé, interdits, rappel du critère positionnel de lecture d'un
PV). Delacour **v1.2** et CSG **v1.1** = doctrine SEULE (leur base sert encore le narratif
v1 : décrire un annuaire qu'ils n'ont pas serait mensonger), avec la note que la v2 arrive
avec la migration de leur déploiement. Template + AGENTS.md alignés.
RESTE : C6 (recette golden case + bout-en-bout) puis deploy v12 et recollage des 3 clients.

**C6 LIVRÉ (01/09)** : `tests/recette_fiche_v2.py` — recette REJOUABLE PAR TENANT
(`PALIM_CLIENT=...`), critère de sortie de chaque rollout et sonde de non-régression après
chaque deploy. Elle prouve des propriétés, pas l'absence d'exception : I1 structure (5
sections, aucune clé de prose libre), I2 pointeurs présents, **I3 intégrité référentielle
de TOUS les pointeurs** (un chunk_id / resolution_id / dossier_id mort enverrait le LLM
dans le vide), I4 cohérence des chiffres avec la base (le symptôme v1 : narratif 25
dossiers vs faits 37 dans la même fiche), I5 aucune résolution établie sans source, I6
contrat MCP (`fiche_version=v2`, aucun narratif servi), G golden case « comptes de
l'exercice N ». **Run NCG : 19/19 fiches, 54 questions, 110 dossiers chauds, 1 463
pointeurs vérifiés, 0 échec.**
Trouvaille de recette (vérification sur pièces) : la question « comptes 2021 » de 5750 est
un VRAI POSITIF de prudence — le texte s'arrête après « Votent POUR 6 Associés totalisant
84025/84025 tantièmes », sans contre ni proclamation : l'adoption n'est pas établissable,
la question est légitime, le lecteur tranche sur pièce. Design validé par le cas réel.
Au passage : « ASSOCIES » (ASL/AFUL) ajouté aux qualificatifs de décompte. Golden case
branché à `PLAN_SELF_LEARNING.md` (rejoué à chaque deploy).

## 4. Questions clés — règles de dérivation initiales (toutes sur données calculées)

| Règle | Source | Exemple produit |
|---|---|---|
| Exercice comptable sans approbation `adoptee` détectée | C2 (objet ~ « comptes exercice N ») | « Comptes 2023 et 2024 : aucune approbation acquise — voir rés. 3 PV 30/03/2026 (rejetée) » |
| Résolution à `resultat=indetermine` (OCR) | C2 | « Rés. 7 du PV … : décompte illisible, vérifier le PV papier » |
| Mandat de syndic sans échéance future détectée | C2 / documents | pointeur PV du dernier mandat |
| Contrat à échéance < 12 mois ou sans date connue | documents (statut/dates) | pointeur contrat |
| Sinistre ouvert depuis > 18 mois | dossiers | pointeur dossier + ref Assynco |
| Écart dossiers RAG vs Assynco live | overview (déjà mergé) | « 3 sinistres Assynco sans dossier documentaire » |

La liste est extensible ; chaque règle nouvelle doit produire question + pointeurs, jamais
une conclusion.

## 5. Abandonné (du plan v1 du 28/08) — assumé, sans transition

Gate post-génération, prompt durci, colonne `narratif_statut`, régénération corrigée du
narratif : sans objet, il n'y a plus de narratif précalculé. L'ancien narratif reste servi
tel quel jusqu'au deploy v12 — la doctrine d'usage (« la fiche oriente, les sources
tranchent ») entre elle dans les instructions SANS attendre, car elle est permanente et
vaut pour v1 comme v2. Backlog lointain : mini-narratif optionnel généré depuis la fiche
v2 validée elle-même (pas depuis les chunks) — hors chemin, il réintroduirait de la
génération libre.

## 6. Ordre, effort, dépendances

| Étape | Contenu | Effort | Dépend de |
|---|---|---|---|
| 1 | C5 doctrine dans les instructions (dates bumpées) + golden case consigné (self-learning F1) | 1 h | — |
| 2 | C1 détecteur + tests (dont PV incident) | 1/2 session | — |
| 3 | C2 table resolutions + alimentation | 1/2 session | C1 |
| 4 | C3 fiche v2 (09 réécrit) + batch local | 1 session | C1, C2 |
| 5 | C4 overview v2 + C5 bump majeur | portés par deploy v12 | C3 |
| 6 | C6 recette bout-en-bout | 1/2 session | C4 |

Total ≈ 2,5 sessions + le deploy v12 déjà planifié. Aucun chantier jetable : C1/C2
servent aussi `*-fiche-decision` et le futur tool de requête sur les résolutions.
