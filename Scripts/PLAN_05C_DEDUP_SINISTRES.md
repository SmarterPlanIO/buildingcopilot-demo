# Plan — Chantier 05c + dédup sinistres (refs + groupage)

> Objectif : réduire la sur-extraction des dossiers sinistres RAG (8050 : 132 pour ~50 réels)
> et rendre applicable la règle de fusion par référence demandée par Thai (un dossier non daté
> peut fusionner s'il partage une référence sinistre, signal plus fort que nom+date).
> Statut : PLAN, non implémenté. À valider avant exécution.

---

## 1. Causes racines confirmées (dans le code)

1. **Détection de folder cassée** (`05c_entity_extraction.py:247`) : le code cherche un segment
   de chemin exactement égal à `"SINISTRE"`, or le vrai dossier est `"5 - SINISTRES"`. Le match
   échoue quasi toujours → `folder_name` vide → fallback sur `lese_nom` extrait par Haiku, qui
   varie (HIM / Leng HIM / M Leng HIM) → doublons de variantes de nom. C'est le plus gros levier.
2. **`type_dossier` dans la clé de groupage** (`05c:286`) : un même sinistre éclate en
   `SINISTRE_DDE` + `SINISTRE_AUTRE` quand ses documents sont classés en sous-types différents.
3. **`num_sinistre` extrait puis jeté** (`05c:342`, utilisé seulement dans `resume_ia:408`) :
   le prompt Haiku remonte déjà `num_sinistre` et `num_police`, mais ils ne sont PAS persistés
   comme champs du dossier. Donc aucune référence partageable n'existe aujourd'hui dans les données
   (confirmé : 0 ref dans le schéma dossiers, 0 document partagé entre dossiers).

---

## 2. Changements prévus

### A. `05c_entity_extraction.py` — qualité d'extraction
- **A1. Corriger la détection de folder** : matcher le segment sinistres quel que soit le préfixe
  (`5 - SINISTRES`, `SINISTRES`, `SINISTRE`) via regex `^\d*\s*-?\s*SINISTRES?$` (insensible casse).
  Prendre le sous-dossier suivant comme dossier. Gérer le niveau de regroupement `DOMMAGE OUVRAGE`
  (prendre le sous-dossier d'après : `DO LIM`, `DO LAUBIER`). Pour les docs hors `5 - SINISTRES`
  (3-AG, 9-Gestion, 13-DIVERS, 16-PROCEDURE), pas de folder → fallback `lese_nom` (inchangé, la
  dédup 00c rattrape).
- **A2. Persister les références** : ajouter `num_sinistre`, `num_police` (et `ref_compagnie` si
  présent) comme champs du dict dossier. Agrégation = première valeur non nulle des documents du
  groupe (même logique que `best_*`).
- **A3. Retirer `type_dossier` de la clé de groupage** : grouper par `(copro, folder_slug)`. Le
  type du dossier devient le plus spécifique des documents du groupe (priorité DDE > INCENDIE >
  MRI > AUTRE). Réduit le type-split à la source.

### B. `00c_dedup_dossiers_rag.py` — fusion (déjà écrit, à étendre)
- **B1. Ajouter la fusion par référence** : deux dossiers fusionnent si `num_sinistre` normalisé
  identique et non vide, **indépendamment de la date**. C'est la règle demandée par Thai : rescue
  les dossiers non datés via une clé forte. Priorité sur la règle date+nom existante.
- **B2. Conserver la règle actuelle** (datés, ≤30j, nom sous-ensemble, pas de conflit appt) comme
  voie secondaire pour les dossiers sans `num_sinistre`.
- **B3. Émettre un remapping `old_dossier_id -> kept_dossier_id`** dans le rapport (traçabilité ;
  utile si un jour on veut réaligner `chunks.dossier_id`, non requis — cf §3).

### C. Schéma DB — `06a_init_db.py` + chargement
- **C1.** `ALTER TABLE dossiers ADD COLUMN num_sinistre TEXT, num_police TEXT` (idempotent
  `IF NOT EXISTS`). Sert la traçabilité et le futur chantier RAG↔Assynco (clé de jointure forte).
- **C2.** Mettre à jour l'INSERT de `load_dossiers_only.py` (et `06b_load_db.py` pour cohérence)
  pour charger ces deux colonnes.
- *Optionnel* : si on ne veut pas toucher la DB tout de suite, garder `num_sinistre` uniquement
  dans `dossiers.jsonl` pour la dédup (pré-chargement) et ne pas ajouter les colonnes. Recommandé :
  ajouter les colonnes (faible coût, gain de traçabilité).

---

## 3. Analyse d'impact sur le pipeline d'ingestion RAG

| Élément | Impact | Verdict |
|---|---|---|
| **`chunks.dossier_id` (retrieval)** | **Donnée morte** : aucun consommateur. PALIM_retrieval = 0 usage ; MCP = `dossier_id` seulement sur table `dossiers`, aucun tool ne le prend en entrée ; Streamlit = chunks virtuels construits depuis la table `dossiers`, jamais via `chunks.dossier_id`. | **Aucun impact RAG** si les `dossier_id` changent/fusionnent. |
| **Embeddings (05)** | 05c ne recalcule jamais les embeddings, il ajoute juste un champ. | Aucun re-embed, aucun coût Titan. |
| **03 / 04 / 05 / 05b** | En amont de 05c, non touchés. | Aucun impact. |
| **`chunks_avec_embeddings_sq.jsonl`** | 05c le réécrit en place (INPUT == OUTPUT_CHUNKS). | **Backup obligatoire** avant re-run (risque corruption si interruption). |
| **Reload DB** | `chunks.dossier_id` étant mort, `load_dossiers_only.py` (dossiers seuls) suffit pour la justesse. 06b complet (166k chunks) seulement si on veut `chunks.dossier_id` cohérent (cosmétique). | Reload léger : load_dossiers_only + 08. Pas de 06b. |
| **`08_airtable_sync.py`** | `load_dossiers_only` fait TRUNCATE dossiers → efface les dossiers Airtable. | **Relancer 08 après** (inchangé, déjà au runbook). |
| **Colonnes DB num_sinistre/num_police** | Si ajoutées (C1), `load_dossiers_only` doit les charger (C2), sinon INSERT échoue. | Coupler C1 et C2. |
| **Toutes copros** | Re-run 05c re-groupe TOUTES les copros (dossiers.jsonl global), pas que 8050. | Valider 3-4 copros, pas seulement 8050. |

**Conséquence nette** : le chantier est confiné aux dossiers. Le RAG documentaire (chunks,
embeddings, retrieval) n'est pas affecté car le seul lien chunk→dossier en base n'est lu par
personne. Pas de re-chunking, pas de re-embedding.

---

## 4. Séquence d'exécution proposée

```
0. cp dossiers.jsonl dossiers.jsonl.bak
   cp chunks_avec_embeddings_sq.jsonl chunks_..._sq.jsonl.bak     # 05c réécrit ce fichier
1. Éditer 05c (A1, A2, A3) + 00c (B1, B2, B3) + 06a/load_dossiers_only (C1, C2)
2. PYTHONIOENCODING=utf-8 python 05c_entity_extraction.py          # ~<1$ Haiku
3. PYTHONIOENCODING=utf-8 python 00c_dedup_dossiers_rag.py         # dédup (refs + date)
   -> inspecter dossiers_dedup_report.txt sur 8050 + 2-3 autres copros
4. cp dossiers_dedup.jsonl dossiers.jsonl                          # figer après validation
5. DB_PASSWORD=... python load_dossiers_only.py
6. AIRTABLE_PAT=... DB_HOST=... DB_PASSWORD=... python 08_airtable_sync.py
7. Smoke test MCP : PALIM_search_dossiers scopé 8050 -> n_total cohérent, pas de variantes
```

Rollback : restaurer les `.bak`, relancer load_dossiers_only + 08.

---

## 5. Critères de succès vérifiables

- 8050 : nombre de dossiers nettement réduit (cible ~50-60), variantes de nom (HIM, LIM, MICHA)
  collapsées, et **sinistres distincts du même lésé/appartement préservés** (MICHA 2023 ≠ 2025).
- Les dossiers non datés portant un `num_sinistre` partagé sont fusionnés (règle Thai).
- Aucune régression retrieval : `PALIM_search_chunks` sur 8050 renvoie les mêmes chunks qu'avant
  (les embeddings et le texte sont inchangés).
- `08_airtable_sync` re-injecte correctement les dossiers Assynco des copros assurées.

---

## 6. Points en suspens (à trancher avec Thai)

- C optionnel : ajoute-t-on les colonnes DB maintenant, ou refs en jsonl seulement ?
- A3 : retirer `type_dossier` de la clé est plus agressif ; si on préfère minimal, garder la clé
  actuelle et laisser 00c gérer le type-split (déjà le cas). À décider.
- Le bruit "syndic pris pour lésé" (BELLMAN, FONCIA, IMMO EXPRESS) n'est traité par AUCUN de ces
  changements (c'est un défaut de jugement Haiku sur "qui est le lésé"). Amélioration possible du
  prompt 05c, hors scope de ce plan.
