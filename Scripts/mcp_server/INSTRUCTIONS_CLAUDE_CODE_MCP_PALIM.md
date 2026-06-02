# Instructions Claude Code — Durcissement du plan MCP PALIM multi-copropriétés

**Projet :** PALIM — Building Copilot — RAG multi-copropriétés  
**Objectif :** corriger et préciser le plan `PLAN_ACTION_MCP_PALIM.md` avant implémentation dans Claude Code  
**Date :** 1er juin 2026  
**Statut :** Instructions de développement à appliquer avant codage du serveur MCP  
**Portée :** refactoring retrieval PALIM, serveur MCP FastMCP, scoping multi-copropriété, sécurité pilote, recette qualité

---

## 1. Résumé exécutif

Le plan MCP PALIM existant est validé comme base technique, mais il doit être renforcé avant développement.

L'architecture FastMCP Python sur AWS Lambda, avec Lambda Web Adapter et Function URL streaming, peut être conservée. Les quatre tools V1 prévus sont également pertinents :

- `PALIM_search_chunks`
- `PALIM_list_copros`
- `PALIM_get_full_document`
- `PALIM_search_dossiers`

Cependant, le plan actuel est trop centré sur le transport MCP et pas assez sur la qualité métier du passage mono-copropriété vers multi-copropriétés.

Le risque principal n'est pas Lambda, FastMCP ou Claude Teams. Le risque principal est la dilution inter-copropriété : une requête ambiguë peut retourner des éléments provenant de plusieurs immeubles, puis produire une réponse fausse ou juridiquement fragile.

Claude peut orchestrer la conversation, mais le serveur PALIM doit imposer des invariants de scoping, de sécurité et de qualité retrieval.

---

## 2. Principe directeur

Ne pas construire simplement un connecteur MCP vers une base RAG.

Construire un service retrieval multi-copropriété contrôlé, exposé à Claude via MCP.

Cela signifie :

1. Claude peut décider quand appeler les tools.
2. Claude peut reformuler, clarifier, comparer, synthétiser.
3. Le serveur PALIM doit empêcher les usages dangereux :
   - recherche globale non maîtrisée ;
   - mélange silencieux de copropriétés ;
   - extraction massive de documents ;
   - réponse finale basée sur une copro mal identifiée ;
   - perte des règles métier existantes du pipeline Streamlit.

---

## 3. Décisions à conserver du plan initial

Conserver les décisions suivantes du plan `PLAN_ACTION_MCP_PALIM.md`.

| Sujet | Décision conservée |
|---|---|
| Architecture | FastMCP Python sur AWS Lambda container |
| Exposition | Lambda Web Adapter + Function URL en streaming |
| Transport | MCP Streamable HTTP |
| Déploiement pilote | Claude Teams custom connector |
| Refactoring | Extraction de la logique retrieval hors `streamlit_app.py` |
| User DB | `mcp_ncg_reader`, read-only, minuscules |
| Préfixe | `PALIM_` pour les modules et tools |
| Rerank Cohere | Hook prévu mais désactivé en V1 |
| Langfuse | Backlog post-V1, sauf logs minimum CloudWatch |

---

## 4. Correction majeure : le scoping ne doit pas être uniquement côté Claude

Le plan initial indique que la stratégie et le scoping sont gérés côté Claude, pas côté serveur.

Cette décision doit être modifiée.

### Nouvelle règle

Claude garde l'orchestration conversationnelle, mais le serveur PALIM impose les règles de scoping.

Le serveur doit distinguer explicitement trois modes :

```text
single_copro
multi_copro_compare
global_discovery
```

### Définition des modes

#### `single_copro`

Utilisé quand la question porte sur une seule copropriété.

Règles :

- `copro_codes` doit contenir exactement une copropriété.
- Le serveur filtre strictement sur cette copropriété.
- La réponse du tool doit indiquer clairement le code copropriété utilisé.
- Si aucune copropriété n'est fournie, retourner une erreur contrôlée ou un warning structuré.

