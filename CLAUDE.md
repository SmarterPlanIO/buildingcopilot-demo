# PALIM — Regles et contexte pour nouvelles sessions Claude

> Ce fichier compile toutes les regles, feedbacks et contexte du projet PALIM.
> A coller en debut de session pour que Claude ait le meme contexte.
> Derniere mise a jour : 2 avril 2026, v0.5.0

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

- **Frontend** : Streamlit Cloud (deploie depuis la branche `main` uniquement)
- **LLM** : AWS Bedrock — Sonnet 4.6 (generation), Haiku 4.5 (strategie, classification, filtrage prompts)
- **Embeddings** : Amazon Titan Embed Text V2 (1024 dims)
- **DB** : PostgreSQL sur AWS RDS (`sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com`) avec pgvector
- **Observabilite** : Langfuse Cloud (EU) — traces, feedback, prompt filtering. Pin `langfuse==2.60.4` (v3 casse `.trace()`)
- **Auth** : login gate simple avec utilisateurs pilotes dans `st.secrets[pilot_users]`
- **Version** : affichee dans la sidebar, stockee dans `Scripts/Streamlit Cloud/VERSION` (actuellement v0.5.0)
- **Python** : 3.12 (fichier `.python-version` a la racine du repo — requis pour Langfuse/Pydantic V1)

### Pipeline (scripts locaux, executer dans l'ordre)
1. `03_chunking.py` — classifier doc_type (3 passes : folder/filename/Haiku) + chunking + BORDEREAU_AR
2. `04_metadata_documents.py` — metadonnees document-level via Haiku + protection RCP (`_TRUSTED_FOLDER_TYPES`)
3. `05_embedding.py` — embeddings Titan V2 (parallelise)
4. `05b_synthetic_questions.py` — questions synthetiques Haiku (PV_AG, RCP, CONTRAT)
5. `06b_load_db.py` — TRUNCATE + INSERT dans PostgreSQL (chunks, documents, dossiers)
6. `08_airtable_sync.py` — sync dossiers sinistres depuis Airtable Assynco (**OBLIGATOIRE apres 06b**)

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

---

## 4. Pipeline re-run checklist

```bash
PYTHONIOENCODING=utf-8 python 03_chunking.py
PYTHONIOENCODING=utf-8 python 04_metadata_documents.py
rm chunks_avec_embeddings.jsonl chunks_avec_embeddings_sq.jsonl  # PURGER avant re-embed
PYTHONIOENCODING=utf-8 python 05_embedding.py
PYTHONIOENCODING=utf-8 python 05b_synthetic_questions.py
PYTHONIOENCODING=utf-8 python 06b_load_db.py
# OBLIGATOIRE apres 06b — le TRUNCATE efface les chunks virtuels Airtable :
PYTHONIOENCODING=utf-8 AIRTABLE_PAT="patFfI...cfa15" DB_HOST="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com" DB_PASSWORD="NokiumRAG99?" python 08_airtable_sync.py
```

### Gotchas
- `04_metadata_documents.py` lit `chunks_copro.jsonl` — couteux (Haiku par doc), ~10-15min
- `05_embedding.py` est incremental (append) — **supprimer l'ancien fichier output** avant re-run
- `06b_load_db.py` fait TRUNCATE → perte des chunks Airtable virtuels → 08 OBLIGATOIRE apres
- `08_airtable_sync.py` necessite `AIRTABLE_PAT`, `DB_HOST`, `DB_PASSWORD` en variables d'env
- `chunks_avec_embeddings.jsonl` est un intermediaire supprimable — seul `chunks_avec_embeddings_sq.jsonl` est necessaire

---

## 5. Bugs corriges dans cette session (v0.5.0)

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

## 7. Tache future enregistree

- **Refactoring `streamlit_app.py`** : deplacer toute la logique retrieval/business dans des modules dedies (ex: `retrieval.py`, `strategy.py`). Actuellement le fichier est trop gros avec de la logique metier melee a l'UI.
- **Web RAG** : plan concu pour interroger des sites juridiques whitelistes (Legifrance, Service-Public, ANIL). Module `web_search.py` + Google Custom Search API. Option A (scraping live) recommandee en premier.
