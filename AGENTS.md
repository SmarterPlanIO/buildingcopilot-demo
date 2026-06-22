# AGENTS.md — Guide de navigation du code base PALIM

> Pour un LLM qui débarque sur ce repo. Carte du terrain, pas un cours.
> PALIM = assistant IA RAG pour la gestion de copropriété (syndic). Client : NCG.
> Voir `CLAUDE.md` (racine) pour les **règles strictes** (git Google Drive, encodage, st.secrets) — à lire en plus de ce fichier.
> Dernière mise à jour : 19 juin 2026.

---

## 1. Ce que fait le projet, en une phrase

Pipeline d'ingestion de documents de copropriété (PDF/Word/Excel scannés) → PostgreSQL + pgvector, exposé via un **backend de tools MCP** que le LLM du client (Claude Teams / Cowork) interroge pour répondre à des questions juridiques/opérationnelles avec citations de sources.

**Direction produit (en place)** : le produit livré au client est le **serveur MCP PALIM** plus des **Project Instructions** et des **skills** (modèle LillySalesBot). L'app Streamlit est devenue un **banc de test interne (harness de debug)**, plus le produit. Le pipeline d'ingestion et le schéma DB restent le socle commun. Détail du backend MCP en **§10**, du livrable client en **§11**.

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
│   ├── ingest.py                # ⭐ Driver d'ingestion per-copro de bout en bout (CRUD docs) — voir §4
│   ├── run_pipeline_per_copro.py   # Orchestrateur Tier-1 : enchaîne 01→05b pour 1 copro
│   ├── 09_copro_synthese.py     # Fiche synthèse pré-calculée par copro (table copro_synthese → PALIM_copro_overview)
│   ├── 00a_cost_preflight.py    # Préflight coût ingestion (zéro appel AWS) — modèle de coût/copro
│   ├── 00c_dedup_dossiers_rag.py # Dédup dossiers sinistres (union-find par copro, garde-fous date+nom)
│   ├── rotate_ragadmin.py       # Rotation mot de passe RDS ragadmin (via env, sans secret en clair)
│   ├── PLAN_*.md                # Plans : SCALE_150_COPROS, ANALYTIQUE_INTER_COPRO, REDUCTION_COUT_COPRO, 05C_DEDUP_SINISTRES
│   ├── pipeline_config.py       # (cf. plus haut)
│   ├── content_filter.py        # Filtre contenu binaire (importé par 03)
│   ├── diag_*.py / debug_*.py   # Scripts de diagnostic jetables (one-off, pas du code prod)
│   ├── Streamlit Cloud/         # Banc de test interne (déployé depuis main)
│   │   ├── streamlit_app.py     # Harness (2900+ lignes) — UI + orchestration retrieval
│   │   ├── dossiers_api.py      # Logique métier dossiers/sinistres (UI-agnostique, vendorisé côté MCP)
│   │   ├── analytics.py         # Route analytique : NL→spec JSON whitelist→SQL paramétré
│   │   ├── rerank.py            # Rerank Cohere 3.5 (Bedrock eu-central-1) — partagé app + MCP (vendorisé)
│   │   ├── VERSION              # Version harness Streamlit (actuellement 0.7.1)
│   │   ├── requirements.txt     # deps du harness
│   │   └── secrets_template.toml
│   └── mcp_server/              # ⭐⭐ BACKEND PRODUIT (serveur MCP FastMCP/Lambda) — voir §10-11
│       ├── PALIM_server.py      # Définit les 12 tools @mcp.tool() + app ASGI FastMCP
│       ├── PALIM_retrieval.py   # hybrid_search (vector+BM25+RRF+rerank Cohere) côté MCP
│       ├── PALIM_config.py      # Constantes + ARNs secrets + ASSYNCO_SYNDIC_NCG (isolation tenant)
│       ├── PALIM_db.py          # Connexion RDS read-only (mcp_ncg_reader)
│       ├── PALIM_assynco.py     # ERP assurance Assynco (Airtable) + isolation tenant Syndic=NCG
│       ├── PALIM_overview.py    # Fiche synthèse copro (lit copro_synthese)
│       ├── PALIM_{dossiers,copros,discovery,scope,visites}.py  # helpers des tools
│       ├── PALIM_tracing.py     # Tracing Langfuse optionnel (no-op si pas de clés)
│       ├── Dockerfile / build_and_push.sh / env.json  # Packaging Lambda container + LWA
│       ├── INSTRUCTIONS_NCG_PROJECT.md   # ⭐ Project Instructions client (v1.9) — voir §11
│       ├── RUNBOOK_DEPLOY_V7.md  # Procédure de déploiement Lambda
│       └── skills/              # Skills client : ncg-redaction-livrable, ncg-note-juridique, assynco-erp
├── Données brutes/              # Archives sources par copro (gitignored)
└── Résultats bruts/             # Sorties pipeline + guide (jsonl/csv gitignored)
    └── rag-prototype-guide.md   # Mémoire complète du pipeline (à maintenir)