#### `multi_copro_compare`

Utilisé quand la question demande explicitement une comparaison entre plusieurs copropriétés.

Règles :

- `copro_codes` doit contenir au moins deux copropriétés.
- Le serveur doit retourner les résultats groupés par copropriété.
- Le serveur doit éviter qu'une copropriété à fort volume documentaire écrase les autres.
- Le serveur doit appliquer un quota minimum par copropriété si des résultats existent.

#### `global_discovery`

Utilisé uniquement pour identifier les copropriétés potentiellement concernées.

Règles :

- Ce mode ne doit pas servir à produire une réponse finale de fond.
- Le retour doit être orienté découverte :
  - copros candidates ;
  - nombre de documents ou chunks pertinents ;
  - types de documents disponibles ;
  - score indicatif.
- Le tool doit inclure un warning explicite :
  - `final_answer_not_allowed_from_global_discovery: true`

---

## 5. Nouveau contrat recommandé pour `PALIM_search_chunks`

Remplacer le contrat trop simple :

```text
query, copro?, doc_type?, max_chunks?, include_bordereau_ar?
```

par le contrat suivant :

```python
PALIM_search_chunks(
    query: str,
    scope_mode: Literal["single_copro", "multi_copro_compare", "global_discovery"],
    copro_codes: list[str] | None = None,
    doc_type: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    statut: str | None = None,
    sous_type: str | None = None,
    retrieval_mode: Literal["cible", "equilibre", "inventaire"] = "equilibre",
    max_chunks: int = 12,
    include_bordereau_ar: bool = False,
    include_legal_context: bool = False
)
```

### Paramètres

| Paramètre | Rôle |
|---|---|
| `query` | Question ou requête utilisateur reformulée |
| `scope_mode` | Mode de scoping obligatoire |
| `copro_codes` | Liste explicite de codes copropriété |
| `doc_type` | Filtre optionnel : `PV_AG`, `RCP`, `CONTRAT`, `ASSURANCE`, etc. |
| `year_min` | Année minimale, si requête temporelle |
| `year_max` | Année maximale, si requête temporelle |
| `statut` | Filtre document-level si disponible |
| `sous_type` | Filtre document-level si disponible |
| `retrieval_mode` | Stratégie de recherche |
| `max_chunks` | Nombre maximum de chunks retournés |
| `include_bordereau_ar` | Inclure ou non les bordereaux AR |
| `include_legal_context` | Forcer quota RCP/PV/contrat selon cas juridique |

### Règles de validation serveur

Implémenter une validation stricte avant toute requête SQL.

```python
if scope_mode == "single_copro":
    assert copro_codes and len(copro_codes) == 1

if scope_mode == "multi_copro_compare":
    assert copro_codes and len(copro_codes) >= 2

if scope_mode == "global_discovery":
    # OK sans copro_codes, mais retour limité et non utilisable comme réponse finale
    pass
```

En cas d'erreur, retourner une réponse MCP contrôlée, pas une exception brute.

Exemple :

```json
{
  "ok": false,
  "error_type": "MISSING_COPRO_SCOPE",
  "message": "La requête nécessite une copropriété explicite. Appeler PALIM_list_copros ou demander une clarification utilisateur.",
  "suggested_next_tool": "PALIM_list_copros"
}
```

---

## 6. Format de retour normalisé pour `PALIM_search_chunks`

Le tool doit toujours retourner un objet structuré, pas seulement une liste brute.

