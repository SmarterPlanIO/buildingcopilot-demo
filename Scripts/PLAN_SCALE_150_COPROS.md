# PLAN - Scaler le pipeline d'ingestion a 150 copros (laptop-friendly + ajout quotidien de docs)

> Objectif : passer de 10 a 150 copros SANS etouffer le laptop (RAM, disque), et
> rendre TRIVIAL l'ajout quotidien de nouveaux documents par NCG (qui depose des
> docs dans les dossiers de copros tous les jours). Le tout doit "marcher d'un coup".
>
> Perimetre : pipeline d'INGESTION offline (01..08). N'adresse PAS la scalabilite du
> RETRIEVAL (ANN, routage copro) qui est un axe distinct (cf. memoire scale retrieval).

---

## 1. Contraintes (de Thai)

- RAM et disque d'un laptop : ne jamais charger tout le corpus en memoire, ne jamais
  stocker 58 Go de JSONL d'un coup.
- Ajout incremental QUOTIDIEN de docs : NCG ajoute des PDF dans les dossiers de copros
  chaque jour. Ajouter 3 docs ne doit PAS re-traiter / re-embedder la copro entiere.
- Robustesse : idempotent, reprenable, "marche d'un coup".

---

## 2. Etat actuel (ce qui existe deja - a NE PAS refaire)

- `pipeline_config.py` : registre `INCLUDED_COPROS` (code -> dossier brut) + `paths_for(code)`
  qui donne deja tous les paths per-copro (chunks, embeddings, embeddings_sq, dossiers,
  documents_metadata, checkpoints).
- Etages **01..05b supportent `--copro`** : sortent dans `per_copro/<code>/` (shard ~145 Mo/copro).
- **`05_embedding` est INCREMENTAL** : charge les `chunk_id` deja embeddes du shard et ne
  traite que les nouveaux. Append-only. => le levier cout/temps clef est deja la.
- `03_chunking` a un cache doc_type + une logique de skip sur sortie existante.
- `00c` (dedup dossiers) : logique deja per-copro (union-find par copro).

## 3. Ce qui bloque le scale (les 3 trous)

1. **05c** (entites/dossiers) : pas de `--copro`, lit le monolithe racine 3,9 Go et le
   charge ENTIEREMENT en RAM (`all_chunks`). Reecrit aussi le fichier chunks (3,9 Go)
   pour un `dossier_id` qui est DONNEE MORTE en aval.
2. **06b** (load DB) : pas de `--copro`, lit le monolithe, fait un **TRUNCATE global**
   (efface tout, recharge tout). Streame deja ligne par ligne (RAM ok), mais reload total.
3. **Monolithe** `chunks_avec_embeddings_sq.jsonl` (3,9 Go -> ~58 Go a 150) cree par
   `concat_slices.py`. Seuls 05c et 06b le consomment. Streamlit/MCP lisent la DB, jamais
   ces fichiers => le monolithe peut DISPARAITRE sans impact aval.

---

## 4. Principes du redesign

1. **La copro est l'unite de travail ; le document est l'unite d'incrementalite.**
2. **Streaming partout** : iterer ligne par ligne, jamais `all_chunks = [...]` global.
3. **Shards transitoires** : la DB (RDS cloud) est la source de verite. Les shards locaux
   sont jetables, supprimes apres load DB reussi => le laptop ne stocke jamais > 1-2 shards.
4. **Pas de monolithe** : 05c et 06b iterent sur `per_copro/<code>/...`, un shard a la fois.
5. **Upsert, jamais TRUNCATE global** : `DELETE WHERE code_ncg=X` + INSERT par copro.
6. **Detection de delta par document** : on ne traite que les docs nouveaux/changes.

---

## 5. Plan par phases

### Phase 1 - Per-copro + streaming des 2 derniers etages (gain RAM/disque immediat)

- **05c** : ajouter `--copro`. Lire `paths_for(code)["embeddings_sq_jsonl"]` en STREAMING
  (supprimer `all_chunks`, traiter doc par doc). Ecrire `paths_for(code)["dossiers_jsonl"]`.
  **Supprimer la reecriture des chunks** (dossier_id mort en aval). Cout Haiku inchange
  mais RAM ~constante et plus de reecriture 145 Mo.
- **06b** : ajouter `--copro`. Lire le shard de la copro. Remplacer `TRUNCATE chunks/documents`
  par `DELETE FROM chunks/documents/dossiers WHERE code_ncg=%s` + INSERT (deja en streaming).
