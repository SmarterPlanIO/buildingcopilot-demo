# PLAN — Immatriculation RNIC comme identifiant copro canonique (Delacour, puis standard produit)

> Date : 17/08/2026. Statut : PLAN VALIDÉ PAR LES FAITS, RIEN CODÉ.
> Décision proposée par Thai : utiliser le numéro d'immatriculation du Registre National
> d'Immatriculation des Copropriétés (RNIC, ANAH) comme identifiant unique des copros
> Delacour, en s'appuyant sur le MCP de l'État (data.gouv.fr) pour validation/enrichissement.

---

## 0. Pourquoi c'est la bonne décision (faits vérifiés le 17/08)

L'immatriculation RNIC est un identifiant **national, unique, stable, public** — exactement
ce qui manquait au produit multi-client (les codes internes varient par syndic, et on a
prouvé que les préfixes de fichiers Delacour peuvent venir de l'ANCIEN syndic, cf. 2190).

État des lieux mesuré :

| Source | Couverture | Détail |
|---|---|---|
| **Assynco Airtable** (champ `Numéro d'immatriculation`, table Copropriétés) | **30/31 copros Delacour**, 100 % au format valide `AA0000000` | Relevé live 17/08. Seul manque : 23 quai de Grenelle (hors liste des 25, dossier Drive vide). Inclut les 9 copros sans code Lobby ET `AH7171655` = 67 rue Escudier (confirme la copro du dossier `SDC - 92100`) |
| **Lobby** (étape 13/14 onboarding, "Numéro d'immatriculation du SDC") | affiché avec tirets (`AE3-410-578`) | Concordance vérifiée : Lobby `AE3-410-578` = Assynco `AE3410578` (100 Bd Victor Hugo) |
| **Docs Drive** (`2025 IMMATRICULATION.PDF` etc.) | ponctuel | source de recoupement |
| **RNIC data.gouv** | dataset ANAH `62da71c068871f4c54258c7c` ("Registre national d'Immatriculation des Copropriétés", 18 ressources) | vérifié via l'API data.gouv. Serveur MCP état : `https://mcp.data.gouv.fr/mcp` (public, sans auth, HTTP streamable) — `tools/list` OK ; `tools/call` a un caprice de handshake stateless à régler (voir §5) |

**Conséquence majeure** : le problème d'alignement Assynco (`{Ref client}` peuplé 2/31,
référentiels mélangés type "5807/2190") **disparaît** — on joint PALIM ↔ Assynco par
`Numéro d'immatriculation`, déjà peuplé à 97 %, sans rien demander à Assynco.

---

## 1. Format canonique et normalisation (règle produit)

- **Format canonique** : `^[A-Z]{2}\d{7}$` (9 caractères, ex. `AA6219950`). Stockage DB,
  clés de profil, params de tools : TOUJOURS canonique.
- **Normalisation** (`canon()`): upper + suppression de tout caractère non alphanumérique.
  `"ae3-410-578"`, `"AE3 410 578"`, `"AE3410578"` → `AE3410578`. Appliquée à TOUTE entrée :
  params des tools MCP (`copro_codes`, `code_ncg`), CLI `--copro`, chargement de profil,
  jointures Assynco, saisie utilisateur via le LLM client.
- **Format d'affichage** : `AA6-219-950` (groupes 3-3-3, comme Lobby/le registre) dans les
  sorties destinées aux humains ; le canonique reste accepté partout.
- **Garde-fou anti-régression NCG** : `canon()` est appliqué partout mais la VALIDATION
  stricte `[A-Z]{2}\d{7}` ne s'applique qu'aux codes qui contiennent des lettres. Les codes
  numériques courts (NCG `8050`, codes Lobby `0200`) restent valides tels quels : le produit
  supporte les deux régimes, le profil client déclare son régime canonique.

Implémentation : un module unique `copro_id.py` (Scripts/) vendorisé côté `mcp_server/`
(même mécanique que `rerank.py`), utilisé par pipeline + MCP + harness. Zéro duplication
de la logique de normalisation.

## 2. Modèle d'identité dans le profil client

`clients/<client>/client.json` — `included_copros` évolue de `{code: dossier}` vers :

```json
"included_copros": {
  "AA6219950": {
    "folder": "SDC 50 rue Vaneau - 75007",
    "lobby_code": "0200",
    "label": "SDC 50 rue Vaneau, 75007 Paris"
  }
}
```

- Rétrocompatibilité : valeur string = ancien format (NCG inchangé). `pipeline_config`
  normalise en interne vers le format riche.
- `lobby_code` conservé comme **alias de résolution** (les gestionnaires Delacour pensent
  encore en codes Lobby ; les tools de découverte doivent matcher les deux).
- Les 25 entrées Delacour sont **complètes dès maintenant** (mapping immatriculation ↔
  dossier Drive fait par nom/adresse, 24 nets + Escudier confirmé par AH7171655).

## 3. DB et pipeline

- `code_ncg` (colonne et param — nom hérité documenté) porte l'immatriculation canonique
  pour Delacour. Varchar : aucun changement de schéma.
- `06b` estampille déjà `code_ncg = --copro` (fix ac35b54) : il suffit que `--copro` reçoive
  l'immatriculation. `ingest.py`, `run_pipeline_per_copro`, paths per-copro
  (`per_copro/AA6219950/`) : suivent mécaniquement via `pipeline_config`.
- Migration du pilote : recharger 0200→`AA6219950` et 0179→`AC9872896` depuis les shards
  conservés (06b + 09, ~0 $, ~15 min), purge des anciens codes. Aucune ré-ingestion LLM.

## 4. MCP serveur (adaptations)