```json
{
  "ok": true,
  "scope_mode": "single_copro",
  "copro_codes": ["NCG_001"],
  "query_used": "ravalement façade",
  "filters_applied": {
    "doc_type": null,
    "year_min": null,
    "year_max": null,
    "include_bordereau_ar": false,
    "include_legal_context": false
  },
  "warnings": [],
  "results": [
    {
      "chunk_id": 12345,
      "code_ncg": "NCG_001",
      "copropriete": "Résidence Exemple",
      "source_file": "NCG_001/PV_AG/2023_AG.pdf",
      "nom_fichier": "2023_AG.pdf",
      "doc_type": "PV_AG",
      "chunk_index": 7,
      "text": "...",
      "score": 0.82,
      "vec_similarity": 0.74,
      "bm25_score": 0.31,
      "source_rank": 1
    }
  ]
}
```

### Règles supplémentaires

- Toujours inclure `code_ncg`.
- Toujours inclure `source_file`.
- Toujours inclure `doc_type`.
- Toujours inclure les warnings.
- En `multi_copro_compare`, grouper ou équilibrer les résultats par copropriété.
- En `global_discovery`, ne pas retourner de longs chunks, mais des extraits courts et des agrégats.

---

## 7. Ajouter un tool de planning : `PALIM_plan_query`

Le plan initial retire `detect_strategy_haiku` et `decompose_temporal_query` du serveur.

Ne pas supprimer entièrement cette intelligence.

Créer un tool léger :

```python
PALIM_plan_query(
    query: str,
    conversation_context: str | None = None,
    candidate_copros: list[str] | None = None
)
```

### Objectif

Ce tool analyse la requête avant retrieval et aide Claude à choisir les bons appels tools.

Il ne répond pas à la question finale.

Il retourne :

```json
{
  "ok": true,
  "needs_copro_clarification": true,
  "candidate_copros": [
    {
      "code_ncg": "NCG_001",
      "nom": "Résidence Exemple",
      "reason": "Mention d'adresse ou alias détecté"
    }
  ],
  "recommended_scope_mode": "single_copro",
  "recommended_retrieval_mode": "equilibre",
  "recommended_doc_type": null,
  "year_min": 2020,
  "year_max": 2024,
  "include_bordereau_ar": false,
  "include_legal_context": true,
  "should_ask_user_clarification": true,
  "clarification_question": "De quelle copropriété parlez-vous ?"
}
```

### Implémentation V1 possible

Deux options acceptables :

#### Option A — règles déterministes V1

Commencer sans Haiku :

- détection année avec regex ;
- détection doc_type via mots-clés ;
- détection juridique via mots-clés ;
- détection comparaison via mots-clés ;
- détection copro via alias table `dossiers` ou table `copros`.

#### Option B — Haiku conservé

Réutiliser une version simplifiée du routeur Haiku existant.

Sortie impérativement JSON, validée côté Python.

### Recommandation

Implémenter Option A immédiatement pour limiter la complexité, mais garder l'interface compatible avec une Option B Haiku.

---

## 8. Enrichir `PALIM_list_copros`

Le contrat initial est trop pauvre.

Remplacer :

```json
[
  {"code_ncg": "NCG_001", "nom": "Résidence Exemple", "nb_documents": 123}
]
```

par :

```json
{
  "ok": true,
  "copros": [
    {
      "code_ncg": "NCG_001",
      "nom": "Résidence Exemple",
      "adresse": "12 rue Exemple, Paris",
      "aliases": ["Résidence Exemple", "12 rue Exemple"],
      "nb_documents": 123,
      "nb_chunks": 2456,
      "doc_types_available": ["PV_AG", "RCP", "CONTRAT", "ASSURANCE"],
      "annee_min": 2012,
      "annee_max": 2025,
      "has_rcp": true,
      "has_pv_ag": true,
      "has_dossiers": true
    }
  ]
}
```

### Objectif

Permettre à Claude de choisir la bonne copropriété sans lancer une recherche globale sur les chunks.

### Recherche optionnelle

Prévoir un paramètre facultatif :

```python
PALIM_list_copros(query: str | None = None)
```

Si `query` est fourni, filtrer ou scorer les copropriétés candidates par nom, adresse, alias ou code.

---

## 9. Sécuriser `PALIM_get_full_document`

