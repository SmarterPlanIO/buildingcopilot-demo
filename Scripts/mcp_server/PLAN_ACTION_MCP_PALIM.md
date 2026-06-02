# Plan d'action — MCP PALIM pour Claude Teams (v2, durci)

> Service retrieval multi-copropriété **contrôlé**, exposé à Claude via MCP. Pas un simple endpoint de recherche vectorielle.
> Architecture **C** : FastMCP Python sur AWS Lambda (Lambda Web Adapter), Function URL streaming, authless **+ slug secret + caps + logs** pour le pilote.
> Statut : **PLAN** — aucun code écrit. v2 intègre `INSTRUCTIONS_CLAUDE_CODE_MCP_PALIM.md` (1er juin 2026).

---

## 0. Décisions verrouillées

| Sujet | Décision |
|---|---|
| Archi | **C** — FastMCP Python sur Lambda (container ECR + Lambda Web Adapter), Function URL streaming |
| Auth Claude ↔ MCP | **Authless + slug secret `/mcp/<random>` + caps serveur + logs CloudWatch**. Cognito OAuth post-pilote |
| Préfixe | `PALIM_` sur les **fichiers modules** et les **noms de tools** |
| Tools V1 | `PALIM_search_chunks`, `PALIM_list_copros`, `PALIM_discover_copros`, `PALIM_get_full_document`, `PALIM_search_dossiers` |
| Cred DB | user **read-only** `mcp_ncg_reader` (minuscules, cf. gotcha §3.1) |
| Rerank cohere | hook `enable_rerank`, **off par défaut** en V1 |
| Scoping | **invariant serveur dur** (non-dilution). `scope_mode` non passé par Claude → **dérivé** en `inferred_scope` |
| Query planning | `PALIM_plan_query` **différé V1.1** (désambiguïsation copro dans `list_copros`/`discover_copros` en V1) |

### Principe directeur
Claude orchestre la conversation (reformule, clarifie, compare, synthétise). **Le serveur impose les invariants** : scope explicite, non-dilution inter-copro, traçabilité documentaire (`code_ncg` + `source_file` toujours présents), protection contre l'extraction massive. Aucune requête de retrieval ne part sans validation de scope.

### Le fait qui justifie l'archi
Claude.ai web n'accepte pour un connecteur custom que **OAuth 2.x ou authless** — pas de bearer statique ni header custom. → archi C, authless durci pour le pilote.

---

## 1. Architecture & arborescence

```
Claude Teams (poste NCG)
   │  MCP Streamable HTTP (authless + slug secret)
   ▼
AWS Lambda Function URL  (--invoke-mode RESPONSE_STREAM, --auth-type NONE)
   │  Lambda Web Adapter → uvicorn → FastMCP ASGI app (/mcp/<slug>)
   ▼
PALIM_server.py  (5 tools + validation de scope)
   ├─ PALIM_scope.py       → validate_scope / infer_scope / build_warning (invariant non-dilution)
   ├─ PALIM_retrieval.py   → embed_query + hybrid_search (extrait streamlit_app) + équilibrage multi-copro
   ├─ PALIM_discovery.py   → agrégat découverte documentaire (COUNT/GROUP BY)
   ├─ PALIM_dossiers.py    → search_dossiers (wrap dossiers_api.py)
   └─ PALIM_db.py / PALIM_config.py
   ▼
RDS PostgreSQL  sp-rag-ncg-copros  (user read-only mcp_ncg_reader, SSL strict)
```

```
Scripts/mcp_server/
  PALIM_config.py        # constantes, régions, caps serveur
  PALIM_db.py            # connexion psycopg2 via env vars + keepalives
  PALIM_scope.py         # validation/inférence de scope (invariant)
  PALIM_retrieval.py     # embed_query + hybrid_search + équilibrage multi-copro
  PALIM_discovery.py     # découverte documentaire (agrégat)
  PALIM_copros.py        # registre copro + fuzzy-match alias (annuaire)
  PALIM_dossiers.py      # search_dossiers (wrap dossiers_api)
  PALIM_server.py        # FastMCP : 5 tools → app ASGI
  PALIM_run_local.py     # lancement local (stdio) pour MCP Inspector
  Dockerfile
  requirements.txt
  tests/
    palim_mcp_eval_questions.json     # 20 questions de recette
    test_palim_mcp_contracts.py       # validation contrats/scope
    test_palim_retrieval_regression.py# régression vs Streamlit (mono-copro)
  NOTICE_MCP_PALIM.md
  PLAN_ACTION_MCP_PALIM.md  # ce fichier
  INSTRUCTIONS_CLAUDE_CODE_MCP_PALIM.md  # source des durcissements
```