- **Supprimer `concat_slices.py`** du flux (ne plus produire le monolithe). Garder le script
  archive si besoin ponctuel, mais hors pipeline standard.
- Resultat : pic RAM = un shard streame ; pic disque = quelques Go (shards) au lieu de 58.

### Phase 2 - Incrementalite par document (ops quotidiennes NCG)

- **Source de verite du "deja ingere" = la DB** (pas un manifest a maintenir) :
  `SELECT DISTINCT source_file FROM documents WHERE code_ncg=%s`.
- **Detection delta** : comparer les fichiers presents dans le dossier brut de la copro
  (`raw_source_dir`) au set deja en DB. Nouveaux fichiers = a traiter. (v1 : ADDITIONS
  seulement ; CHANGEMENTS via hash de contenu et SUPPRESSIONS = Phase 4.)
- Chaque etage ne traite que le delta :
  - 01/02 : filtrer/extraire seulement les nouveaux fichiers.
  - 03 : chunker seulement les nouveaux source_files (verifier le skip cross-run ; sinon ajouter).
  - 04 : metadata seulement les nouveaux (cache `metadata_cache.json` deja present).
  - 05/05b : deja incremental (skip chunk_id connus).
  - 05c : re-extraire les nouveaux docs sinistre, re-grouper la copro (folders), 00c re-dedup.
  - 06b : upsert des nouveaux chunks/docs + DELETE+reinsert des dossiers de la copro.
- **A VERIFIER avant de coder** : que 02 et 03 skippent bien les source_files deja traites
  en cross-run (05 oui). Si non, ajouter un skip base sur la DB ou le shard.

### Phase 3 - Orchestration "un coup" + nettoyage disque

- Un driver unique `ingest.py` :
  - `python ingest.py --copro 5390`            # delta auto-detecte, traite + upsert
  - `python ingest.py --copro 5390 --full`     # rebuild complet de la copro
  - `python ingest.py --all`                    # boucle sur INCLUDED_COPROS
  - enchaine 01..06b pour le delta, puis 08 pour la copro, puis (option) supprime le shard
    d'embeddings apres load DB reussi (`--keep-shards` pour garder).
- **Atomicite / reprise** : chaque etage ecrit en `.tmp` + `os.replace` (05c le fait deja).
  Le driver logge l'avancement par copro (checkpoint) pour reprendre apres interruption.
- Secrets : DB via `palim/ragadmin`, Airtable via `palim/airtable_pat` (Secrets Manager).

### Phase 4 - Robustesse registre + 08 dynamique + changements/suppressions

- **`INCLUDED_COPROS`** : a 150, soit on le peuple, soit on le derive en scannant
  `Donnees brutes/` (un dossier = une copro, code en prefixe). Decision a prendre.
- **`08_airtable_sync.COPRO_FILTERS`** (dict code en dur, 10 copros) : le derouler depuis
  `INCLUDED_COPROS` (formule `FIND("(code)",{Name})` generee) au lieu du hardcode.
- **Changements de doc** (meme nom, contenu modifie) : hash de contenu en DB ; si hash
  change -> re-traiter ce doc. **Suppressions** : doc disparu du Drive -> option de purge.

---

## 6. Impact resume sur les contraintes

| Contrainte | Avant | Apres |
|---|---|---|
| RAM | charge 3,9->58 Go en liste Python | un shard streame, ~constant |
| Disque | monolithe 58 Go + shards | shards transitoires, ~qques Go |
| Ajout de 3 docs | re-traite/re-embedde la copro entiere | traite 3 docs, upsert, ~secondes |
| Onboarder copro 151 | reload global TRUNCATE | 1 shard + upsert, n'effleure pas les 150 |
| Cout Bedrock | re-embedde tout | n'embedde que le delta (deja le cas) |

---

## 7. Ordre d'implementation propose

1. Phase 1 (05c streaming + 06b upsert + drop monolithe) : plus gros gain RAM/disque, faible risque.
2. Verifier incrementalite 02/03 (audit) ; combler si trou.
3. Phase 3 driver `ingest.py` + nettoyage shards.
4. Phase 4 (registre dynamique, 08 dynamique, changements/suppressions).

Chaque phase est testable sur 1 copro (8050, le plus gros) avant de derouler `--all`.
