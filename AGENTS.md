# AGENTS.md — Guide de navigation du code base PALIM

> Pour un LLM qui débarque sur ce repo. Carte du terrain, pas un cours.
> PALIM = assistant IA RAG pour la gestion de copropriété (syndic). Client : NCG.
> Voir `CLAUDE.md` (racine) pour les **règles strictes** (git Google Drive, encodage, st.secrets) — à lire en plus de ce fichier.
> Dernière mise à jour : 29 mai 2026.

---

## 1. Ce que fait le projet, en une phrase

Pipeline d'ingestion de documents de copropriété (PDF/Word/Excel scannés) → PostgreSQL + pgvector, exposé via une app Streamlit RAG qui répond à des questions juridiques/opérationnelles avec citations de sources.

**Direction produit** : on migre de l'UI Streamlit custom vers **Claude Cowork + backend en tools MCP** (modèle LillySalesBot). Streamlit devient un harness de test. Le pipeline d'ingestion et le schéma DB restent le socle.

---

## 2. Stack

| Couche | Techno |
|--------|--------|
| Frontend | Streamlit Cloud (déployé **depuis `main` uniquement**) |
| LLM | AWS Bedrock — Sonnet 4.6 (génération), Haiku 4.5 (stratégie, classification, filtrage) |
| Embeddings | Amazon Titan Embed Text V2 (1024 dims) |
| Rerank | Cohere rerank-v3.5 sur Bedrock **eu-central-1** (pas dispo eu-west-1) |
| DB | PostgreSQL RDS `sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com` + pgvector |
| OCR | AWS Textract (async, via S3) |
| Observabilité | Langfuse Cloud EU — **pin `langfuse==2.60.4`** (v3 casse `.trace()`) |
| Python | **3.12** (`.python-version` à la racine — requis Langfuse/Pydantic V1) |

---

## 3. Carte des fichiers

```
202512 Mission Déploiement IA interne/
├── CLAUDE.md                    # Règles strictes + contexte (LIRE EN PREMIER)
├── AGENTS.md                    # Ce fichier
├── .python-version              # 3.12
├── requirements.txt             # deps racine
├── Scripts/                     # ⭐ TOUT le code vit ici
│   ├── pipeline_config.py       # Source de vérité : map code NCG→dossier, helpers paths per-copro
│   ├── 00_inventaire.py         # Étape 0 : inventaire des fichiers d'archives
│   ├── 01_filtrage.py           # Étape 0.2 : tri plans/photos/inutiles (règles + Vision Sonnet)
│   ├── 02_extraction_optimized.py  # Étape 2 : extraction texte (Textract fire-all-then-collect)
│   ├── 03_chunking.py           # Étape 3 : classif doc_type (3 passes) + chunking + BORDEREAU_AR
│   ├── 04_metadata_documents.py # Étape 4 : métadonnées doc-level via Haiku (protection RCP)
│   ├── 05_embedding.py          # Étape 5 : embeddings Titan (parallèle, incrémental)
│   ├── 05b_synthetic_questions.py  # Étape 5b : questions synthétiques Haiku (boost BM25)
│   ├── 05c_entity_extraction.py # Étape 5c : entités sinistre → dossiers auto
│   ├── 06a_init_db.py           # Étape 6a : CREATE TABLE + index (schéma DB — voir §5)
│   ├── 06b_load_db.py           # Étape 6b : TRUNCATE + INSERT chunks/documents/dossiers
│   ├── 07_query_rag_ui.py       # App Streamlit LOCALE (avec FlashRank) — variante dev
│   ├── 08_airtable_sync.py      # Étape 8 : sync dossiers sinistres Airtable Assynco (OBLIGATOIRE après 06b)
│   ├── run_pipeline_per_copro.py   # Orchestrateur : enchaîne 01→05b pour 1 copro
│   ├── pipeline_config.py       # (cf. plus haut)
│   ├── content_filter.py        # Filtre contenu binaire (importé par 03)
│   ├── diag_*.py / debug_*.py   # Scripts de diagnostic jetables (one-off, pas du code prod)
│   └── Streamlit Cloud/         # ⭐ App DÉPLOYÉE
│       ├── streamlit_app.py     # App prod (2900+ lignes) — UI + orchestration retrieval
│       ├── dossiers_api.py      # Logique métier dossiers/sinistres (UI-agnostique)
│       ├── analytics.py         # Route analytique : NL→spec JSON whitelist→SQL paramétré
│       ├── VERSION              # Version affichée en sidebar (actuellement 0.6.1)
│       ├── requirements.txt     # deps de l'app déployée
│       └── secrets_template.toml
├── Données brutes/              # Archives sources par copro (gitignored)
└── Résultats bruts/             # Sorties pipeline + guide (jsonl/csv gitignored)
    └── rag-prototype-guide.md   # Mémoire complète du pipeline (à maintenir)
```