---

## 2. Invariant de scope (cœur métier)

Le serveur ne reçoit **pas** de `scope_mode` de Claude. Il **dérive** `inferred_scope` depuis `copro_codes` et le **retourne** dans chaque réponse.

| Tool | 0 code | 1 code | ≥2 codes |
|---|---|---|---|
| `PALIM_search_chunks` (réponse finale) | **ERREUR** `MISSING_COPRO_SCOPE` → suggère `discover_copros`/`list_copros` | `single` | `multi` (résultats équilibrés par copro) |
| `PALIM_search_dossiers` | `single` autorisé seulement si la requête est nominative ; sinon warning | `single` | `multi` (équilibré) |
| `PALIM_discover_copros` | **c'est son rôle** : découverte sans copro | filtre la découverte | filtre la découverte |

`PALIM_scope.py` :
```python
infer_scope(copro_codes) -> "single" | "multi"          # 1 vs ≥2
validate_search_scope(copro_codes)                       # 0 → MISSING_COPRO_SCOPE
normalize_copro_codes(copro_codes) -> list[str]          # dédup, trim, casse
build_scope_warning(...) -> list[str]
```
**Règle dure** : `PALIM_search_chunks` ne lance aucune requête SQL sans au moins un `copro_code`. La découverte non scopée n'existe **que** via `PALIM_discover_copros`, qui retourne `final_answer_allowed=false`.

Erreurs = réponses MCP contrôlées, jamais d'exception brute, jamais de variable d'env dans le message :
```json
{ "ok": false, "error_type": "MISSING_COPRO_SCOPE",
  "message": "Recherche de réponse nécessite une ou plusieurs copropriétés. Utiliser PALIM_discover_copros pour identifier les copros, ou PALIM_list_copros pour choisir par nom.",
  "suggested_next_tool": "PALIM_discover_copros" }
```

---

## 3. Contrats des 5 tools V1

> Caveats data appliqués partout : `chunk_id` = **TEXT** ; `adresse`/`aliases` = **optionnels** (omis si absents en DB) ; `code_ncg` = **entier 4-6 chiffres en string** (ex. `"5390"`, vérifié via `08_airtable_sync.py` regex `\((\d{4,6})\)`), pas `NCG_001`.

