# Plan — Registre d'ingestion PALIM (mémoire d'état du pipeline)

> Statut : **P0 LIVRÉ ET EXÉCUTÉ** sur NCG et Delacour le 20/08/2026 (cf. §9).
> P1 à P3 restent à faire. Rédigé le 20/08/2026.
> Modèle de référence : `ingestion_registry` de LillySalesBot
> (`G:\Mon Drive\Projet SmarterPlan\Données & Scripts\LLB\Scripts\ingestion_framework\`),
> transposé du **dossier d'opportunité** (unité LLB) vers le **document** (unité PALIM).
> Chantier parent : `PLAN_SCALE_150_COPROS.md`.

---

## 1. Le problème que ça résout

Aujourd'hui l'état de l'ingestion PALIM vit dans des fichiers épars sur le poste de travail :
`filtrage_rapport.json` (décisions du 01), `dedup_manifest.json` (00b), `extraction_checkpoint.json`
(02), `chunks.jsonl` (03), `documents_metadata.jsonl` (04). Rien ne relie un fichier source à son
sort final, et **aucun de ces fichiers ne dit pourquoi un document n'est pas arrivé en base**.

Conséquence mesurée le 20/08/2026 : **2 731 documents chez Delacour (22,4 M de caractères, 14 % du
texte du parc, dont au moins 381 PV/AG) et 2 356 chez NCG** ont été extraits sans jamais produire un
seul chunk, avalés par la dédup par similarité de `03_chunking.py`. Il a fallu croiser quatre artefacts avec un
script d'audit jetable pour s'en apercevoir. Avec le registre, c'est une requête :

```sql
SELECT motif, count(*) FROM ingestion_registre WHERE statut = 'REJETE' GROUP BY 1 ORDER BY 2 DESC;
```

Le registre est de l'**observabilité du pipeline**, pas une brique produit. Il ne change aucun
comportement d'ingestion. Il rend seulement visible et interrogeable ce que le pipeline décide déjà.

## 2. Ce que fait LLB, et ce qu'on en reprend

| Brique LLB | Reprise PALIM | Commentaire |
|---|---|---|
| Table `ingestion_registry` en base, 7 statuts | **Oui** | Cœur de ce plan |
| Unité = dossier d'opportunité | **Non**, unité = document | Une copro PALIM porte jusqu'à 3 800 fichiers, un dossier LLB quelques dizaines |
| Ré-ingestion `DELETE WHERE opportunite_id` + réinsert | Déjà là | `06b --copro` fait l'upsert per-copro |
| Delta API Graph + jeton persistant | **Hors scope**, phase ultérieure | L'équivalent Drive est l'API `changes` |
| Fenêtre de stabilisation 14 j | **Hors scope**, phase ultérieure | Suppose le delta |
| Cycle serverless quotidien (Lambda + EventBridge) | **Hors scope**, phase ultérieure | Aujourd'hui tout part du poste |

Ce plan couvre uniquement la première ligne. Les trois dernières deviennent faciles une fois le
registre en place (elles n'ont plus qu'à écrire dedans), et coûteuses tant qu'il n'y est pas.

## 3. Hypothèses posées

1. **Unité = le document**, identifié par `source_file` (chemin relatif préfixé du dossier copro,
   exactement la valeur portée par `chunks.source_file`). Jointure directe avec `chunks` et
   `documents`, aucune clé de correspondance à maintenir.
2. **Le registre vit dans la base du client** (RDS Postgres), écrit par `ragadmin` comme le reste du
   pipeline. Pas de SQLite local : l'intérêt est de croiser le registre avec `chunks` en SQL.
3. **Le MCP ne le lit pas.** `mcp_*_reader` n'a aucun GRANT dessus. Le registre est un outil
   d'exploitation, il ne remonte jamais au LLM du client.
4. **Écriture non bloquante par défaut.** Une base injoignable dégrade l'observabilité, elle ne doit
   pas arrêter une ingestion. `PALIM_REGISTRE_STRICT=1` inverse le comportement pour les batches de
   nuit, où un registre muet est pire qu'un échec visible.
5. **Aucun changement de comportement du pipeline.** Aucune décision d'ingestion ne dépend du
   registre en V1. Il observe, il ne pilote pas. (La détection des suppressions le rejoint en V2,
   cf. §7.)

## 4. Schéma

Ajouté à `06a_init_db.py`, dans le style des tables existantes (`CREATE TABLE IF NOT EXISTS` +
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` pour les installations en place).