```

**Deux apps Streamlit coexistent** : `Scripts/07_query_rag_ui.py` (locale, rerank FlashRank) et `Scripts/Streamlit Cloud/streamlit_app.py` (déployée, rerank Cohere via `rerank.py`). **Modifier la version Cloud** pour tout ce qui touche le harness déployé.

> **Important** : les deux apps Streamlit sont des **bancs de test internes (harness de debug)**. Le produit livré au client est le **backend MCP** (`Scripts/mcp_server/`, §10), interrogé par le LLM du client via les **Project Instructions** et **skills** (§11). Le pipeline d'ingestion et le schéma DB sont le socle commun aux deux.

---

## 4. Pipeline d'ingestion (mode per-copro incrémental)

Chaque script prend `--copro <code>`, résout ses paths via `pipeline_config.py`, écrit dans `per_copro/{code}/`. Lançable en parallèle (1 process par copro). Mode legacy global (sans `--copro`) conservé.

**Driver recommandé** : `ingest.py --copro <code>` (ou `--all`) ingère une copro de bout en bout avec **CRUD documents** (création / modification / suppression détectées vs l'état DB) et régénération des agrégats **Tier-2 gatée par doc_type** :

```
Tier-1 (toujours) :  01_filtrage → 02_extraction → 03_chunking → 04_metadata → 05_embedding → 05b_synthetic_questions
Tier-2 (si delta) :  05c_entity_extraction + 00c_dedup   [si doc_type SINISTRE touché]
                     09_copro_synthese                   [si PV_AG ou SINISTRE touché]
