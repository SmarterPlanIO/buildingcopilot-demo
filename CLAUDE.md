# PALIM — Regles et contexte pour nouvelles sessions Claude

> Ce fichier compile toutes les regles, feedbacks et contexte du projet PALIM.
> A coller en debut de session pour que Claude ait le meme contexte.
> Derniere mise a jour : 22 juin 2026. Harness Streamlit v0.7.1, backend MCP image v8, Project Instructions client v1.9.
>
> **Bascule produit (mai-juin 2026)** : le produit livre au client est desormais un **backend de tools MCP** que le LLM du client (Claude Teams / Cowork) interroge, plus l'app Streamlit (devenue harness de debug interne). Carte du code a jour dans `AGENTS.md` (sections 10-11 = backend MCP + livrable client) ; memoire complete dans `Resultats bruts/rag-prototype-guide.md` (sections 12+).

---

## 1. Profil utilisateur

- **Thai** — CTO / co-fondateur de SmarterPlan
- Construit PALIM, un assistant IA pour la gestion de copropriete (syndic)
- Client : **NCG** (syndic professionnel), pilote avec 3-4 beta users (Quentin, Johan, Christophe)
- Prefere des solutions propres et production-ready, pas des quick hacks
- Prefere la classification Haiku/LLM plutot que des regex fragiles quand les deux sont viables
- Exige la rigueur juridique : les PV d'AG sont des documents legaux, pas a paraphraser
- Itere vite : attend que le code soit push, merge dans main, et deploye dans la meme session
- Pose des questions diagnostiques precises — attend une analyse root cause, pas du guesswork

---

## 2. Architecture du projet

### Produit : backend MCP interroge par le LLM du client
- **Modele de livraison** : le client (NCG) utilise son propre LLM (Claude Teams / Cowork) avec des **Project Instructions** fournies (`Scripts/mcp_server/INSTRUCTIONS_NCG_PROJECT.md`, v1.9) et 3 **skills** (`ncg-redaction-livrable`, `ncg-note-juridique`, `assynco-erp`). Ce LLM se connecte au **serveur MCP PALIM** qui expose le RAG documentaire et l'ERP assurance en tools.
- **Serveur MCP** : FastMCP (Python 3.12) sur **AWS Lambda** (image container + Lambda Web Adapter, `response_stream`), code dans `Scripts/mcp_server/`. **12 tools** `PALIM_*` (retrieval, dossiers, synthese copro, visite 3D, Assynco, feedback). Authless : barriere = slug d'URL secret (`MCP_URL_SLUG`) plus resource policy de la Function URL. `stateless_http=True` obligatoire en Lambda. Compte AWS 046004768626, fonction `palim-mcp`, image v8.
- **Secrets** : AWS Secrets Manager (`palim/mcp_ncg_reader` pour la DB, `palim/airtable_pat` pour Assynco). DB en **lecture seule** via user `mcp_ncg_reader` cote MCP.
- **Streamlit** : devenu **harness de debug interne** (toujours deploye depuis `main`, version dans `Scripts/Streamlit Cloud/VERSION` = 0.7.1). N'est plus le produit livre au client. La regle 3.1 (UI seulement) reste valide.

### Socle commun (partage MCP + harness Streamlit)
- **LLM** : AWS Bedrock — Sonnet 4.6 (generation ; cote client en MCP), Haiku 4.5 (strategie, classification, filtrage, ingestion)
- **Embeddings** : Amazon Titan Embed Text V2 (1024 dims), region eu-west-1
- **Rerank** : Cohere rerank-v3.5 sur Bedrock **eu-central-1** (Francfort ; pas dispo eu-west-1). Module `rerank.py`, vendorise cote MCP. Parite app <-> MCP.
- **DB** : PostgreSQL sur AWS RDS (`sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com`) avec pgvector
- **Observabilite** : Langfuse Cloud (EU) — traces, feedback, span rerank. Pin `langfuse==2.60.4` (v3 casse `.trace()`)
- **Python** : 3.12 (fichier `.python-version` a la racine du repo — requis pour Langfuse/Pydantic V1)