**Deux apps Streamlit coexistent** : `Scripts/07_query_rag_ui.py` (locale, avec FlashRank) et `Scripts/Streamlit Cloud/streamlit_app.py` (déployée, sans FlashRank, `RERANK_CANDIDATES=200` pour compenser). **Modifier la version Cloud** pour tout ce qui touche la prod.

---

## 4. Pipeline d'ingestion (ordre obligatoire)

Mode **per-copro** (recommandé) : chaque script prend `--copro <code>`, résout ses paths via `pipeline_config.py`, écrit dans `per_copro/{code}/`. Lançable en parallèle (1 process par copro).

```
01_filtrage → 02_extraction → 03_chunking → 04_metadata → 05_embedding → 05b_synthetic_questions
```
puis **globaux** (une fois, après tous les copros) :
```
06b_load_db (TRUNCATE + INSERT)  →  08_airtable_sync (OBLIGATOIRE : 06b efface les chunks virtuels Airtable)
```

`run_pipeline_per_copro.py --copro 5033` enchaîne 01→05b. Flags `--from / --only / --skip`.

**Gotchas** (détail complet dans `CLAUDE.md` §4) :
- `05_embedding.py` est incrémental (append) → **supprimer l'ancien `chunks_avec_embeddings*.jsonl`** avant re-run.
- `06b` fait TRUNCATE → perte des chunks Airtable → `08` obligatoire ensuite.
- Préfixer toute commande Python par `PYTHONIOENCODING=utf-8` (console Windows cp1252 crashe sur emojis).

**10 copros actuellement en DB** (`INCLUDED_COPROS` dans `pipeline_config.py`) : 5033, 5354, 5390, 5427, 5480, 5499, 5548, 5553, 8030, 8050.

---

## 5. Schéma DB (`06a_init_db.py`)

| Table | Rôle | Colonnes clés |
|-------|------|---------------|
| `chunks` | Unité de retrieval | `chunk_id` (PK), `copropriete`, `code_ncg`, `doc_type`, `text`, `embedding vector(1024)`, `text_search tsvector` (BM25 FR), `resolution_category`, `synthetic_questions`, `dossier_id` |
| `documents` | Métadonnées doc-level (1 ligne/fichier source) | `source_file` (PK), `doc_type`, `doc_type_corrige`, `annee`, `sous_type`, `statut`, `dossier_lie`, `montant_principal`, `resume` |
| `dossiers` | Sinistres/travaux/contentieux (sync Airtable) | `dossier_id` (PK), `type_dossier`, `statut`, `lese_nom`, `expert_nom`, `assureur`, `etapes` (JSONB), `montant_estime`, `airtable_record_id`, colonnes pipeline `at_*` |
| `chat_sessions` | Persistance conversation (résilience mobile) | `updated_at` |

**Index** : IVFFlat sur `embedding` (cosine), GIN sur `text_search` (BM25) et `themes`, btree sur `copropriete`/`code_ncg`/`doc_type`/`annee`.

**doc_types valides** (`DOC_TYPES_VALID` dans `03_chunking.py`) : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, PLAN, ASSURANCE, ENTRETIEN, SINISTRE, COMPTABILITE, BORDEREAU_AR.

---

## 6. Retrieval (cœur de `streamlit_app.py`)

Pipeline d'une requête :
```
detect_strategy_haiku()   → Haiku classifie (inventaire/ciblé/équilibré) + extrait filtres structurels (~300ms)
search_chunks()           → pré-filtrage documents → Vector + BM25 → RRF fusion → diversité par source
                            (Cloud : pas de FlashRank, RRF direct + RERANK_CANDIDATES=200)
build_llm_payload()       → assemble le contexte
generate_answer_stream()  → Sonnet 4.6, streaming, citations [Source N]
```

