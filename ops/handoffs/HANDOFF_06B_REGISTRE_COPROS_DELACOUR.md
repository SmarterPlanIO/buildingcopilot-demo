# Transfert de tâche — 3 correctifs `06b_load_db.py` + rerun sur Delacour

> Rédigé le 22/09/2026 par la session PALIM principale, pour **palim_louise**.
> Ce document se suffit à lui-même : contexte, cause prouvée, correctifs, procédure,
> preuves de sortie. Aucune question ouverte : tout ce qui devait être décidé l'a été.
> Lire `CLAUDE.md` (racine) avant de commencer : git sur Google Drive, `PYTHONIOENCODING`,
> secrets via Secrets Manager, chemins accentués.

## 0. En une phrase

Le registre annuaire `copros` n'est écrit qu'à moitié par `06b` (code + immatriculation,
jamais le nom ni l'adresse), en best-effort (un échec passe pour un succès), et la
correction de type de document faite par 04 n'est jamais propagée aux chunks. Trois
correctifs dans un seul fichier, puis un rerun de 06b sur les 25 copros Delacour.

## 1. D'où ça vient (relevé client Delacour du 21/09, instruit sur pièces)

| Relevé Delacour | Cause prouvée | Correctif |
|---|---|---|
| Le 67 rue Escudier apparaît sous « SDC - 92100 », sans adresse, introuvable par son nom | `06b` n'écrit dans `copros` que `code_ncg` + `immatriculation` ; `nom_residence`/`adresse` sont NULL pour les **24** copros ; l'annuaire retombe sur le nom du dossier Drive | **C1** |
| Le 9 rue Edmond Nocard (`AJ6978050`) remonte sans immatriculation | Table `copros` = **24 lignes pour 25 copros**. Nocard, dernière copro du run du 27/08 (09:47:51), n'a pas sa ligne alors que ses 197 chunks et 11 documents sont chargés. Le bloc d'upsert est en `try/except` qui imprime un avertissement et continue | **C2** |
| Le PV du 25/03/2026 d'Escudier ressort en « Comptabilité » | 03 classe par dossier Drive d'origine (`Comptabilité` → `COMPTABILITE`) ; 04 corrige en `PV_AG` dans `documents.doc_type_corrige` ; la recherche filtre bien sur `COALESCE(doc_type_corrige, doc_type)` ; mais `chunks.doc_type` (celui affiché dans les citations) n'est jamais corrigé. **15 documents** Delacour dans ce cas | **C3** |

Le filtre de `PALIM_list_copros` n'est PAS en cause (testé en direct : « Vaneau » → 1
résultat). Il renvoie la liste complète quand aucun score ne matche ; ce comportement
changera dans l'image v13, hors périmètre ici. Une fois C1 appliqué, « Escudier » matche.

## 2. Les trois correctifs (tous dans `Scripts/06b_load_db.py`)

### C1 — Écrire le libellé et l'adresse dans `copros`

Bloc actuel, en fin de fichier (≈ lignes 517-531) :

```python
_reg_codes = [COPRO] if COPRO else sorted(pcfg.COPRO_META)
_reg_rows = [(c, pcfg.immatriculation_of(c)) for c in _reg_codes]
execute_values(cur, """
    INSERT INTO copros (code_ncg, immatriculation) VALUES %s
    ON CONFLICT (code_ncg) DO UPDATE SET immatriculation = EXCLUDED.immatriculation
""", _reg_rows)
```

Cible : ajouter `nom_residence` et `adresse`, tirés du profil client
(`pcfg.COPRO_META[code]`, dict avec `folder`, `label`, `lobby_code`, `immatriculation`,
`raw_dir`). Règles :
- `nom_residence` = `label` s'il existe, sinon `folder` (jamais NULL) ;
- `adresse` = `label` aussi (le label Delacour est déjà une adresse complète :
  « SDC 67 rue Escudier, 92100 Boulogne-Billancourt ») ; pour NCG, `label` est souvent
  absent → `adresse` reste NULL, c'est acceptable (leur annuaire cherche par code) ;
- `ON CONFLICT ... DO UPDATE` sur les trois colonnes (le rerun doit écraser les NULL).

Colonnes existantes de la table : `code_ncg, nom_residence, adresse, rue, aliases,
immatriculation` (créée par 06a, cf. `06a_init_db.py` « Table copros »). Ne pas
toucher au schéma.