Le tool `PALIM_get_full_document` est utile, mais il est risqué.

Il peut permettre l'extraction massive de documents si non contrôlé.

### Nouveau contrat

```python
PALIM_get_full_document(
    source_file: str,
    max_chars: int = 20000,
    chunk_start: int | None = None,
    chunk_end: int | None = None,
    reason: str | None = None
)
```

### Règles

- `max_chars` plafonné serveur à une valeur raisonnable, par exemple 50 000 caractères.
- Retour tronqué par défaut.
- Ajouter `truncated: true/false`.
- Ajouter `total_chars_available`.
- Ajouter `chunks_returned`.
- Ne jamais retourner toute la base ou tous les documents d'une copropriété.
- Refuser les patterns trop larges dans `source_file`.

### Format de retour

```json
{
  "ok": true,
  "source_file": "NCG_001/PV_AG/2023_AG.pdf",
  "metadata": {
    "code_ncg": "NCG_001",
    "copropriete": "Résidence Exemple",
    "doc_type": "PV_AG",
    "nom_fichier": "2023_AG.pdf"
  },
  "text": "...",
  "truncated": true,
  "max_chars": 20000,
  "total_chars_available": 87321,
  "chunks_returned": [0, 1, 2, 3]
}
```

---

## 10. Adapter `PALIM_search_dossiers`

Garder le principe du tool, mais accepter plusieurs copros.

```python
PALIM_search_dossiers(
    query: str,
    scope_mode: Literal["single_copro", "multi_copro_compare", "global_discovery"],
    copro_codes: list[str] | None = None,
    max_results: int = 20
)
```

### Règles

- Même logique de validation que `PALIM_search_chunks`.
- En global discovery, retourner des dossiers candidats synthétiques, pas un dump complet.
- En comparaison multi-copro, équilibrer les résultats par copropriété.
- Inclure systématiquement :
  - `code_ncg`
  - `copropriete`
  - `dossier_id`
  - `type`
  - `statut`
  - `lese`
  - `montant`
  - `source`

---

## 11. Garde-fous SQL et retrieval

### Filtrage copro

Utiliser `code_ncg` comme clé de filtrage primaire.

Ne pas filtrer sur le nom libre `copropriete`, sauf pour affichage ou recherche candidate.

### IVFFlat

Conserver :

```sql
SET LOCAL ivfflat.probes = 10;
```

À appliquer dans la transaction de recherche vectorielle, surtout quand un filtre `code_ncg` est utilisé.

### Diversité

Conserver la logique existante :

- RRF k=60 ;
- hybrid vector + BM25 ;
- diversité par `groupe_doc` ou équivalent ;
- limite par source ;
- exclusion par défaut des `BORDEREAU_AR` ;
- déduplication texte ;
- quota RCP si `include_legal_context = true`.

### Multi-copro

Ajouter une règle d'équilibrage.

Exemple :

```text
max_chunks = 12
copro_codes = 3 copros
=> viser 4 chunks max par copro, avec redistribution si une copro n'a aucun résultat
```

### Global discovery

Ne pas utiliser le même scoring qu'une recherche finale.

Retourner plutôt :

```json
{
  "code_ncg": "NCG_001",
  "nom": "Résidence Exemple",
  "match_count": 12,
  "doc_types": ["PV_AG", "CONTRAT"],
  "years": [2021, 2022, 2023],
  "top_evidence_snippet": "..."
}
```

---

## 12. Sécurité pilote

Le plan initial accepte une Function URL authless pour le pilote.

C'est acceptable uniquement avec des garde-fous additionnels.

### Obligatoire avant pilote

1. User DB read-only `mcp_ncg_reader`.
2. SSL obligatoire vers RDS.
3. Caps serveur :
   - `max_chunks <= 30`
   - `max_results <= 50`
   - `max_chars <= 50000`
