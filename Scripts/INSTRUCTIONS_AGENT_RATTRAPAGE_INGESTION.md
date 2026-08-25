# Instructions agent — Ingestion et rattrapage d'ingestion PALIM (multi-client)

> Fichier a donner tel quel a un agent LLM (Claude Code) charge d'ingerer des copros
> ou de rattraper une ingestion apres decouverte d'un bug pipeline.
> Redige le 22/08/2026 apres le rattrapage du bug de dedup proche (tete-500c,
> fix `6863e3d` du 21/08). Concu pour etre reutilise a chaque futur rattrapage.
> Contexte produit : PALIM = RAG documentaire de coproprietes, un client syndic
> = une base RDS dediee + un serveur MCP dedie. Repo : SmarterPlanIO/buildingcopilot-demo.

---

## 0. Regle d'or : un agent = un client = une base

- Chaque client a sa RDS, ses secrets, son profil `Scripts/clients/<client>/client.json`.
  On ne melange JAMAIS deux clients dans une meme base ni dans une meme session d'agent.
- **Avant de demarrer, verifier aupres de Thai qu'aucun autre agent ne travaille sur la
  meme base client.** Deux agents sur deux clients differents = OK (seul le quota Bedrock
  est partage, les retries absorbent le throttling). Deux agents sur le meme client = INTERDIT
  (le `06b` fait DELETE+INSERT per-copro : deux writers sur la meme copro se corrompent).
- Le selecteur de client est la variable d'env `PALIM_CLIENT` (defaut `ncg`).
  Delacour : `PALIM_CLIENT=delacour`. Toujours l'exporter explicitement, jamais l'assumer.
- **Copro Delacour `AE8711459` (105 av. de Verdun) : blocage LEVÉ le 26/08/2026** (mandat
  renouvelé avec Delacour, confirmé par Thai). Ingestion autorisée comme les autres copros.

## 1. Preflight (10 min, obligatoire)

1. Lire `CLAUDE.md` a la racine du repo (regles git Google Drive, encodage, secrets).
   Invoquer le skill `palim-vibe-startup` si disponible.
2. Verifier que le clone est a jour de `main` et contient les fixes pipeline recents :
   ```bash
   git log --oneline -3 -- Scripts/03_chunking.py Scripts/05c_entity_extraction.py
   ```
   Attendu au minimum : `6863e3d` (dedup 2 etages) et `83a55ad` (_scalar 05c).
   **Ingerer avec un 03 anterieur a 6863e3d recree le bug de dedup : STOP si absent.**
3. Prefixer toute commande Python de `PYTHONIOENCODING=utf-8` (console Windows cp1252).
4. Paths accentues (`Déploiement`, `Résultats`) : toujours entre guillemets doubles,
   copies depuis un `ls`, jamais retapes en ASCII.

## 2. Secrets (Secrets Manager uniquement, jamais en clair, jamais commites)

| Client | Secret ecriture (pipeline) | Format | Secret lecture (verifs) | Format |
|---|---|---|---|---|
| NCG | `palim/ragadmin` | **JSON** `{"DB_PASSWORD": "..."}` | `palim/mcp_ncg_reader` | chaine brute |
| Delacour | `palim/delacour/ragadmin` | chaine brute | `palim/delacour/mcp_reader` | chaine brute |
| (commun) | `palim/airtable_pat` (Airtable Assynco) | chaine brute | — | — |

Piege verifie en prod : les formats different par client. Extraction sure :
```bash
S="$(aws secretsmanager get-secret-value --secret-id <SECRET> --region eu-west-1 --query SecretString --output text)"
case "$S" in "{"*) S="$(printf '%s' "$S" | python -c "import sys,json;print(list(json.load(sys.stdin).values())[0])")";; esac
```
Hotes DB : NCG `sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com`,
Delacour `sp-rag-delacour-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com`.
Les users `mcp_*_reader` sont en LECTURE SEULE : verifs uniquement. Le pipeline ecrit via `ragadmin`.

## 3. Qu'est-ce qu'un rattrapage, et comment etablir la liste

Un fix pipeline ne repare que les ingestions FUTURES. Les documents deja ecartes a tort
par l'ancienne regle restent absents de la base : il faut re-derouler la chaine sur les
copros touchees. La source de verite est le **registre d'ingestion** (table
`ingestion_registre`, presente dans chaque base client) :

```sql
-- Liste authoritative des copros a rattraper et volumes attendus
SELECT code_ncg, COUNT(*) AS docs_a_recuperer
FROM ingestion_registre
WHERE statut = 'REJETE' AND motif = '<MOTIF_DU_BUG>'   -- ex. 'DOUBLON_PROCHE'
GROUP BY 1 ORDER BY 2 DESC;
```

Etat au 22/08/2026 pour le bug DOUBLON_PROCHE (a re-verifier avant de lancer, un autre
agent a pu avancer) :
- **NCG : 2 356 docs / 10 copros residentielles** (8050=1281, 5390, 5480, 8030, 5427,
  5548, 5553, 5033, 5499, 5354). Les copros NCG GE ingerees apres le fix sont saines.