Charge :             06b_load_db --copro (UPSERT)  →  08_airtable_sync (OBLIGATOIRE après 06b)
```

- `02` détecte les docs modifiés sur place (signature `taille:mtime_ns`) ; `03` produit des `chunk_id` content-addressed (stables cross-run) ; `05` est incrémental (n'embedde que les nouveaux `chunk_id`).
- `06b --copro` fait un **UPSERT** (DELETE WHERE code_ncg + INSERT ON CONFLICT) au lieu du TRUNCATE global ; il préserve les dossiers Airtable (`airtable_record_id IS NOT NULL`) mais retire les virtuels, donc **08 obligatoire ensuite**.
- `run_pipeline_per_copro.py --copro <code>` enchaîne seulement 01→05b (flags `--from / --only / --skip`). `ingest.py` orchestre en plus 05c/00c/09/06b/08.

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
| `copro_synthese` | Fiche pré-calculée par copro (lue par `PALIM_copro_overview`) | `code_ncg` (PK), narratif PV/dossiers, faits (JSONB), `generated_at` |

**Index** : IVFFlat sur `embedding` (cosine), GIN sur `text_search` (BM25) et `themes`, btree sur `copropriete`/`code_ncg`/`doc_type`/`annee`.

**doc_types valides** (`DOC_TYPES_VALID` dans `03_chunking.py`) : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, PLAN, ASSURANCE, ENTRETIEN, SINISTRE, COMPTABILITE, BORDEREAU_AR.

---

## 6. Retrieval (cœur de `streamlit_app.py`)

Pipeline d'une requête :
```
detect_strategy_haiku()   → Haiku classifie (inventaire/ciblé/équilibré) + extrait filtres structurels (~300ms)
search_chunks()           → pré-filtrage documents → Vector + BM25 → RRF fusion → diversité par source → rerank Cohere
                            (Cloud : rerank Cohere 3.5 eu-central-1 sur le pool RRF quand le pré-filtrage est inactif, cf. rerank.py ;
                             bypass + cap RRF quand le pré-filtrage est actif. Pool = RERANK_CANDIDATES=200)
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
- `rerank.py` — rerank Cohere 3.5 (Bedrock eu-central-1). `build_rerank_client()` (client cross-région) + `rerank_rows()` (injection en-tête `[DOC_TYPE] nom_fichier`, score hybride RRF×Cohere `alpha=0.25`, fallback ordre RRF si échec). Appelé par `search_chunks` hors pré-filtrage.

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
| Rerank Cohere (cloud) | `Scripts/Streamlit Cloud/rerank.py` |
| Dossiers sinistres | `Scripts/Streamlit Cloud/dossiers_api.py` + `08_airtable_sync.py` |
| Agrégations analytiques sécurisées | `Scripts/Streamlit Cloud/analytics.py` |
| Backend MCP produit (serveur, 12 tools, deploy) | `Scripts/mcp_server/` (§10) |
| Project Instructions + skills client | `Scripts/mcp_server/INSTRUCTIONS_NCG_PROJECT.md` + `skills/` (§11) |
| Ingestion incrémentale d'une copro (CRUD) | `Scripts/ingest.py` |
| Fiche synthèse par copro | `Scripts/09_copro_synthese.py` → table `copro_synthese` |
| Modèle / réduction de coût ingestion | `Scripts/00a_cost_preflight.py`, `Scripts/PLAN_REDUCTION_COUT_COPRO.md` |
| Plans (scale / analytique / coût / dédup) | `Scripts/PLAN_*.md` |
| Mémoire complète du pipeline (archi, décisions) | `Résultats bruts/rag-prototype-guide.md` |
| ERP assurance Assynco (Airtable) | skill `assynco-erp` + `Scripts/mcp_server/PALIM_assynco.py` |
```

---

## 10. Backend MCP (produit) — `Scripts/mcp_server/`

Serveur **FastMCP** (Python 3.12) déployé sur **AWS Lambda** en image container + **Lambda Web Adapter** (`AWS_LWA_INVOKE_MODE=response_stream`). App ASGI `app = mcp.streamable_http_app()` servie par uvicorn. `stateless_http=True` obligatoire (chaque requête = invocation Lambda séparée, sinon "Session not found"). Base image `python:3.12-slim` (PAS la base lambda/python, son ENTRYPOINT RIC casse uvicorn). Compte AWS **046004768626**, région eu-west-1, fonction `palim-mcp`, image courante **v8**.

- **Accès** : authless. Barrière = slug d'URL secret (`MCP_URL_SLUG`) plus resource policy de la Function URL. Pas d'OAuth/bearer. Protection DNS-rebinding FastMCP désactivée (rejetait le domaine `*.lambda-url.*.on.aws` en 421).
- **Secrets (AWS Secrets Manager)** : `palim/mcp_ncg_reader` (DB, user **lecture seule** `mcp_ncg_reader`), `palim/airtable_pat` (Assynco). Env = fallback dev seulement, jamais loggé.
- **Régions** : embeddings Titan eu-west-1, rerank Cohere eu-central-1, Secrets Manager + RDS eu-west-1.
- **Build/deploy** : `build_and_push.sh <tag>` vendorise `dossiers_api.py` et `rerank.py` depuis `../Streamlit Cloud/`, build linux/amd64, push ECR ; puis `aws lambda update-function-code ... --image-uri ...:vN`. Runbook : `RUNBOOK_DEPLOY_V7.md`. Rollback = repointer sur l'image précédente.
- **Tracing** : Langfuse optionnel via `PALIM_tracing.py` (no-op si pas de clés ; `langfuse==2.60.4`). `search_chunks`/`search_dossiers` renvoient un `trace_ref` pour rattacher le feedback.

### Les 12 tools `PALIM_*` (décorateurs `@mcp.tool()` dans `PALIM_server.py`)

| Tool (signature résumée) | Rôle | Notes |
|--------------------------|------|-------|
| `PALIM_search_chunks(query, copro_codes, doc_type?, year_min?, year_max?, statut?, sous_type?, retrieval_mode=equilibre, max_chunks=12, include_bordereau_ar?, include_legal_context?)` | Recherche de passages (chunks) | INVARIANT : ≥1 `copro_code` sinon `MISSING_COPRO_SCOPE`. Renvoie `trace_ref` |
| `PALIM_list_copros(query?)` | Annuaire copros (identité) | Choisir la copro sans recherche documentaire |
| `PALIM_discover_copros(query, doc_type?, year_min?, year_max?, top_k=10)` | Découverte / triage | `final_answer_allowed=false` |
| `PALIM_get_full_document(source_file, max_chars=20000, chunk_start?, chunk_end?, reason?)` | Texte intégral d'un doc | Anti-aspiration (cap 50000, refuse `%`/`*`) |
| `PALIM_get_chunks(chunk_ids, reason?)` | Re-matérialise le texte EXACT par id | Re-fetch du verbatim quand le passage a quitté le contexte (cap 20) ; sinon citer depuis `result.text` |
| `PALIM_search_dossiers(query, copro_codes?, max_results=20)` | Dossiers sinistres/travaux/contentieux | scope 0=découverte / 1=single / ≥2=multi |
| `PALIM_get_visite_3d(query)` | Liens visite 3D (jumeau numérique) | Mapping `visites_3d.txt` (LEMEAU, EXTINCTEUR) |
| `PALIM_assynco_get_copro(code_ncg)` | Fiche assurance Assynco (live) | Isolation tenant Syndic=NCG |
| `PALIM_assynco_list_polices(code_ncg, max_results=20)` | Polices d'assurance (live) | `type_contrat` MRI/RCS/PJ |
| `PALIM_assynco_search_sinistres(code_ncg, query?, max_results=20)` | Sinistres Assynco (live) | Plus riche que la table `dossiers` |
| `PALIM_copro_overview(code_ncg)` | Fiche synthèse en 1 appel | Lit `copro_synthese` + watermark `freshness.stale` |
| `PALIM_log_feedback(rating, comment?, question?, copro_codes?, mode?, utilisateur?, trace_ref?)` | Feedback Langfuse | `rating` = utile / a_ameliorer |

Tous renvoient un dict `{ok, ...}` (jamais d'exception brute). Le **scope est dérivé serveur-side** via `PALIM_scope` (jamais reçu de Claude ; `validate_search_scope` exige ≥1 copro). Le rerank Cohere réutilise `rerank_rows()` (vendorisé) = parité avec l'app.

> NB : le docstring d'en-tête de `PALIM_server.py` annonce encore "5 tools" — obsolète, 12 décorateurs `@mcp.tool()` sont présents.

---

## 11. Livrable client — Project Instructions + skills

Le client connecte son LLM (Claude Teams / Cowork) au MCP avec :

- **Project Instructions** `Scripts/mcp_server/INSTRUCTIONS_NCG_PROJECT.md` (**v1.9**, à coller dans le Projet Claude). 12 blocs (Bloc 0→11). **Cadre à 2 axes** annoncé en une ligne avant toute réponse non triviale : Axe 1 Destinataire (Interne par défaut / Externe, gate de sécurité), Axe 2 Type de tâche (Factuel par défaut / Analyse juridique / Synthèse de dossier / Rédaction de livrable). Invariant : jamais de réponse "toutes copros confondues". Bloc 10 (sourçage, v1.9) : le verbatim se cite depuis le `text` du passage renvoyé par `search_chunks` tant qu'il est en contexte ; `get_chunks` re-matérialise le texte exact par `chunk_id` quand le passage a quitté le contexte ; `citation` = métadonnées seules. Bloc 11 : `get_visite_3d` obligatoire sur match littéral de mot-clé 3D.
- **3 skills** (`Scripts/mcp_server/skills/`) :
  - `ncg-redaction-livrable` — mise en forme (note interne, courrier copropriétaires, note conseil syndical, email prestataire, export Word, logo `logo NCG.png` en en-tête des livrables externes). 4 gabarits dans `templates.md`.
  - `ncg-note-juridique` — analyse juridique (RCP/EDD, majorités art. 24/25/25-1/26 loi 1965, délais art. 42), 3 couches (doc copro primant / cadre légal à valider / interprétation signalée), active `include_legal_context=true`, ne se fait jamais passer pour un avis juridique.
  - `assynco-erp` — accès lecture assurance Assynco via les 3 tools `PALIM_assynco_*`.

> Version alignée en **v1.9** (2026-06-22) : header et Bloc 0 = v1.9. L'ancien drift v1.6/v1.8 est résolu.