```sql
-- ── Table ingestion_registre : mémoire d'état du pipeline, unité = document ──
-- Écrite par 01/00b/02/03/06b via registre.py. Jamais lue par le MCP.
-- source_file = chemin relatif préfixé du dossier copro, identique à chunks.source_file.
CREATE TABLE IF NOT EXISTS ingestion_registre (
    source_file     TEXT PRIMARY KEY,
    code_ncg        TEXT NOT NULL,
    nom_fichier     TEXT,
    taille_octets   BIGINT,
    sha256          TEXT,          -- posé par 00b, regroupe les doublons exacts
    signature       TEXT,          -- "taille:mtime_ns", celle du checkpoint de 02

    statut          TEXT NOT NULL DEFAULT 'DECOUVERT'
        CHECK (statut IN ('DECOUVERT','EXTRAIT','INGERE','REJETE','SUPPRIME','ERREUR')),
    motif           TEXT           -- pourquoi REJETE/ERREUR, NULL sinon (cf. §5)
        CHECK (motif IS NULL OR motif IN (
            'FILTRAGE_PHOTO','FILTRAGE_PLAN','FILTRAGE_SYSTEME','FILTRAGE_AUTRE',
            'FILTRAGE_GOOGLE_NATIF','DOUBLON_EXACT','TEXTE_VIDE','NON_EXPLOITABLE',
            'DOUBLON_PROCHE','EXTRACTION_KO','CHARGEMENT_KO','COPIE_KO')),
    etape           TEXT,          -- '01','00b','02','03','06b' : qui a décidé
    ref_source_file TEXT,          -- document conservé, si rejeté comme doublon
    score           NUMERIC,       -- similarité ayant motivé un DOUBLON_PROCHE

    doc_type        TEXT,          -- rempli par 03 (doc_type_corrige par 04 si dispo)
    nb_caracteres   INTEGER,       -- texte extrait par 02
    nb_chunks       INTEGER,       -- chunks produits par 03

    run_id          TEXT,          -- run ayant produit le statut courant
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- dernier scan où la source portait le fichier
    last_ingest     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_registre_copro ON ingestion_registre (code_ncg, statut);
CREATE INDEX IF NOT EXISTS idx_registre_motif ON ingestion_registre (statut, motif);
CREATE INDEX IF NOT EXISTS idx_registre_sha   ON ingestion_registre (sha256);
CREATE INDEX IF NOT EXISTS idx_registre_run   ON ingestion_registre (run_id);

-- ── Table ingestion_runs : un batch = une ligne, pour le rapport de fin de cycle ──
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id      TEXT PRIMARY KEY,     -- "<code>-<horodatage ISO compact>"
    code_ncg    TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    ok          BOOLEAN,
    stats       JSONB                 -- compteurs par statut/motif, coût, durée par étape
);
```

## 5. Cycle de vie et motifs

```
                 01_filtrage
   (source) ──────────────────► DECOUVERT ─────► ... ─────► INGERE
        │                            │                        │
        │ écarté                     │ écarté                 │ absent du scan suivant
        ▼                            ▼                        ▼
      REJETE                       REJETE                  SUPPRIME
   FILTRAGE_*                   DOUBLON_EXACT (00b)
                                TEXTE_VIDE (02)
                                NON_EXPLOITABLE (03, verdict SKIP)
                                DOUBLON_PROCHE (03, similarité) ← 2 731 Delacour + 2 356 NCG
```

Six statuts, orthogonaux au motif :

| Statut | Sens | Posé par |
|---|---|---|
| `DECOUVERT` | Vu à la source et retenu par le filtrage, pas encore traité | 01 |
| `EXTRAIT` | Texte extrait, en attente de chunking | 02 |
| `INGERE` | Chunks présents en base | 06b |
| `REJETE` | Écarté volontairement, `motif` dit par quelle règle | 01, 00b, 02, 03 |
| `SUPPRIME` | Disparu de la source, retiré de la base | ingest.py |
| `ERREUR` | Échec technique, à rejouer | 02, 06b |

