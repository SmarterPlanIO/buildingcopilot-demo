# PLAN_ACTION — Intégration Assynco ERP dans le MCP PALIM

> Objectif : exposer l'ERP Assynco (Airtable) comme tools du serveur MCP PALIM.
> **Read-only d'abord sur tout le périmètre Assynco** (toutes les tables pertinentes,
> pas seulement les sinistres), **CRUD ensuite** derrière des garde-fous.
> Statut : PLAN. Aucun code écrit. Dernière mise à jour : 3 juin 2026.

---

## 0. Constat de départ (ce qui existe déjà)

- **Couche d'accès Airtable Assynco éprouvée mais partielle** : `Scripts/08_airtable_sync.py`
  interroge la base Assynco (`appi1ee5p93EBHtLR`, table Sinistre `tblvvkhcHZjDyHLdp`)
  en REST paginé (`fetch_airtable_records`), sélectionne ~80 champs (`AIRTABLE_FIELDS`),
  les mappe vers la table PostgreSQL `dossiers` (`map_airtable_to_dossier`). C'est un
  **sync batch unidirectionnel, read-only, limité aux sinistres**.
- **Filtre copro→sinistre validé** : `COPRO_FILTERS` = `FIND("(<code>)",{Name})` par copro.
  Les alias rue/résidence ont été **rejetés** (non 1:1 : TARIEL→5448/5443, CRESSON→5 codes,
  PATAY→8031/8032). Tranché via `diag_copro_filters.py`. → réutilisable comme helper de scope.
- **Serveur MCP PALIM** (`PALIM_server.py`, FastMCP, 5 tools RAG/dossiers en lecture) avec
  invariants : retours `{ok, ...}`, jamais d'exception brute, jamais de secret dans les
  messages, caps serveur, scope validé en amont, tracing Langfuse (`PALIM_tracing.py`).
- **Skill Assynco** (`assynco-erp.skill`) : couche métier (17 tables, gotchas linked-fields,
  glossaire) MAIS **archive incomplète** (références `data-model.md`/`patterns.md`/`glossary.md`
  absentes) et écrite contre des tools **génériques `airtable_*`** (convention `body.fields`),
  pas contre les tools PALIM.

**Conséquence** : il n'y a pas de « MCP Assynco » — il y a un sync sinistres. Le travail =
généraliser cette couche en tools MCP read-only multi-tables, puis CRUD.

---

## 1. Décisions d'architecture à trancher (avant code)

### D1 — Forme des tools : métier de haut niveau vs passthrough Airtable générique
- **Option A (passthrough générique)** : exposer dans PALIM des tools `airtable_list/get/...`
  imitant la surface attendue par le skill (`filterByFormula`, `body.fields`). Le skill
  Assynco marche **sans réécriture**. MAIS casse les invariants PALIM (scope, caps, retours
  structurés), expose Airtable brut, surface d'attaque large.
- **Option B (tools métier scopés)** : exposer des tools de haut niveau
  (`PALIM_assynco_search_sinistres`, `..._search_polices`, `..._get_quittances`, ...) qui
  encapsulent les requêtes, appliquent scope + caps + `{ok,...}`. Plus sûr, cohérent avec
  PALIM. MAIS le skill doit être **réécrit** pour cibler ces tools + références régénérées.
- **Reco : B**, avec une couche interne générique privée (`_airtable_list/_airtable_get`) et
  des tools métier publics par table. Le skill Assynco devient un skill PALIM (adapté).
  Option A possible en complément si un MCP Airtable générique tiers est déjà branché côté NCG.