1. **Normalisation d'entrée** sur tous les tools acceptant des codes (`PALIM_scope`,
   `search_chunks`, `get_full_document`, `copro_overview`, tools Assynco...) : `canon()`
   en tête, avant SQL/Airtable. Un humain peut taper `ae3-410-578` ou `AE3 410 578`.
2. **`PALIM_assynco._get_copro_record`** : la validation actuelle `code.isdigit()` rejette
   les immatriculations → remplacer par `canon()` + match. Résolution Airtable par
   `{Numéro d'immatriculation}` (canonisé des deux côtés dans la formule) avec repli
   `{Ref client}` pour NCG. L'isolation tenant (allowlist Syndic) reste inchangée et
   fail-closed.
3. **Docstrings** : exemples mis à jour ("code copro : immatriculation RNIC ex.
   `AE3-410-578`, ou code interne syndic ex. `5390` — toutes graphies acceptées").
4. **08_airtable_sync (sinistres) — à traiter AVANT activation pour Delacour** : le match
   NCG `FIND("(code)",{Name})` ne fonctionnera pas (les {Name} des sinistres Delacour ne
   contiennent pas l'immatriculation). Stratégie cible : résoudre la copro par
   immatriculation → record id → sinistres par lien (champ lié), déclarée par profil
   (`assynco.sinistre_match: "name_code"` (ncg) / `"copro_link"` (delacour)). À vérifier
   sur les données réelles des sinistres Delacour avant de coder.

## 5. Intégration Registre de l'État (data.gouv MCP)

**Usage : outillage d'onboarding/validation (batch), PAS de dépendance runtime** des tools
PALIM (latence + disponibilité d'un service tiers dans le chemin de réponse = non).

- Script d'onboarding `onboard_rnic_check.py` (Scripts/, générique produit) : pour chaque
  copro du profil, interroge le RNIC et produit un rapport d'écarts :
  immatriculation existe ? adresse/CP concordent avec le dossier Drive ? statut
  (immatriculée/à jour) ? métadonnées utiles (nb lots, type de syndic) pour enrichir
  `copro_synthese`.
- **Deux canaux d'accès, dans cet ordre de préférence** :
  1. API REST data.gouv directe (`/api/1/datasets/62da71c068871f4c54258c7c` + API
     tabulaire sur la ressource RNIC) — simple, robuste, testée OK le 17/08.
  2. MCP `https://mcp.data.gouv.fr/mcp` — pertinent quand c'est le LLM qui interroge
     (ex. futur tool `PALIM_copro_registre` à la demande). Constaté le 17/08 :
     `initialize` et `tools/list` OK (en-tête `MCP-Protocol-Version` requis),
     `tools/call` renvoie 500 en stateless → gérer le handshake de session complet
     dans l'implémentation (ou client MCP standard type fastmcp.Client).
- Points de vigilance : taille des ressources RNIC vs API tabulaire (CSV ≤ 100 Mo —
  vérifier si la ressource nationale est requêtable ou s'il faut les découpes
  départementales) ; fraîcheur (millésimes) ; certificats SSL du Python local
  (utiliser `certifi`/`requests`, le store Windows du Python 3.14 a échoué sur
  mcp.data.gouv.fr).

## 6. Ce qui ne change pas

- NCG : codes `8050` etc. restent canoniques (leurs users et les Instructions v1.9 les
  connaissent). La normalisation est transparente pour eux.
- Schéma DB, nom de colonne `code_ncg`, contrat des 12 tools (seulement docstrings).
- Isolation tenant Assynco (allowlist Syndic, fail-closed).

## 7. Risques et cas limites

| Risque | Traitement |
|---|---|
| Copro non immatriculée au RNIC (obligation légale mais retards réels) | repli `lobby_code` comme canonique temporaire + backfill ; le rapport RNIC les liste |
| Immatriculation changeante (fusion/scission de SDC) | rare ; procédure documentée : UPDATE code_ncg + reload per-copro |
| Écarts Assynco ↔ RNIC (faute de frappe dans Airtable) | le check RNIC (§5) les détecte avant ingestion |
| Syndicats secondaires / unions (plusieurs immatriculations par adresse) | le RNIC les distingue ; le mapping folder↔immatriculation est fait par copro, pas par adresse |
| Collision de régimes (un code Lobby numérique = un code NCG) | impossible inter-client (DB séparées par client) |

## 8. Séquencement d'exécution (estimations)

1. **P0 — Rapport de validation RNIC des 25** (½ session) : script batch API data.gouv,
   croisement Assynco/Lobby/Drive, GO/NO-GO par copro. Zéro impact code produit.
2. **P1 — `copro_id.py` + profil enrichi** (½ session) : canon/validate/display, loader
   `pipeline_config` rétrocompatible, 25 entrées Delacour complètes, tests unitaires.
3. **P2 — MCP** (½ session) : normalisation des entrées, `_get_copro_record` par
   immatriculation, docstrings. Image v9 requise pour la prod (déjà en attente).
4. **P3 — Migration pilote** (¼ session) : reload 0200/0179 sous immatriculations,
   purge anciens codes, re-tests MCP live.
5. **P4 — Sinistres Delacour** (séparé) : audit du format {Name} Assynco côté Delacour,
   puis stratégie `copro_link` dans 08.
6. **P5 (option, plus tard)** — tool `PALIM_copro_registre` (RNIC à la demande via MCP
   état) + enrichissement `copro_synthese` (nb lots, mandat).

Ordre de reprise recommandé : P0 immédiatement (aucun risque), P1-P3 dans la même session
de travail, P4 avant l'activation du sync sinistres Delacour, P5 au besoin.