**Règle d'arbitrage : le fait prime sur la décision.** Un document présent dans la base est
`INGERE`, même si une étape amont avait décidé de l'écarter. Le cas existe réellement : `00b` retire
un doublon exact de `Archives_Filtrees`, mais son JSON d'extraction issu d'un run antérieur survit
dans `Archives_Extraites`, donc `03` le chunke quand même et il arrive en base. La dédup n'est pas
rétroactive. Ces documents gardent leur `sha256` et leur `ref_source_file`, ce qui rend l'anomalie
interrogeable : `WHERE statut='INGERE' AND ref_source_file IS NOT NULL` liste les doublons qui ont
fui dans le RAG. Le compteur `incoherences` du run porte le détail.

Pour la même raison, `nb_chunks` d'un document `INGERE` porte le **nombre de chunks réellement en
base**, pas celui du shard local. L'écart entre les deux est une dérive tracée dans
`ingestion_runs.stats` (`CHUNKS_HORS_SHARD`, `INGERE_HORS_SHARD`), jamais lissée.

Distinction importante : `REJETE` est une décision de règle (rejouable si la règle change),
`ERREUR` est un incident (rejouable tel quel). C'est ce qui permettra, après le fix de la dédup,
de cibler exactement `statut='REJETE' AND motif='DOUBLON_PROCHE'` pour rattraper le corpus perdu
sans rien re-traiter d'autre.

## 6. Module `registre.py` et points d'écriture

Un seul module, importé par les scripts d'étape. Aucun script n'ouvre sa propre connexion.

```python
run_id = registre.demarrer(code)                  # crée la ligne ingestion_runs
registre.vus(code, run_id, entries)               # bulk upsert DECOUVERT + last_seen (01)
registre.rejeter(code, run_id, source_file, motif, etape, ref=None, score=None)
registre.extrait(code, run_id, source_file, signature, nb_caracteres)   # (02)
registre.chunke(code, run_id, source_file, doc_type, nb_chunks)         # (03)
registre.ingeres(code, run_id, source_files)      # bulk INGERE + last_ingest (06b)
registre.absents(code, run_id) -> [source_file]   # last_seen < début du run
registre.cloturer(run_id, ok, stats)              # rapport
```

Écritures en bloc via `psycopg2.extras.execute_values` : une grosse copro fait 3 500 lignes, il
faut un seul aller-retour par étape, pas 3 500.

| Script | Écriture |
|---|---|
| `01_filtrage.py` | `vus()` pour tout ce qui est GARDER ; `rejeter(FILTRAGE_*)` pour les exclus. C'est la passe qui rafraîchit `last_seen`, donc celle qui date la présence à la source. |
| `00b_dedup.py` | `sha256` sur tout le groupe ; `rejeter(DOUBLON_EXACT, ref=<gardé>)` sur les copies |
| `02_extraction_optimized.py` | `extrait()` en succès ; `rejeter(TEXTE_VIDE)` là où `stats["vides"]` est aujourd'hui un compteur anonyme ; `ERREUR/EXTRACTION_KO` sur échec Textract |
| `03_chunking.py` | `chunke()` en succès ; `rejeter(NON_EXPLOITABLE)` sur verdict SKIP ; `rejeter(DOUBLON_PROCHE, ref=<gardé>, score=<ratio>)` sur la règle de similarité |
| `06b_load_db.py` | `ingeres()` sur les `source_file` effectivement chargés ; `ERREUR/CHARGEMENT_KO` sinon |
| `ingest.py` | `demarrer()` en tête, `cloturer()` en fin, et bascule `SUPPRIME` sur le retour d'`absents()` |

## 7. Ce que le registre remplace, à terme

`ingest.py` détecte aujourd'hui les suppressions en comparant l'instantané DB (`db_snapshot`) à la
source vivante (`live_source_files`). Ça ne voit que les documents **arrivés jusqu'en base** : un
fichier rejeté au filtrage puis supprimé du Drive n'est nulle part. `absents()` généralise la
détection à tout ce que le pipeline a déjà vu. Bascule prévue en V2, une fois le registre validé en
parallèle du mécanisme actuel sur deux ou trois cycles.

## 8. Phasage