### D2 — Scope d'accès (multi-tenant)
- L'accès Assynco est-il **partagé** (toutes les copros NCG) ou **cloisonné par utilisateur** ?
- Généraliser le scope copro au-delà des sinistres : quelles tables ont un lien copro
  exploitable ? (à confirmer par l'introspection R0). Le helper `FIND("(<code>)")` ne vaut
  que pour les tables dont un champ porte le code entre parenthèses.

### D3 — Secret PAT
- `AIRTABLE_PAT` doit passer par **Secrets Manager** (comme le mot de passe DB, cf
  [[feedback_clean_code_blocks]] / archi MCP), jamais en clair dans `env.json` ou le code.

### D4 — CRUD : périmètre et garde-fous (phase W, plus tard)
- Quelles tables/champs **inscriptibles** ? Le **delete** est-il autorisé ? (reco : non par défaut).
- Pattern d'écriture sûr : allowlist tables+champs, résolution linked-fields (display→recordId),
  wrapper `body.fields`, dry-run, confirmation explicite, audit (Langfuse + CloudWatch).

---

## 2. Phasage

### Phase R0 — Socle & schéma (prérequis)
1. **Introspecter la base** Assynco via l'API metadata
   `GET https://api.airtable.com/v0/meta/bases/{baseId}/tables` (PAT) → liste des 17 tables,
   leurs champs, types, linked-fields.
2. **Générer les `references/`** du skill à partir de cette introspection :
   `data-model.md` (tables + champs + relations), `patterns.md` (recettes filtres),
   `glossary.md` (FR/EN assurance). → résout l'archive incomplète **et** documente le périmètre.
3. **Sélectionner les tables pertinentes** pour le read-only (cf §3) et, par table, le
   sous-ensemble de champs exposés (ne pas tout tirer — cf `AIRTABLE_FIELDS` comme modèle).
4. **Mettre `AIRTABLE_PAT` dans Secrets Manager** + wiring config (`PALIM_config.py`).

### Phase R1 — Tools read-only multi-tables
1. **`PALIM_assynco.py`** (nouveau module mcp_server) :
   - Client Airtable interne repris de `08` mais **générique** : `_airtable_list(table, formula,
     fields, max)`, `_airtable_get(table, record_id)`. Paginé, field-select, timeouts, retries.
   - Helper scope : `copro_formula(code_ncg)` = `FIND("(<code>)",{<name_field>})` généralisé.
   - Mapping par table (réutiliser `map_airtable_to_dossier` pour Sinistre).
2. **Tools MCP read-only** (dans `PALIM_server.py`, mêmes invariants que les 5 existants) —
   liste cible (à confirmer en R0) :
   - `PALIM_assynco_search_sinistres` — live (complète/raffraîchit le `PALIM_search_dossiers` DB).
   - `PALIM_assynco_search_polices` — polices d'assurance (MRI, etc.) par copro/statut.
   - `PALIM_assynco_search_quittances` — quittances/primes.
   - `PALIM_assynco_search_cotations` — devis/cotations.
   - `PALIM_assynco_get_contact` / `..._search_contacts` — contacts (syndic, expert, assureur).
   - `PALIM_assynco_search_organisations` — organisations (cabinets, compagnies).
   - `PALIM_assynco_get_bordereaux` — bordereaux.
   - `PALIM_assynco_get_record(table, record_id)` — drilldown plafonné (anti-aspiration).
   Chaque tool : scope copro optionnel, caps (max records, field-select), `{ok, results[], warnings}`,
   pas d'exception brute, tracing Langfuse.
3. **Décision live vs DB** pour les sinistres : R1 lit Airtable **live** (données fraîches) ;
   garder le sync `dossiers` pour le RAG (chunks virtuels) et le fallback hors-ligne.

### Phase R2 — Alignement du skill
1. **Réécrire `assynco-erp` en skill PALIM** ciblant les tools `PALIM_assynco_*` (Option B),
   OU documenter le branchement d'un MCP Airtable générique (Option A) si retenu.
2. **Packager** dans `Scripts/mcp_server/skills/assynco-erp/` (SKILL.md + references générées),
   comme `ncg-redaction-livrable`. → livrable « adaptation Assynco » complet et cohérent.

### Phase W1 — CRUD (après validation read-only)
1. Tools d'écriture derrière un **flag serveur** (`cfg.ENABLE_ASSYNCO_WRITE`, défaut False).
2. Garde-fous : allowlist tables+champs, résolution linked-fields, wrapper `body.fields`,
   **pas de delete par défaut**, confirmation, audit complet.
3. Tools : `PALIM_assynco_create_*`, `PALIM_assynco_update_*` sur le périmètre autorisé.

---

## 3. Périmètre data (à figer en R0)

Tables candidates (d'après le skill — à confirmer/compléter par l'introspection) :
Sinistres, Polices, Cotations, Quittances, Contacts, Organisations, Bordereaux.
Pour chacune, R0 produit : clé primaire, champ portant le code copro (le cas échéant),
champs exposés en lecture, linked-fields, filtres usuels.

Caveat scope : seules les tables avec un champ `(<code>)` exploitable bénéficient du helper
copro `FIND`. Les autres (organisations, contacts globaux) seront non-scopées ou scopées via
linked-field (résolution display→recordId).

---

## 4. Sécurité & gouvernance

- **PAT en Secrets Manager**, jamais en clair (cf §1 D3). Rôle Lambda read-only en R1.
- **Read-only strict en R1/R2** : aucun tool d'écriture déployé tant que W1 n'est pas validé.
- **Caps & anti-aspiration** : `max_records`, field-select obligatoire, drilldown plafonné.
- **Invariants PALIM** repris tels quels : `{ok,...}`, pas d'exception brute, pas de secret
  dans les messages, scope validé, tracing Langfuse par tool.
- **Audit écriture (W1)** : chaque create/update tracé (qui, quoi, avant/après) → Langfuse + CloudWatch.

---

## 5. Tests & validation

- **Contract tests** (modèle `tests/test_palim_mcp_contracts.py`) : forme `{ok,...}`, caps,
  erreurs contrôlées, scope. Read-only → exécutables sans risque.
- **Eval questions** : étendre `tests/palim_mcp_eval_questions.json` avec des cas Assynco
  (« quelle est la police MRI de la copro X ? », « sinistres en cours chez Y »).
- **Smoke live** : 1 requête par table contre la base réelle (lecture), bornée.

---

## 6. Livrables

1. `PALIM_assynco.py` (client générique + mapping par table).
2. Tools `PALIM_assynco_*` read-only dans `PALIM_server.py` + config (`PALIM_config.py`).
3. `AIRTABLE_PAT` câblé via Secrets Manager.
4. Skill `skills/assynco-erp/` régénéré (SKILL.md + references) — livrable adaptation Assynco.
5. Tests (contracts + eval) + smoke live.
6. Déploiement via `Dockerfile`/`build_and_push.sh` existants.
7. (W1) tools CRUD + garde-fous + audit, derrière flag.

---

## 7. Décisions en attente (bloquantes pour démarrer)

1. **D1** : tools métier scopés (Option B, reco) vs passthrough générique (A) ?
2. **D2** : accès Assynco partagé ou cloisonné ? Existe-t-il déjà un MCP Airtable générique
   branché côté NCG (qui changerait le calcul A vs B) ?
3. **D4** : périmètre CRUD futur (tables/champs inscriptibles, delete autorisé ?).
4. **R0** : fournir l'`AIRTABLE_PAT` (lecture) pour introspecter la base et figer la liste
   exacte des tables/champs + générer les `references/`.