- **Delacour : 1 921 docs / 22 copros** (AA8321549=224 en tete, puis degresse).
- Une copro jamais ingeree n'a RIEN a rattraper (rien n'a ete rejete pour elle).

Note 8050 (STYLE — 145 av. de France) : un rattrapage a ete lance puis stoppe proprement
le 22/08 pendant la phase 01 (aucune ecriture DB, caches intacts). Relancer = repartir
de la commande standard, rien de special a faire.

## 4. Executer (une copro a la fois, sequentiel, petites d'abord)

La chaine complete est pilotee par `ingest.py` — ne pas orchestrer les etapes a la main :

```bash
export PALIM_CLIENT=<client> PYTHONIOENCODING=utf-8
export DB_HOST="<hote du client>"
export DB_PASSWORD="<extrait du secret ragadmin, cf. section 2>"
export AIRTABLE_PAT="<extrait de palim/airtable_pat>"
cd "<repo>/Scripts"
python ingest.py --copro <CODE> --keep-shards
```

- `--keep-shards` toujours : conserve les caches per-copro pour les reprises.
- Boucle multi-copros : sequentielle, `|| echo "ECHEC <code> — on continue"` entre les
  copros (une copro qui echoue ne doit pas bloquer le lot), bilan a la fin.
- Lancer en tache de fond ; tolerer les longues phases SILENCIEUSES du log (buffering
  Python + etapes 01/02 peu bavardes : 30-60 min muettes = normal sur une grosse copro).
  Preuve d'activite sans log : compter les fichiers du dossier filtre de la copro
  (`Résultats bruts/Archives_Filtrees/<dossier copro>/`) a 20 s d'intervalle.
- Un rattrapage est BON MARCHE : Textract est en cache (zero OCR), seuls les docs
  recuperes passent par Haiku/Titan. Ordre de grandeur constate : ~0,002-0,02 $/doc.
- Interrompre un run est SANS RISQUE : `06b` (ecriture DB) n'arrive qu'en fin de chaine ;
  un kill avant laisse la base intacte et les caches reutilisables.

## 5. Verification apres chaque copro (obligatoire, user reader)

```sql
-- 1. Le trou est-il referme ? (attendu : 0, ou tres faible si OCR illisibles)
SELECT COUNT(*) FROM ingestion_registre
WHERE code_ncg='<CODE>' AND statut='REJETE' AND motif='<MOTIF>';
-- 2. Volumetrie coherente (comparer avant/apres : chunks et documents en hausse)
SELECT COUNT(*) FROM chunks WHERE code_ncg='<CODE>';
SELECT COUNT(*) FROM documents WHERE code_ncg='<CODE>';
```
Spot-check qualite : 1 doc recupere au hasard -> verifier qu'il a des chunks en DB et
que son doc_type est plausible.

## 6. Pannes connues et remedes (vecues, pas theoriques)

| Symptome | Cause | Remede |
|---|---|---|
| `password authentication failed` immediat | secret JSON passe brut comme mot de passe | extraction section 2 |
| Crash 05c `dict(entities)` ou concat str/list | sortie Haiku non conforme | fixes `5158281`+`83a55ad` requis (preflight) |
| `06b` echoue sur tres grosse copro (OOM RDS t4g.micro) | pic memoire au DELETE+INSERT | retenter (souvent transitoire) ; sinon relancer seulement `06b` puis `09` ; en dernier recours upgrade temporaire t4g.small |
| `shutil.copy2` WinError 1 / EINVAL sur `.gsheet` | GoogleDriveFS / placeholders Google natifs | deja gere par 01 (stream-copy + exclusion) — ne pas "corriger" |
| `.git/index.lock` | GoogleDriveFS | pattern CLAUDE.md : `taskkill //F //IM git.exe; sleep 2; rm -f .git/index.lock` |
| Log de tache fige 30-60 min | buffering + phase 01/02 silencieuse | verifier l'activite disque (section 4), ne pas tuer |
| `06b` tres long sur "Generation de l'index full-text BM25" (57 min mesurees sur 285 k lignes, 24/08) | l'UPDATE `WHERE text_search IS NULL` seul force un seq scan de TOUTE la table (+ concurrence autovacuum), et sature les IOPS de la t4g.micro | fix = filtre `AND code_ncg = %s` en mode per-copro (parcours via l'index btree). Verifier sa presence dans TON clone : `grep -n "AND code_ncg" Scripts/06b_load_db.py` pres de `_SQL_BM25`. Plus la base grossit, plus l'absence du fix coute cher (chaque copro ajoutee ralentit les 06b de TOUTES les suivantes) |

Ne JAMAIS : tuer `GoogleDriveFS.exe` ; ecrire dans le Drive source d'un client (lecture
seule stricte) ; lancer `06b` global sans `--copro` (TRUNCATE toute la base) ; retirer un
filtre tenant Assynco ; afficher un secret dans un log ou un commit.

## 7. Si TU decouvres un nouveau bug majeur pendant une ingestion