### C2 — Upsert `copros` bloquant + invariant de recette

- Retirer le `try/except` autour de l'upsert : une exception doit **arrêter 06b avec un
  code de sortie non nul** (le driver `ingest.py` utilise `subprocess.run(..., check=True)`,
  il s'arrêtera aussi). Garder un message clair pointant vers `06a_init_db.py` si la table
  n'existe pas, mais lever ensuite.
- Ajouter à `Scripts/tests/recette_fiche_v2.py`, à côté de l'invariant I0 (≈ ligne 66),
  un invariant **I0b** : « toute copro ayant des chunks a une ligne dans `copros` avec
  `nom_residence` non NULL ». Bloquant (`dur=True`). Il aurait attrapé Nocard le 10/09.

### C3 — Propager `doc_type_corrige` aux chunks

Après le chargement des documents (l'upsert `INSERT INTO documents ... doc_type_corrige`
est ≈ ligne 381), et **restreint à la copro chargée** (`WHERE c.code_ncg = %s`, jamais
global) :

```sql
UPDATE chunks c
SET doc_type = d.doc_type_corrige
FROM documents d
WHERE d.source_file = c.source_file
  AND c.code_ncg = %s
  AND d.doc_type_corrige IS NOT NULL
  AND d.doc_type_corrige <> c.doc_type
```

Points d'attention :
- `doc_type_corrige` est déjà validé contre la liste fermée (≈ ligne 339, repli `AUTRE`) :
  pas de valeur inconnue possible ;
- garder `BORDEREAU_AR` intact (il n'est jamais une valeur corrigée, mais vérifier que
  l'UPDATE ne le touche pas : ajouter `AND c.doc_type <> 'BORDEREAU_AR'`) ;
- afficher le nombre de chunks réalignés dans la sortie de 06b.

Ne PAS modifier `03_chunking.py` ni `04_metadata_documents.py` : la correction existe
déjà, seul le chargement l'ignore.

## 3. Tests avant tout rerun

- `python -m py_compile 06b_load_db.py`
- Test unitaire à ajouter (`tests/test_06b_registre.py`, sans DB) : sur un profil
  factice, la fonction qui construit `_reg_rows` rend `(code, immat, label, label)` et
  `(code, immat, folder, None)` quand `label` est absent. Petit, mais il fige le contrat.
- Canari : **une** copro Delacour d'abord, `AJ6978050` (Nocard, 15 fichiers, la plus
  petite, et c'est celle qui manque) :

```bash
cd "G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Déploiement IA interne/Scripts"
export PALIM_CLIENT=delacour PYTHONIOENCODING=utf-8
export DB_PASSWORD="$(aws secretsmanager get-secret-value --secret-id palim/delacour/ragadmin --region eu-west-1 --query SecretString --output text | python -c "import json,sys; s=sys.stdin.read().strip(); d=json.loads(s); print(d.get('password') or d.get('DB_PASSWORD') or s)")"
export AIRTABLE_PAT="$(aws secretsmanager get-secret-value --secret-id palim/airtable_pat --region eu-west-1 --query SecretString --output text | python -c "import json,sys; s=sys.stdin.read().strip(); d=json.loads(s); print(d.get('AIRTABLE_PAT') or d.get('pat') or d.get('token') or s)")"
export DB_HOST="$(python -c "import pipeline_config as p; print(p.require_db_host())")"
python 06b_load_db.py --copro AJ6978050
python 08_airtable_sync.py            # OBLIGATOIRE après tout 06b (restaure les dossiers Airtable)
```

Attendu après le canari : `SELECT * FROM copros WHERE code_ncg='AJ6978050'` renvoie une
ligne avec `nom_residence = 'SDC 9 rue Edmond Nocard, 94410 Saint-Maurice'`.

## 4. Rerun sur les 25 copros Delacour

**Ce qu'il faut savoir sur 06b `--copro`** : c'est un `DELETE WHERE code_ncg` + `INSERT`
depuis les shards `Résultats bruts/per_copro/<code>/` (chunks_avec_embeddings_sq.jsonl,
documents_metadata.jsonl, dossiers.jsonl). Il ne relance ni extraction ni embeddings ni
Haiku : **coût LLM 0 $**. Le coût est en temps RDS (t4g.micro, saturation IOPS sur les
gros rechargements) : compter 1 à 2 h pour les 25 copros, à lancer hors des heures
d'usage de Delacour.

Prérequis : les shards des 25 copros existent (ils datent du run du 27/08 et des
rattrapages du 10/09 ; vérifier `ls "Résultats bruts/per_copro/"`). Si un shard manque,
NE PAS lancer 06b sur cette copro (elle serait vidée) : signaler.

Séquence, en boucle sur les codes du profil, dans cet ordre, sans parallélisme :

```bash
for C in $(python -c "import pipeline_config as p; print(' '.join(sorted(p.COPRO_META)))"); do
  echo "=== $C $(date +%H:%M)"
  python 06b_load_db.py --copro "$C" || { echo "ECHEC $C"; break; }
done
python 08_airtable_sync.py
python 09b_resolutions.py --all
python 09_copro_synthese.py --all
python tests/recette_fiche_v2.py
```

Pourquoi 09b/09 à la fin : le delta du driver `ingest.py` est par nom de fichier, un
rechargement ne le déclenche pas (leçon du 10/09) ; ici on n'utilise pas le driver, donc
on les lance explicitement. `09_copro_synthese.py --all` est gratuit (zéro LLM) et remet
les fiches en cohérence avec la base (invariant I4 de la recette).

## 5. Preuves de sortie (toutes obligatoires)

1. `SELECT COUNT(*) FROM copros` = **25** ; `COUNT(*) FILTER (WHERE nom_residence IS NULL)`
   = **0**.
2. `SELECT COUNT(DISTINCT d.source_file) FROM documents d JOIN chunks c USING (source_file)
   WHERE d.doc_type_corrige = 'PV_AG' AND c.doc_type <> 'PV_AG'` = **0** (était 15).
3. `tests/recette_fiche_v2.py` : **RECETTE OK**, avec le nouvel invariant I0b.
4. Smoke par le chemin client (connecteur MCP Delacour ou Function URL) :
   - `PALIM_list_copros("Escudier")` → 1 résultat, `nom` = « SDC 67 rue Escudier, … » ;
   - `PALIM_list_copros("Nocard")` → 1 résultat, avec `immatriculation` `AJ6978050` ;
   - `PALIM_search_chunks` scopé `AH7171655`, `doc_type="PV_AG"` → le PV du 25/03/2026
     ressort avec `doc_type` PV_AG dans la citation.
5. Commit sur `PALIM_gestion_projet`, merge `main`, push (pattern git du CLAUDE.md :
   `taskkill //F //IM git.exe; sleep 2; rm -f .git/index.lock` avant chaque commande).
   Message : `fix(06b): registre copros complet et bloquant + doc_type corrige propage aux chunks`.
   **Puis reporter le commit sur la branche Delacour** (`PALIM_Delacour_Patrimoine`) :
   c'est une branche orpheline à parité v12, elle ne reçoit pas les merges de main.
   Méthode éprouvée : `git format-patch` sur main, `git am` dans un clone shallow de la
   branche (le clone local ne fetch plus cette branche proprement).

## 6. Hors périmètre de cette tâche (ne pas entreprendre)

- Ajout des 4 copros Delacour manquantes (Coubertin `AB8763831`, Alma `AD1265248`,
  Pasteur `AC8312977`, Blomet `AC1168715`) : chantier séparé avec `add_copro.py` puis
  `ingest.py` (extraction + LLM, coût à estimer).
- `PALIM_list_copros` : liste vide au lieu de liste complète quand rien ne matche → image
  v13.
- Timeout Assynco (`TimeoutError` sur `list_polices`, 3 occurrences en 21 jours) → v13.
- NCG : les mêmes correctifs s'appliquent à sa base (même 06b), mais le rerun NCG n'est
  pas demandé ici. Ne pas lancer 06b sur NCG.

## 7. Pièges connus

- `PALIM_CLIENT=delacour` doit être exporté AVANT tout import de `pipeline_config`, sinon
  le profil chargé est NCG et `require_db_host()` pointe la mauvaise RDS (le script
  refuse de démarrer si le host est vide, mais pas s'il est celui d'un autre client).
- Le Drive partagé Delacour est en lecture seule et n'est pas touché par 06b.
- `08_airtable_sync.py` après 06b n'est pas optionnel : le `DELETE WHERE code_ncg` retire
  les dossiers virtuels Airtable de la copro.
- Ne jamais lancer `06b_load_db.py` **sans** `--copro` : c'est le mode legacy, TRUNCATE
  global de la base.