### 3.1 `PALIM_search_chunks` — retrieval scopé (réponse finale)
```python
PALIM_search_chunks(
    query: str,
    copro_codes: list[str],                 # REQUIS, >= 1 (sinon MISSING_COPRO_SCOPE)
    doc_type: str | None = None,            # PV_AG, RCP, CONTRAT, ASSURANCE, ...
    year_min: int | None = None,
    year_max: int | None = None,
    statut: str | None = None,              # filtre document-level
    sous_type: str | None = None,           # filtre document-level
    retrieval_mode: str = "equilibre",      # cible | equilibre | inventaire
    max_chunks: int = 12,                   # cap serveur 30
    include_bordereau_ar: bool = False,
    include_legal_context: bool = False     # force quota RCP/PV/contrat
)
```
Retour structuré :
```json
{ "ok": true, "inferred_scope": "single", "copro_codes": ["5390"],
  "query_used": "...", "filters_applied": { "doc_type": null, "year_min": null, ... },
  "warnings": [],
  "results": [ { "chunk_id": "<txt>", "code_ncg": "5390", "copropriete": "...",
    "source_file": "...", "nom_fichier": "...", "doc_type": "PV_AG", "chunk_index": 7,
    "text": "...", "score": 0.82, "vec_similarity": 0.74, "bm25_score": 0.31, "source_rank": 1 } ] }
```
Toujours : `code_ncg`, `source_file`, `doc_type`, `warnings`. En `multi` : équilibrage par copro (`max_chunks // n_copros`, redistribution si une copro n'a aucun résultat).

### 3.2 `PALIM_list_copros` — annuaire (identité)
```python
PALIM_list_copros(query: str | None = None)
# query → score les copros candidates par code_ncg / nom de résidence / rue / adresse / alias
```
```json
{ "ok": true, "copros": [ {
  "code_ncg": "5390", "nom": "TIVOLI", "adresse": "2-6 BIS HENRI TARIEL",
  "aliases": ["TIVOLI", "Résidence TIVOLI", "TARIEL"],
  "nb_documents": 123, "nb_chunks": 2456,
  "doc_types_available": ["PV_AG","RCP","CONTRAT"], "annee_min": 2012, "annee_max": 2025,
  "has_rcp": true, "has_pv_ag": true, "has_dossiers": true } ] }
```
Rôle : choisir la bonne copro **sans** lancer de recherche sur les chunks. `query` fuzzy-matche code/nom/rue/adresse/alias.

**Source des alias** : le mapping nom→code existe déjà dans `08_airtable_sync.py` (`COPRO_FILTERS`) + champs Airtable `Name` (nom résidence, ex. "TIVOLI(5390)") et `Adresse Copro`. V1 : matérialiser un **registre copro** — nouvelle table `copros(code_ncg PK, nom_residence, adresse, rue, aliases TEXT[])` peuplée depuis Airtable (extension de `08_airtable_sync.py`), avec **fallback dégradé** sur `MAX(copropriete)` de `documents` si la table est absente (`adresse`/`aliases` alors omis).

**Non-résolution silencieuse (invariant)** : un alias n'est **pas 1:1** (cf. `08_airtable_sync.py` l.32-34 : TARIEL→5448/5443, CRESSON→5 codes). `query` retourne donc une **liste de candidats classés**, jamais une copro unique implicite. La sélection finale du `copro_code` reste à Claude/utilisateur avant tout `PALIM_search_chunks`.

### 3.3 `PALIM_discover_copros` — découverte documentaire (agrégat)
```python
PALIM_discover_copros(
    query: str, doc_type: str | None = None,
    year_min: int | None = None, year_max: int | None = None, top_k: int = 10
)
```
Requête **d'agrégat** (`COUNT/GROUP BY code_ncg`), **pas** le pipeline RRF.
```json
{ "ok": true, "final_answer_allowed": false,
  "candidates": [ { "code_ncg": "5390", "nom": "...", "match_count": 12,
    "doc_types": ["PV_AG","CONTRAT"], "years": [2021,2022,2023],
    "top_evidence_snippet": "..." } ],
  "warnings": ["final_answer_not_allowed_from_global_discovery"] }
```
Rôle : identifier les copros pertinentes. Réponse finale interdite depuis ce tool → Claude doit ensuite appeler `PALIM_search_chunks` scopé.

### 3.4 `PALIM_get_full_document` — drilldown plafonné
```python
PALIM_get_full_document(
    source_file: str, max_chars: int = 20000,   # cap serveur 50000
    chunk_start: int | None = None, chunk_end: int | None = None,
    reason: str | None = None
)
```
Règles : `max_chars` plafonné, tronqué par défaut, refuse les `source_file` à pattern large (wildcard, vide). Retour : `text`, `truncated`, `total_chars_available`, `chunks_returned`, `metadata{code_ncg, copropriete, doc_type, nom_fichier}`.

### 3.5 `PALIM_search_dossiers` — dossiers scopés
```python
PALIM_search_dossiers(query: str, copro_codes: list[str] | None = None, max_results: int = 20)  # cap 50
```
Même logique de scope (inferred). Champs garantis : `code_ncg, copropriete, dossier_id, type, statut, lese, montant, source`. Multi-copro équilibré ; sans copro → dossiers candidats synthétiques (pas de dump).

---

## 4. Phase 1 — Modules purs (refactor durable)

`PALIM_config.py` : `EMBEDDING_MODEL`, `EMBED_DIM=1024`, `RRF_K=60`, `SIMILARITY_THRESHOLD=0.15`, `MAX_CHUNKS_PER_SOURCE=3`, `RERANK_CANDIDATES=200`, `MIN_CHUNK_CHARS=500`, `RCP_MIN_SLOTS=3`, `AWS_REGION_EMBED="eu-west-1"`, `AWS_REGION_RERANK="eu-central-1"`, **caps** `MAX_CHUNKS_CAP=30`, `MAX_RESULTS_CAP=50`, `MAX_CHARS_CAP=50000`.

`PALIM_db.py` : `get_conn()` psycopg2 via env (`DB_HOST/PORT/NAME/USER/PASSWORD`), `sslmode=require`, keepalives (repris streamlit_app.py:330), singleton + reconnexion sur `SELECT 1`.

`PALIM_retrieval.py` (extrait **maîtrisé** de `search_chunks` streamlit_app.py:761-959, **pas** copié aveuglément) :
- `embed_query(text, bedrock)` ← `get_embedding:693`, bedrock en param.
- `hybrid_search(conn, bedrock, query, *, copro_codes, doc_type, year_min, year_max, statut, sous_type, retrieval_mode, max_chunks, include_bordereau_ar, include_legal_context, exclude_categories, enable_rerank=False)`.
  - **Conserver** : pré-filtrage table `documents` (lignes 772-831, piloté par params explicites au lieu de Haiku), vector + BM25 + RRF k=60, diversité `PARTITION BY groupe_doc`, boost doc_type, exclusion `BORDEREAU_AR`, `nb_caracteres>=MIN_CHUNK_CHARS`, dédup texte, quota RCP (gardé par `include_legal_context`).
  - **Filtre copro** = `code_ncg IN (...)` (jamais le nom libre `copropriete`).
  - **Multi-copro** : équilibrage `max_chunks // n_copros` + redistribution.
  - **`SET LOCAL ivfflat.probes = 10`** dans la transaction (préserve le rappel sous filtre `code_ncg`).
  - **Retirer** : tout `st.*`, UI, état session, `detect_strategy_haiku`, `decompose_temporal_query`.
- `enable_rerank=False` : hook no-op V1, branchement cohere eu-central-1 en Phase 6.

`PALIM_discovery.py` : `discover_copros(conn, bedrock, query, ...)` → agrégat par `code_ncg`.
`PALIM_dossiers.py` : wrap de `search_dossiers_for_query(conn, query, copropriete)` (dossiers_api.py, déjà conn-based) + équilibrage multi-copro.
`PALIM_copros.py` : `list_copros(conn, query=None)` → lit le **registre copro** (table `copros` si présente, sinon fallback `MAX(copropriete)` de `documents`) + fuzzy-match candidats. **Pré-requis data** : peupler la table `copros` depuis Airtable (extension de `08_airtable_sync.py`, source `COPRO_FILTERS` + `Name` + `Adresse Copro`). Dégradation gracieuse si absente.

**Critère de succès** : test CLI `hybrid_search(...)` retourne des chunks pertinents sans importer streamlit ; top-5 cohérent avec l'app Streamlit (mono-copro).

---

## 5. Phase 2 — Scope + serveur FastMCP local

### 5.1 Cred DB `mcp_ncg_reader` (minuscules — gotcha PostgreSQL : identifiants non quotés repliés en minuscules)
```sql
CREATE USER mcp_ncg_reader WITH PASSWORD '<motdepasse_fort>';
GRANT CONNECT ON DATABASE postgres TO mcp_ncg_reader;
GRANT USAGE ON SCHEMA public TO mcp_ncg_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_ncg_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_ncg_reader;
```
Vérif : `SELECT` OK, `INSERT` refusé.

**Stocker le mot de passe dans AWS Secrets Manager** (jamais en env, jamais transmis) :
```bash
aws secretsmanager create-secret --name palim/mcp_ncg_reader \
  --secret-string '{"password":"<motdepasse_fort>"}' --region eu-west-1
# -> noter l'ARN, à mettre dans DB_SECRET_ARN
```
Le secret peut être une chaîne brute (= password) ou un JSON `{"password": ...}` (compatible secret RDS-managed : `host`/`username`/`dbname` repris en repli si absents en env). Pour le test local, Thai exporte `DB_SECRET_ARN` + ses creds AWS — le password est résolu au runtime, Claude ne le voit pas.

### 5.2 `PALIM_scope.py` puis `PALIM_server.py`
- D'abord la validation de scope (aucun SQL retrieval avant). Puis les 5 tools, chacun avec **description riche** + JSON Schema, retour structuré `{ok, ...}`, `inferred_scope` renvoyé.
- `FastMCP("PALIM")`, `app = mcp.streamable_http_app()`, monté sur `/mcp/<slug>`. `conn`/`bedrock` en singletons.
- Caps appliqués serveur-side (clamp `max_chunks≤30`, `max_results≤50`, `max_chars≤50000`).
- Logs CloudWatch structurés par appel : `timestamp, tool, inferred_scope, copro_codes, max_chunks/max_chars, latence, n_results, warnings`.

**Critère de succès** : MCP Inspector liste les 5 tools ; mono-copro OK ; `search_chunks` sans copro → `MISSING_COPRO_SCOPE` ; `get_full_document` tronque ; refus d'extraction massive.

---

## 6. Phase 3 — Tests AVANT déploiement (porte bloquante)

`tests/palim_mcp_eval_questions.json` — **20 questions** :

| Catégorie | N | Objectif |
|---|--:|---|
| Mono-copro explicite | 5 | précision `single` |
| Requête ambiguë | 5 | déclenche `discover_copros`/`list_copros`, pas de réponse non scopée |
| Comparaison multi-copro | 3 | équilibrage par copro |
| Découverte documentaire | 3 | identifie les copros, `final_answer_allowed=false` |
| Juridique RCP/PV/Contrat | 2 | quota RCP via `include_legal_context` |
| Hors-sujet / extraction abusive | 2 | refus / warning contrôlé |

**Critères bloquants (un échec = blocage déploiement)** :
- mélange de 2 copros sans le signaler ;
- découverte utilisée comme base de réponse finale ;
- document complet extrait sans limite ;
- résultat sans `code_ncg` ;
- exception brute renvoyée ;
- **divergence forte du retrieval MCP vs Streamlit sur mono-copro équivalente** (`test_palim_retrieval_regression.py`).

Ces tests tournent en CLI **avant** tout packaging Docker.

---

## 7. Phase 4 — Packaging container + Lambda + Function URL

`requirements.txt` : `mcp[cli]`, `psycopg2-binary`, `boto3`, `uvicorn`, `starlette`.

`Dockerfile` :
```dockerfile
FROM public.ecr.aws/lambda/python:3.12
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV AWS_LWA_INVOKE_MODE=response_stream
ENV PORT=8000
COPY requirements.txt . && RUN pip install -r requirements.txt
COPY PALIM_*.py ./
CMD ["uvicorn", "PALIM_server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Déploiement (CloudShell/Docker local) :
```bash
aws ecr create-repository --repository-name palim-mcp --region eu-west-1
# build + push palim-mcp:v1
aws lambda create-function --function-name palim-mcp --package-type Image \
  --code ImageUri=<acct>.dkr.ecr.eu-west-1.amazonaws.com/palim-mcp:v1 \
  --role <ARN_role_exec> --timeout 60 --memory-size 1024 \
  --environment file://env.json --region eu-west-1
aws lambda create-function-url-config --function-name palim-mcp \
  --auth-type NONE --invoke-mode RESPONSE_STREAM --region eu-west-1
```
`env.json` : `DB_HOST/PORT/NAME`, `DB_USER=mcp_ncg_reader`, **`DB_SECRET_ARN`** (PAS de `DB_PASSWORD` en prod), `AWS_REGION_SECRETS`, `MCP_URL_SLUG=<random>`. Le mot de passe est lu depuis AWS Secrets Manager au runtime (modèle LLB `handler.py`), jamais en variable d'env ni transmis à un tiers.

IAM : `bedrock:InvokeModel` (Titan V2 eu-west-1 ; cohere eu-central-1 plus tard) + **`secretsmanager:GetSecretValue` sur l'ARN du secret DB** + `AWSLambdaBasicExecutionRole`.
Réseau : **RDS reste publiquement accessible** → pas de VPC Lambda (le plus simple). **Posture = LLB "Option B"** : on ne compte pas sur l'isolement réseau, on compense par les garde-fous d'accès (§11). Optionnel : restreindre le Security Group RDS aux IP de sortie connues si stables.

**Critère de succès** : `curl -N "<url>/mcp/<slug>" ... tools/list` → 200 + 5 tools ; cold start < 60s.

---

## 8. Phase 5 — Connexion Claude Teams + recette

1. Claude Teams → Connectors → Add custom connector → URL = `<function-url>/mcp/<slug>`, auth none (Owner requis).
2. Les 5 tools `PALIM_*` apparaissent.
3. Recette = les 20 questions §6 jouées via Claude. Critères de succès §10.

---

## 9. Phase 6 — Backlog post-pilote

| Prio | Item |
|---|---|
| P1 | **Rerank cohere** eu-central-1 → `enable_rerank=True` quand l'autre terminal merge |
| P1 | `PALIM_plan_query` (V1.1) : planning déterministe (Option A), interface compatible routeur Haiku (Option B) |
| P1 | `PALIM_run_analytical_query` : route spec→SQL (`analytics.py`), comptages multi-copro |
| P2 | **Cognito OAuth** devant Function URL ; IP allowlist si Anthropic publie ses IP de connecteurs ; alarme CloudWatch volume anormal |
| P2 | Langfuse tracing (`langfuse==2.60.4`) |
| P3 | **Scale 150 copros** : IVFFlat → HNSW + routage grossier copro |
| P3 | Retry / circuit breaker Bedrock/RDS ; rotation mot de passe DB |

---

## 10. Définition de succès

1. Claude Teams appelle PALIM via MCP.
2. Réponses mono-copro **au niveau Streamlit** (régression verte).
3. Requêtes ambiguës → **aucune** réponse finale non scopée.
4. Comparaisons multi-copro explicites et équilibrées.
5. Documents complets **non aspirables** massivement.
6. Erreurs contrôlées, sans secrets.
7. Compatible future auth Cognito.
8. Code retrieval réutilisable par Streamlit **et** MCP.

---

## 11. Sécurité pilote (authless durci) — obligatoire

**RDS publique = posture LLB "Option B"** : pas d'isolement réseau, compensé par les garde-fous d'accès ci-dessous (mot de passe fort + SSL strict + user read-only, comme `n8n_reader` côté LLB).

1. User DB read-only `mcp_ncg_reader` (jamais `ragadmin`). 2. SSL RDS strict (`sslmode=require`). 3. **Mot de passe DB dans AWS Secrets Manager** (`DB_SECRET_ARN`), jamais en env ni transmis ; IAM `secretsmanager:GetSecretValue` scopé à l'ARN. 4. Caps serveur (`max_chunks≤30`, `max_results≤50`, `max_chars≤50000`). 5. Slug secret `/mcp/<random>` (**obligatoire**, pas optionnel). 6. Logs CloudWatch structurés par appel. 7. Refus d'extraction massive (tous docs / toute la copro / toute la base). 8. Jamais d'env var dans les messages d'erreur. 9. Rotation mot de passe DB après pilote. 10. Optionnel : Security Group RDS restreint aux IP de sortie connues.

---

## 12. Phrase de cadrage (README)
PALIM MCP n'est pas un simple endpoint de recherche vectorielle. C'est un service retrieval multi-copropriété contrôlé, conçu pour permettre à Claude d'orchestrer des réponses fiables sans perdre les invariants métier des archives de copropriété : scope explicite, traçabilité documentaire, non-dilution inter-copro, protection contre l'extraction massive.

---

## 13. Checklist d'exécution

**P0 — Contrats & scope (avant tout SQL)**
- [ ] `PALIM_scope.py` : `validate_search_scope`, `infer_scope`, `normalize_copro_codes`, `build_scope_warning`
- [ ] Contrats des 5 tools figés (retours structurés, `inferred_scope`, warnings)
- [ ] Caveats data appliqués (`chunk_id` TEXT, adresse/aliases optionnels, code_ncg non figé)

**P0 — Modules purs**
- [ ] `PALIM_config.py`, `PALIM_db.py`, `PALIM_retrieval.py`, `PALIM_discovery.py`, `PALIM_dossiers.py`, `PALIM_copros.py`
- [ ] Zéro dépendance `streamlit` ; RRF/BM25/vector/diversité/exclusion AR/quota RCP conservés ; équilibrage multi-copro ; `ivfflat.probes`
- [ ] Registre copro : table `copros` peuplée depuis Airtable (extension `08_airtable_sync.py`) OU fallback `documents` ; alias multi-candidats (non 1:1)

**P1 — Serveur & tools**
- [ ] `PALIM_server.py` : 5 tools, descriptions riches, JSON Schema, caps clampés, logs structurés
- [ ] `PALIM_run_local.py`

**P1 — Tests (porte bloquante)**
- [ ] `palim_mcp_eval_questions.json` (20), `test_palim_mcp_contracts.py`, `test_palim_retrieval_regression.py`
- [ ] CLI retrieval, MCP Inspector, `tools/list`, mono-copro, ambiguë, refus extraction massive — tous verts

**P1 — Déploiement**
- [ ] `mcp_ncg_reader` créé/testé ; vérif RDS publique+SSL ; image ECR ; Lambda + Function URL stream authless + slug ; env vars ; Bedrock OK ; connecteur Claude Teams

**P2 — Après pilote** : Cognito · Langfuse · rerank cohere · tool analytique · HNSW · circuit breaker · rotation MDP