1. **Stopper le lot** (fin de la copro en cours ou kill, sans risque cf. section 4).
2. Prouver la cause sur le traceback / les donnees (pas de fix "plausible").
3. Fixer le script generique, `py_compile`, commit + **merge dans main** (procedure
   CLAUDE.md), pour que les AUTRES agents heritent du fix immediatement.
4. S'assurer que le chemin de rejet ecrit dans `ingestion_registre` avec un motif dedie :
   c'est ce qui rend le rattrapage possible et chiffrable (le bug de dedup est reste
   invisible 5 mois faute de ce registre).
5. Relancer : les copros deja passees AVANT le fix deviennent un rattrapage (section 3) ;
   prevenir Thai avec le chiffrage (docs perdus, copros, cout estime) AVANT de relancer
   en masse.
6. Signaler le bug aux autres agents actifs via Thai (leur clone doit re-puller main).

## 8. Key learnings des sessions precedentes (memoire consolidee)

**Identite des copros — ne jamais se fier a un surnom.**
- Le code canonique est l'immatriculation RNIC sans tirets (`AA0000000`) pour les
  nouveaux clients, un code numerique interne pour NCG. `pipeline_config.resolve()`
  normalise toute graphie humaine ("AE3-410-578", "ae3 410 578", alias code Lobby)
  vers UN code et UN shard : toujours passer par lui, jamais par le nom.
- Piege vecu : "BERCY" designait la copro NCG 8050 (STYLE, 145 av. de France, quartier
  Bercy) dans nos notes, alors que "TOUR LYON BERCY" est la 5412 (lot GE). Avant d'agir
  sur une copro nommee en langage naturel, resoudre le code via `client.json` /
  `included_copros` et confirmer folder + code dans le rapport.
- Ajouter une copro a un client existant : `Scripts/add_copro.py` puis `ingest.py --copro`.
  Nouveau client syndic complet : skill `palim-onboarding-tenant` (RDS, secrets, Lambda).

**Ordre et dependances du pipeline (si etapes lancees a la main, hors ingest.py).**
- `08_airtable_sync` est OBLIGATOIRE apres CHAQUE `06b` sur une base qui a des dossiers
  Assynco : le DELETE/TRUNCATE de 06b efface les chunks virtuels Airtable. `ingest.py`
  le fait tout seul (et saute 08 si la copro n'a rien dans Airtable) — c'est une des
  raisons de ne pas orchestrer a la main.
- `05_embedding.py` est incremental en APPEND : en re-run manuel complet, purger d'abord
  `chunks_avec_embeddings*.jsonl` du shard, sinon doublons. Seul
  `chunks_avec_embeddings_sq.jsonl` est necessaire en aval (l'autre est un intermediaire).
- `04_metadata_documents.py` est l'etape Haiku la plus couteuse (par document, ~10-15 min
  sur une grosse copro) : c'est normal qu'elle traine, ne pas la relancer par impatience.

**Fenetres de tir sur une base utilisee par le client.**
- Le `06b` per-copro fait DELETE puis INSERT : pendant quelques minutes la copro est
  incomplete pour les users MCP. Sur une base en usage reel (beta users NCG), viser les
  creneaux hors bureau pour les recharges ; la cadence validee est le batch de nuit.

**Modele de cout (mesure, pas estime).**
- Haiku ~0,00053 $/chunk ; Textract ~0,0015 $/page (~10 pages/fichier ocerise) ;
  embeddings Titan negligeables. Une copro moyenne = 1-3 $, une grosse = 10-20 $.
- La dedup exacte SHA-256 (00b) est le levier n°1 : 20-60 % de vrais doublons dans les
  archives syndic (verifie octet par octet, 68/68 sur echantillon). Un taux eleve de
  doublons EXACTS est normal ; c'est un taux de doublons PROCHES tres superieur au taux
  SHA-256 de la meme copro qui doit alerter (c'est la signature du bug de 2026).
- La colonne "Textract estime" des logs est une hypothese (10 pages/fichier) : le reel
  se lit sur la facture AWS, ne pas presenter l'estimation comme un cout mesure.

**Validation : penser rappel, pas seulement precision.**
- Tous les benchs historiques verifiaient que les reponses etaient justes (precision) ;
  aucun ne verifiait que rien ne manquait (rappel). C'est pour ca que 5 000+ docs ont
  manque 5 mois sans alerte. Toute verification de fin de lot doit inclure un controle
  d'inventaire : docs decouverts = docs ingeres + rejets motives (le registre rend ce
  controle trivial, cf. section 5).

## 9. Rapport de fin de lot (a rendre a Thai)

Tableau par copro : docs recuperes vs attendus (registre), chunks avant/apres, cout Haiku
reel (lignes `Cout Haiku REEL` du log), echecs eventuels avec traceback resume. Plus :
cout total du lot, ecarts residuels (motifs des REJETE restants), et tout symptome anormal
meme non bloquant. Ne pas marquer le lot termine tant que la section 5 n'est pas verte
pour chaque copro.