4. Logs CloudWatch pour chaque appel tool :
   - timestamp ;
   - tool name ;
   - scope_mode ;
   - copro_codes ;
   - max_chunks ou max_chars ;
   - latence ;
   - nombre de résultats ;
   - warning éventuel.
5. Refus des requêtes d'extraction massive :
   - tous les documents ;
   - toute la copro ;
   - toute la base ;
   - toutes les pièces jointes.
6. Rotation du mot de passe DB après pilote.
7. Ne jamais exposer les variables d'environnement dans les erreurs.

### Fortement recommandé

- Ajouter un secret path non devinable dans l'URL MCP, par exemple `/mcp/<random_slug>`.
- Allowlist IP si possible.
- Alarme CloudWatch sur volume anormal.
- Passage Cognito OAuth en P2, avant ouverture hors pilote.

---

## 13. Tests de non-régression obligatoires

Ne pas se limiter aux 6 questions de recette initiales.

Créer un fichier de tests, par exemple :

```text
tests/fixtures/palim_mcp_eval_questions.json
```

### Minimum : 20 tests

| Catégorie | Nombre | Objectif |
|---|---:|---|
| Mono-copro explicite | 5 | Vérifier précision avec `single_copro` |
| Requête ambiguë | 5 | Vérifier clarification ou `PALIM_list_copros` |
| Comparaison multi-copro | 3 | Vérifier équilibrage par copro |
| Global discovery | 3 | Identifier les copros pertinentes sans réponse finale |
| Juridique RCP/PV/Contrat | 2 | Vérifier contexte légal et quota RCP |
| Hors-sujet / extraction abusive | 2 | Vérifier refus ou warning contrôlé |

### Critères bloquants

Un test échoue si :

- la réponse mélange deux copros sans le signaler ;
- une recherche globale est utilisée comme base de réponse finale ;
- un document complet est extrait sans raison ni limite ;
- les résultats ne contiennent pas `code_ncg` ;
- le serveur retourne une exception brute ;
- le retrieval MCP diverge fortement du retrieval Streamlit sur une requête mono-copro équivalente.

---

## 14. Nouvelle checklist d'exécution

### P0 — Contrat multi-copro avant code serveur

- [ ] Ajouter `scope_mode`.
- [ ] Remplacer `copro` par `copro_codes`.
- [ ] Créer les validateurs de scope.
- [ ] Définir les formats de retour structurés.
- [ ] Ajouter warnings et erreurs contrôlées.

### P0 — Refactoring retrieval

- [ ] Créer `PALIM_config.py`.
- [ ] Créer `PALIM_db.py`.
- [ ] Extraire `PALIM_retrieval.py`.
- [ ] Supprimer toute dépendance `streamlit`.
- [ ] Conserver RRF, BM25, vector, diversité, exclusion AR, quota RCP.
- [ ] Ajouter équilibrage multi-copro.
- [ ] Ajouter mode global discovery.

### P0 — Query planning

- [ ] Créer `PALIM_plan_query`.
- [ ] Implémenter règles déterministes V1.
- [ ] Préparer compatibilité avec routeur Haiku V2.
- [ ] Tester sur requêtes ambiguës.

### P1 — Tools MCP

- [ ] Implémenter `PALIM_search_chunks`.
- [ ] Implémenter `PALIM_list_copros`.
- [ ] Implémenter `PALIM_get_full_document`.
- [ ] Implémenter `PALIM_search_dossiers`.
- [ ] Ajouter descriptions riches de tools.
- [ ] Vérifier JSON Schema.

### P1 — Tests locaux

- [ ] Test CLI `PALIM_retrieval.py`.
- [ ] Test MCP Inspector.
- [ ] Test `tools/list`.
- [ ] Test d'une recherche mono-copro.
- [ ] Test d'une requête ambiguë.
- [ ] Test d'un refus d'extraction massive.

### P1 — Déploiement pilote