### Pipeline d'ingestion — mode per-copro incremental (recommande)
Chaque etape prend `--copro <code>` et ecrit dans `Resultats bruts/per_copro/<code>/`. Le driver `ingest.py --copro <code>` (ou `--all`) ingere une copro de bout en bout avec **CRUD documents** (creation / modification / suppression detectees vs l'etat DB) et regeneration des agregats Tier-2 **gatee par doc_type**.
1. `01_filtrage.py` — tri plans/photos/inutiles (regles + Vision Sonnet)
2. `02_extraction_optimized.py` — extraction texte Textract ; checkpoint par signature `taille:mtime_ns` (detecte les docs modifies sur place)
3. `03_chunking.py` — classif doc_type (3 passes) + chunking + BORDEREAU_AR ; `chunk_id` content-addressed (stable cross-run) + caches deterministes (`doc_type_cache.json`, `resolution_format_cache.json`)
4. `04_metadata_documents.py` — metadonnees document-level via Haiku + protection RCP (`_TRUSTED_FOLDER_TYPES`)
5. `05_embedding.py` — embeddings Titan V2 (parallelise, incremental : append, skip `chunk_id` deja embeddes)
6. `05b_synthetic_questions.py` — questions synthetiques Haiku (PV_AG, RCP, CONTRAT)
7. `05c_entity_extraction.py` — entites sinistre -> `dossiers.jsonl` (streaming, cache content-addressed) [Tier-2, si doc_type SINISTRE touche]
8. `00c_dedup_dossiers_rag.py` — dedup des dossiers sinistres (union-find par copro, garde-fous date+nom) [Tier-2]
9. `09_copro_synthese.py` — fiche synthese pre-calculee par copro (table `copro_synthese`, lue par `PALIM_copro_overview`) [Tier-2, si PV_AG ou SINISTRE touche]
10. `06b_load_db.py --copro` — **UPSERT** per-copro (DELETE WHERE code_ncg + INSERT ON CONFLICT), remplace le TRUNCATE global
11. `08_airtable_sync.py` — sync dossiers sinistres depuis Airtable Assynco (**OBLIGATOIRE apres 06b** : le DELETE per-copro retire les chunks/dossiers virtuels Airtable)

Mode legacy global (sans `--copro`) conserve pour retro-compat ; `06b` sans `--copro` fait TRUNCATE global. `run_pipeline_per_copro.py --copro <code>` enchaine seulement 01->05b (flags `--from / --only / --skip`).

### Decisions de design cles
- **Mode juriste** : conditionnel — active quand les sources contiennent des doc_types juridiques
- **Chunking PV_AG** : par resolution (6 regex patterns + fallback Haiku)
- **ODJ/convocations** : classifies COURRIER, pas PV_AG
- **BORDEREAU_AR** : doc_type dedie, exclus du retrieval SQL par defaut sauf `include_bordereau_ar=True`
- **Protection RCP** : `_TRUSTED_FOLDER_TYPES = {"RCP"}` dans 04 — empeche Haiku de reclasser en MUTATION
- **Double retrieval contextuel** : guarde par `_dossier_filter_on` + seuil vectoriel `_CTX_VEC_MIN=0.25`
- **Sync sidebar** : selectionner un dossier coche le filtre ; decocher le filtre deselectionne le dossier

---

## 3. REGLES STRICTES

### 3.1 streamlit_app.py = UI SEULEMENT

`streamlit_app.py` ne doit contenir **AUCUNE logique metier**. Toute logique de retrieval, filtrage, ranking, enrichissement va dans des scripts dedies (`dossiers_api.py`, ou un nouveau module `retrieval.py`). L'app Streamlit ne fait qu'appeler des fonctions importees et afficher les resultats.

### 3.2 Git sur Google Drive — pattern obligatoire

Le repo est sur Google Drive. GoogleDriveFS cree des locks `.git/index.lock` en permanence.

**Pattern obligatoire pour CHAQUE commande git :**
```bash
taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f "G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Deploiement IA interne/.git/index.lock"; cd "G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Deploiement IA interne" && git <commande>
```

**Procedure commit + merge :**
```bash
# 1. Stage + commit
taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f ".git/index.lock"; git add <files> && git commit -m "message"

# 2. Checkout main + merge + push
taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f ".git/index.lock"; git checkout main && git merge PALIM_gestion_projet && git push origin main

# 3. Retour sur la branche feature
taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f ".git/index.lock"; git checkout PALIM_gestion_projet
```

**Pas besoin de pauser Google Drive** — le pattern `taskkill + sleep + rm -f index.lock` suffit a contourner les locks GoogleDriveFS.
**Ne JAMAIS tuer GoogleDriveFS.exe** (rend le disque G:\ inaccessible).

### 3.3 Toujours merger dans main

Streamlit Cloud deploie **uniquement depuis `main`**. Apres chaque commit sur `PALIM_gestion_projet`, toujours merger et pusher vers main.

### 3.4 Paths accentues — attention encodage

Les chemins locaux contiennent des caracteres accentues (ex: `Déploiement`, `Résultats`, `Données`). Regles :
- **Toujours utiliser le path exact avec accents** dans les commandes bash, `cd`, `Read`, `Edit`, etc. Ne jamais remplacer les accents par des versions ASCII (pas `Deploiement` mais `Déploiement`).
- **Tester les paths en copiant depuis un `ls` ou `git status`** plutot que de les taper manuellement — un accent manque = commande qui echoue silencieusement.
- **En Python** : utiliser `pathlib.Path` plutot que des strings brutes pour manipuler les chemins. Si `os.path` est utilise, s'assurer que l'encodage filesystem est UTF-8.
- **En bash** : toujours entourer les paths de guillemets doubles (`"chemin/accentué"`).

### 3.5 PYTHONIOENCODING=utf-8 sur Windows

Toujours prefixer les commandes Python avec `PYTHONIOENCODING=utf-8` sur cette machine Windows. Les scripts utilisent des emojis dans les `print()` et la console Windows (cp1252) crashe sinon.

### 3.6 st.secrets — acces par cle, jamais .get()

Ne JAMAIS utiliser `st.secrets.get("KEY")` ou `st.secrets["section"].get("KEY")`. L'objet AttrDict de Streamlit ne se comporte pas comme un dict standard.

```python
# OK
try:
    val = st.secrets["section"]["KEY"]
except (KeyError, TypeError):
    val = "default"

# INTERDIT
val = st.secrets.get("KEY")
val = st.secrets["section"].get("KEY")
```

### 3.7 Secrets MCP — Secrets Manager, jamais en clair

Le backend MCP lit ses secrets via **AWS Secrets Manager**, jamais en variable d'env en prod (env = fallback dev) :
- DB : `palim/mcp_ncg_reader` (user PostgreSQL **lecture seule**). Le MCP n'ecrit jamais en base.
- Airtable Assynco : `palim/airtable_pat`.

Le pipeline d'ingestion (qui ecrit en base) utilise le user `ragadmin` via `DB_PASSWORD` en variable d'env (jamais commite). Ne jamais hardcoder un mot de passe RDS ni un PAT dans un script ; passer par l'env ou Secrets Manager. Le mot de passe `ragadmin` mort a deja ete sweepe du repo (commits 6fe50a6/97a2822) et peut etre rote via `Scripts/rotate_ragadmin.py`.

### 3.8 Isolation tenant Assynco — filtre Syndic = NCG obligatoire

La base Airtable Assynco est celle du courtier (multi-syndic). Tout acces a une copro DOIT filtrer `Syndic ∈ {NCG IMMOBILIER, NCG GE, IMMOEXPRESS}` (`ASSYNCO_SYNDIC_NCG` dans `Scripts/mcp_server/PALIM_config.py`, fail-safe non vide). Un code d'un autre syndic doit renvoyer "introuvable", jamais les donnees. Ne jamais retirer ce filtre (fuite RGPD de ~157 copros tierces).

---

## 4. Pipeline re-run checklist

```bash
PYTHONIOENCODING=utf-8 python 03_chunking.py
PYTHONIOENCODING=utf-8 python 04_metadata_documents.py
rm chunks_avec_embeddings.jsonl chunks_avec_embeddings_sq.jsonl  # PURGER avant re-embed
PYTHONIOENCODING=utf-8 python 05_embedding.py
PYTHONIOENCODING=utf-8 python 05b_synthetic_questions.py
DB_PASSWORD="..." PYTHONIOENCODING=utf-8 python 06b_load_db.py
# OBLIGATOIRE apres 06b — le TRUNCATE efface les chunks virtuels Airtable :
PYTHONIOENCODING=utf-8 AIRTABLE_PAT="..." DB_HOST="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com" DB_PASSWORD="..." python 08_airtable_sync.py
```

### Gotchas
- `04_metadata_documents.py` lit `chunks_copro.jsonl` — couteux (Haiku par doc), ~10-15min
- `05_embedding.py` est incremental (append) — **supprimer l'ancien fichier output** avant re-run
- `06b_load_db.py` fait TRUNCATE → perte des chunks Airtable virtuels → 08 OBLIGATOIRE apres
- `08_airtable_sync.py` necessite `AIRTABLE_PAT`, `DB_HOST`, `DB_PASSWORD` en variables d'env
- `chunks_avec_embeddings.jsonl` est un intermediaire supprimable — seul `chunks_avec_embeddings_sq.jsonl` est necessaire

---

## 5. Bugs corriges (session v0.5.0 — historique)

> Snapshot historique de la session v0.5.0 (avril 2026), conserve pour reference. Les evolutions posterieures (rerank Cohere, backend MCP, scale per-copro, route analytique, securite) sont documentees dans `Resultats bruts/rag-prototype-guide.md` (sections 12+) et `AGENTS.md` (sections 10-11).


| Bug | Root cause | Fix |
|-----|-----------|-----|
| Dossier filter LEMEAU/BRESSON switch | `dossier_filter_active` restait False | Set True on selection |
| Chunk MARROUNI dans resultats BRESSON | Double retrieval sans garde ni seuil | Guard `_dossier_filter_on` + `_CTX_VEC_MIN=0.25` |
| Bordereau AR polluent le ranking | Chunkes comme COURRIER, pas de filtre | Nouveau doc_type BORDEREAU_AR, exclusion SQL conditionnelle |
| Langfuse inactif sur Streamlit Cloud | Python 3.14 casse Pydantic V1 | `.python-version=3.12` a la racine |
| langfuse v3 casse `.trace()` | `langfuse>=2.0.0` resolvait en v3+ | Pin `langfuse==2.60.4` |
| RCP manquants du retrieval | Haiku reclassait actes notaries en MUTATION | Protection `_TRUSTED_FOLDER_TYPES` dans 04 |
| 14 dossiers RAG sans code_ncg | `extract_code_ncg` ne matchait pas backslash Windows | Fix regex + fallback sur `documents_lies` |
| Regex backslash `[\\/]` | Raw string `[\\/]` ne matche pas `\` | `[\\\\\/]` dans le regex |
| Feedback pouces irreversibles | `disabled=existing is not None` | Boutons toujours actifs, label marque |
| Spinner infini apres inactivite | Connexion TCP Bedrock/PostgreSQL expiree | TTL 30min sur Bedrock, TCP keepalive sur PostgreSQL |
| ModuleNotFoundError python-docx | Dependance manquante | Ajoute a requirements.txt |
| ModuleNotFoundError requests | Dependance transitive non garantie | Ajoute a requirements.txt |

---

## 6. Fichiers cles modifies

| Fichier | Modifications v0.5.0 |
|---------|---------------------|
| `Scripts/03_chunking.py` | BORDEREAU_AR doc_type, regex passe 2, `chunk_whole_document` |
| `Scripts/04_metadata_documents.py` | Protection RCP (`_TRUSTED_FOLDER_TYPES`), prompt renforce |
| `Scripts/06b_load_db.py` | BORDEREAU_AR dans VALID_DOC_TYPES, fix `extract_code_ncg` (regex + fallback documents_lies) |
| `Scripts/Streamlit Cloud/streamlit_app.py` | Langfuse enrichi (tokens, cout, tags, metadata), sync sidebar, double retrieval securise, feedback reversible, keepalive TCP |
| `Scripts/Streamlit Cloud/VERSION` | 0.4.0 → 0.5.0 |
| `.python-version` (racine) | 3.12 |
| `requirements.txt` | `langfuse==2.60.4`, `python-docx`, `requests` |

---

## 7. Taches et priorites en cours

- **PRIORITE — Analytique inter-copro en MCP** : exposer `PALIM_run_analytical_query` (wrapper de `analytics.py` cote MCP, module cible `mcp_server/PALIM_analytics.py`, SQL pur read-only, zero Bedrock) plus facettes UX d'affinage (couverture honnete, jamais "choisis dans 150"). Plan : `Scripts/PLAN_ANALYTIQUE_INTER_COPRO.md`. RIEN CODE (plan seul).
- **Scale ingestion 150 copros** : finir le CRUD per-copro (gate chunk-level dans `ingest.py` pour propager les pures modifications ; cleanup disque des shards ; 08 per-copro ; registre `copros` peuple par 08). Plan : `Scripts/PLAN_SCALE_150_COPROS.md`.
- **Reduction cout ingestion** : leviers L1 dedup SHA-256 / L2 prompt caching sur 04 / L3 OCR page-level / L5 Bedrock Batch. Plan : `Scripts/PLAN_REDUCTION_COUT_COPRO.md`. Mesure faite (BERCY ~61 $ HT), rien code.
- **Refactoring `streamlit_app.py`** : deplacer toute la logique retrieval/business restante dans des modules dedies (`dossiers_api.py`, `analytics.py`, `rerank.py` deja extraits). Le fichier garde encore `search_chunks` et le ranking dans l'UI.
- **Web RAG** : plan concu pour interroger des sites juridiques whitelistes (Legifrance, Service-Public, ANIL). Module `web_search.py` + Google Custom Search API. Option A (scraping live) recommandee en premier.

---

## 8. Fichier guide a maintenir

**Apres chaque session de travail significative**, proposer a Thai de mettre a jour le fichier guide :
`Résultats bruts/rag-prototype-guide.md`

Ce fichier sert de **memoire complete du pipeline** PALIM : architecture, decisions, bugs corriges, parametres, scripts. Il documente tout ce qui a ete construit et pourquoi. Sections cles a mettre a jour :
- Version et date (en-tete)
- Fonctionnalites du pipeline de retrieval (etape 7)
- Parametres des modes (inventaire/equilibre/cible)
- Problemes resolus / residuels
- Section 9 (architecture multi-mode)