**Fonctions d'entrée à connaître** (`Scripts/Streamlit Cloud/streamlit_app.py`) :
- `detect_strategy_haiku()` / `detect_retrieval_strategy()` — routage stratégie.
- `search_chunks()` — retrieval principal (vector+BM25+RRF). `search_decomposed()` pour requêtes temporelles multi-sous-requêtes.
- `generate_answer_stream()` — génération LLM.
- `classify_prompt_relevance()` — filtre prompts hors-sujet (Langfuse).

**Constantes de tuning** (haut de `streamlit_app.py`) : `MAX_CHUNKS_LLM_DEFAULT=50`, `_BROAD=80`, `_TEMPORAL=120`, `TOP_K_DISPLAY=20`, `MAX_CHUNKS_PER_SOURCE=3`, `RERANK_CANDIDATES=200`, `_CTX_VEC_MIN=0.25` (seuil double retrieval contextuel).

**Modules métier UI-agnostiques** (à ne PAS mélanger avec l'UI — voir §7) :
- `dossiers_api.py` — accès/logique dossiers sinistres. `get_dossiers()`, `get_dossier_detail()`, `search_dossiers_for_query()`.
- `analytics.py` — route analytique multi-copro. Le LLM **ne génère jamais de SQL brut** : il mappe vers une spec JSON sur **liste blanche** (`detect_analytical_query`), un builder déterministe produit du SQL **paramétré** (`build_analytical_sql`), `run_analytical_route` formate. Identique pour 1, 10 ou 150 copros.

---

## 7. Règles d'or (détail dans CLAUDE.md)

1. **`streamlit_app.py` = UI SEULEMENT**. Toute logique retrieval/filtrage/ranking/agrégation va dans un module dédié (`dossiers_api.py`, `analytics.py`, ou un nouveau `retrieval.py`). L'app importe et affiche.
2. **Git sur Google Drive** : locks `.git/index.lock` permanents. Pattern obligatoire avant CHAQUE commande git : `taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f ".git/index.lock"; git <cmd>`. **Ne jamais tuer GoogleDriveFS.exe.**
3. **Toujours merger dans `main`** après commit (Streamlit Cloud déploie depuis main seulement).
4. **Paths accentués** : utiliser le path exact avec accents (`Déploiement`, pas `Deploiement`), `pathlib.Path` en Python, guillemets doubles en bash.
5. **`PYTHONIOENCODING=utf-8`** sur toute commande Python (Windows).
6. **`st.secrets`** : accès par clé `st.secrets["section"]["KEY"]` dans try/except. **Jamais `.get()`**.
7. Préférer **classification Haiku/LLM** aux regex fragiles quand les deux sont viables. Rigueur juridique : les PV d'AG sont des documents légaux, pas à paraphraser.

---

## 8. Contraintes & cap futur

- **Échelle cible : 150 copros (~2,5M chunks)**. Le scan exact + full-scan actuels ne scalent pas → besoin ANN + routage grossier par copro. Tout concevoir dans cette optique.
- Sur requête **ambiguë/générique**, proposer un multi-select des copros pertinentes plutôt que deviner.
- Refacto enregistrée : sortir la logique métier de `streamlit_app.py` vers des modules.

---

## 9. Où chercher quoi

| Besoin | Fichier |
|--------|---------|
| Règles projet, bugs corrigés, secrets/hosts | `CLAUDE.md` |
| Map copros, paths per-copro | `Scripts/pipeline_config.py` |
| Schéma DB exact | `Scripts/06a_init_db.py` |
| Classification doc_type, chunking | `Scripts/03_chunking.py` |
| Logique retrieval/ranking | `Scripts/Streamlit Cloud/streamlit_app.py` (`search_chunks`) |
| Dossiers sinistres | `Scripts/Streamlit Cloud/dossiers_api.py` + `08_airtable_sync.py` |
| Agrégations analytiques sécurisées | `Scripts/Streamlit Cloud/analytics.py` |
| Mémoire complète du pipeline (archi, décisions) | `Résultats bruts/rag-prototype-guide.md` |
| ERP assurance Assynco (Airtable) | skill `assynco-erp` |
```