- [ ] Build image Docker.
- [ ] Push ECR.
- [ ] Déployer Lambda.
- [ ] Créer Function URL streaming.
- [ ] Configurer env vars DB.
- [ ] Vérifier Bedrock InvokeModel.
- [ ] Connecter Claude Teams.

### P1 — Sécurité pilote

- [ ] User DB read-only testé.
- [ ] SSL RDS testé.
- [ ] Caps serveur testés.
- [ ] Logs CloudWatch activés.
- [ ] Erreurs sans secrets.
- [ ] Mot de passe DB prêt à rotation.

### P2 — Après pilote

- [ ] Cognito OAuth.
- [ ] Langfuse tracing.
- [ ] Rerank Cohere.
- [ ] Tool analytique SQL.
- [ ] HNSW pour scale 150 copros.
- [ ] Circuit breaker Bedrock/RDS.

---

## 15. Instructions concrètes pour Claude Code

Claude Code doit travailler dans cet ordre.

### Étape 1 — Modifier le plan existant

Mettre à jour `PLAN_ACTION_MCP_PALIM.md` avec les décisions de ce document.

Ne pas coder tant que le contrat de tools n'est pas clarifié.

### Étape 2 — Créer les modules purs

Créer :

```text
Scripts/mcp_server/
  PALIM_config.py
  PALIM_db.py
  PALIM_retrieval.py
  PALIM_query_planner.py
  PALIM_dossiers.py
  PALIM_server.py
  PALIM_run_local.py
  requirements.txt
  Dockerfile
```

### Étape 3 — Implémenter la validation de scope

Créer un module ou des fonctions internes :

```python
validate_scope(scope_mode, copro_codes)
normalize_copro_codes(copro_codes)
build_scope_warning(...)
```

Aucune requête SQL de retrieval ne doit partir avant validation de scope.

### Étape 4 — Refactoriser la recherche

Extraire depuis `streamlit_app.py`, mais ne pas copier aveuglément.

Supprimer :

- `st.*`
- logique UI ;
- affichage ;
- état de session.

Conserver :

- embedding Titan ;
- hybrid search ;
- RRF ;
- BM25 ;
- vector search ;
- diversité source ;
- filtres document-level ;
- exclusion bordereau AR ;
- quota RCP ;
- déduplication ;
- seuils de qualité.

### Étape 5 — Écrire les tests avant déploiement

Créer un jeu de tests local exécutable en CLI.

Exemple :

```bash
python tests/test_palim_mcp_contracts.py
python tests/test_palim_retrieval_regression.py
```

Ces tests doivent pouvoir tourner avant Lambda.

### Étape 6 — Déployer seulement après validation locale

Ne pas passer au packaging Docker/Lambda tant que les tests suivants ne sont pas verts :

- recherche mono-copro ;
- comparaison multi-copro ;
- global discovery ;
- document tronqué ;
- extraction massive refusée ;
- MCP Inspector liste les tools.

---

## 16. Définition de succès

Le chantier est réussi si :

1. Claude Teams peut appeler PALIM via MCP.
2. Les réponses mono-copro restent au niveau de qualité de Streamlit.
3. Les requêtes ambiguës ne produisent pas de réponses finales non scopées.
4. Les comparaisons multi-copro sont explicites et équilibrées.
5. Les documents complets ne peuvent pas être aspirés massivement.
6. Les erreurs sont contrôlées et compréhensibles.
7. Le serveur reste compatible avec une future authentification Cognito.
8. Le code retrieval est réutilisable par Streamlit et par MCP.

---

## 17. Phrase de cadrage à conserver dans le README

PALIM MCP n'est pas un simple endpoint de recherche vectorielle. C'est un service retrieval multi-copropriété contrôlé, conçu pour permettre à Claude d'orchestrer des réponses fiables sans perdre les invariants métier nécessaires aux archives de copropriété : scope explicite, traçabilité documentaire, non-dilution inter-copro, et protection contre l'extraction massive.

