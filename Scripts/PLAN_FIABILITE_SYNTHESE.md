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

### C1 — Détecteur de résolutions à décompte (module pur, fondation)
`resolution_index.py` : sur les chunks PV_AG (déjà chunkés par résolution), détection
regex du décompte (POUR/CONTRE/abstentions/tantièmes, art. 24/25/26) et du **résultat
CALCULÉ depuis les nombres** (adoptée/rejetée), jamais déduit du verbe du dispositif —
c'est le cœur anti-incident. Sortie : {chunk_id, source_file, date, n° résolution,
décompte, resultat_calcule, confiance}. Décompte illisible (OCR) → `resultat: "indetermine"`
+ pointeur, jamais de devinette. Tests unitaires sur cas réels, dont le PV de l'incident.

### C2 — Table `resolutions` (le nœud décisionnel du graphe)
Alimentée par C1 à l'ingestion (extension 09 ou étape dédiée post-06b) :
`(resolution_id, code_ncg, source_file, chunk_ids[], date_ag, numero, objet_court,
decompte_pour, decompte_contre, decompte_abstention, article_majorite, resultat, confiance)`.
`objet_court` peut être titré par Haiku (risque faible, texte entier fourni) ; `resultat`
est TOUJOURS calculé. Sert : questions_cles (approbation des comptes par exercice),
`*-fiche-decision` (historique décisionnel fiable), et à terme un tool de requête dédié.

### C3 — Réécriture de 09_copro_synthese.py → générateur de fiche v2
Remplace le narratif Haiku par la construction déterministe du JSON §2 (SQL + RNIC via
l'attribut immatriculation + refs Assynco + C1/C2 + règles §4). Le champ `narratif` est
retiré (pas conservé « au cas où » : pas de transitoire). Watermark de fraîcheur conservé
tel quel. Coût de génération ≈ 0 (titrage optionnel seul).

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