| Phase | Contenu | Effort |
|---|---|---|
| **P0 — Backfill** | ✅ **FAIT 20/08/2026** : DDL dans `06a_init_db.py` + `registre_backfill.py`, exécuté sur NCG (10 copros) et Delacour (24 copros). Aucune ré-ingestion. | Livré |
| **P1 — Écritures** | `registre.py` + les 6 points d'écriture + `demarrer`/`cloturer` dans `ingest.py` | Moyen |
| **P2 — Rapport** | `ingest.py --rapport <code>` : tableau des statuts/motifs du dernier run, et diff vs le run précédent | Court |
| **P3 — Bascule suppressions** | `absents()` remplace le diff `db_snapshot`/`live_source_files` | Court, après validation parallèle |

P0 se justifie seul : il rend visible l'état des 24 copros Delacour **sans rien re-traiter**, et
c'est lui qui donnera la liste exacte des documents à rattraper après le fix de la dédup.

## 9. Critères de succès vérifiables — résultats P0 (20/08/2026)

| # | Critère | Delacour | NCG |
|---|---|---|---|
| 1 | Population totale tracée | **23 258** documents | **13 123** documents |
| 1b | dont `INGERE` | 7 853 | 8 417 |
| 2 | `sum(nb_chunks)` des `INGERE` = `count(*) chunks` | **OK** (104 517) | **OK** (166 094) |
| 3 | Orphelins `chunks` sans ligne registre | **0** | **0** |
| 3b | `INGERE` sans aucun chunk | **0** | **0** |

Critères 4 (idempotence), 5 (suppression) et 6 (non-régression) portent sur les écritures à chaud :
ils seront vérifiés en P1, le backfill étant par construction en lecture seule sur le pipeline.

### Rejets par motif

| Motif | Delacour | NCG | Lecture |
|---|---:|---:|---|
| `FILTRAGE_GOOGLE_NATIF` | **6 426** | 0 | Fichiers `.gdoc`/`.gsheet` du Drive partagé, exclus d'office par 01. **28 % de tout ce que le pipeline a vu chez Delacour.** Jamais extraits, jamais ingérés |
| `DOUBLON_EXACT` | 5 285 | 0 | 00b (levier L1), jamais lancé côté NCG |
| `DOUBLON_PROCHE` | **2 731** | **2 356** | La règle de similarité de 03. Touche les deux clients |
| `FILTRAGE_PHOTO` | 757 | 623 | Photos écartées par 01, comportement voulu |
| `TEXTE_VIDE` | 111 | **1 476** | Gardés par 01, aucun JSON d'extraction |
| `NON_EXPLOITABLE` | 49 | 181 | Verdict SKIP du `content_filter` |
| `FILTRAGE_AUTRE` | 46 | 70 | Extensions exclues |

Incohérences relevées par le backfill, toutes tracées dans `ingestion_runs.stats` :

- **37 documents `INGERE_MALGRE_DOUBLON_EXACT`** chez Delacour (19 sur AA6219950, 18 sur AC9872896) :
  supprimés par 00b mais présents dans le RAG, dédup non rétroactive (cf. §5).
- **17 `INGERE_HORS_SHARD`** (AA6219950) : en base mais absents du shard courant, reliquats d'un
  chargement antérieur.
- **`CHUNKS_HORS_SHARD`** : 21 chunks côté Delacour, 85 côté NCG (8050). Chunks d'un chunking
  antérieur qui ont survécu à l'upsert.

### Limite connue du backfill

`DOUBLON_PROCHE`, `NON_EXPLOITABLE` et `TEXTE_VIDE` sont **reconstruits par inférence**, le pipeline
actuel ne journalisant pas ses rejets (cf. docstring de `registre_backfill.py`). Le `TEXTE_VIDE` de
NCG (1 476, soit 11 % du parc) est le plus fragile : un run 02 interrompu ou des artefacts
d'extraction nettoyés depuis produisent la même signature qu'un fichier réellement vide. P1 lève
l'ambiguïté en écrivant le motif au moment où la décision est prise.

## 10. Hors scope explicite

API `changes` de Google Drive, fenêtre de stabilisation, planification serverless, interface de
consultation. Ce sont les suites logiques, elles supposent toutes le registre et aucune ne le
précède.
