# Runbook — handoff 06B (registre copros Delacour) sur `main`

> 22/09/2026. Exécution sur le PC Windows, checkout `main` de `PALIM_gestion_projet` :
> `G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Scripts`.
> Écart assumé par rapport au handoff §5 : **pas de report sur la branche orpheline
> `PALIM_Delacour_Patrimoine`** (décision du 22/09 : une seule ligne de dev, `main` ;
> la branche est auditée puis remplacée par un tag ou une `release/` à historique commun).

## 0. Ce que livre `apply_handoff_06b.py` (idempotent, ancres vérifiées, CRLF conservé)

| Fichier | Changement | Handoff |
|---|---|---|
| `pipeline_config.py` | `registre_row(code, meta=None)` → `(code, immat, nom_residence, adresse)` | C1 |
| `06b_load_db.py` | UPDATE `chunks.doc_type` ← `documents.doc_type_corrige`, scopé copro, hors `BORDEREAU_AR`, compteur affiché | C3 |
| `06b_load_db.py` | upsert `copros` sur 4 colonnes, bloquant (`UndefinedTable` → message + `raise`) | C1+C2 |
| `tests/recette_fiche_v2.py` | invariant **I0b** bloquant | C2 |
| `tests/test_06b_registre.py` | nouveau, sans DB, 4 tests | §3 |
| `clients/delacour/client.json` | label Escudier sans « (dossier Drive 'SDC - 92100') » | — |

Deux choix à connaître :
- `adresse = COALESCE(EXCLUDED.adresse, copros.adresse)` : le profil écrase quand il a une
  valeur, sinon on garde ce qui est en base (NCG n'a pas de label → adresse conservée).
- l'UPDATE C3 ne tourne qu'en mode `--copro` (le handoff interdit le global).

## 1. Branche + patch

```bash
cd "G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Déploiement IA interne/Scripts"
taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f ../.git/index.lock
git -C .. status --short            # doit être propre
git -C .. checkout -b fix/06b-registre-copros main
python ops/handoffs/apply_handoff_06b.py .    # ou : python <chemin>/apply_handoff_06b.py .
git -C .. diff --stat                # attendu : 4 fichiers modifiés + 1 nouveau
```

Si le script s'arrête sur « ancre trouvée 0 fois » : le fichier a bougé depuis le 25/08 —
me renvoyer `git log -3 --oneline -- Scripts/06b_load_db.py` et le bloc concerné, je
réajuste l'ancre. Rien n'est écrit dans ce cas.

## 2. Tests hors DB

```bash
export PALIM_CLIENT=delacour PYTHONIOENCODING=utf-8
python -m py_compile 06b_load_db.py pipeline_config.py tests/recette_fiche_v2.py
python tests/test_06b_registre.py            # 4 test(s) OK — client=delacour
```

## 3. Canari : Nocard (`AJ6978050`, 11 documents, la copro sans ligne registre)

```bash
export DB_PASSWORD="$(aws secretsmanager get-secret-value --secret-id palim/delacour/ragadmin --region eu-west-1 --query SecretString --output text | python -c "import json,sys; s=sys.stdin.read().strip(); d=json.loads(s); print(d.get('password') or d.get('DB_PASSWORD') or s)")"
export AIRTABLE_PAT="$(aws secretsmanager get-secret-value --secret-id palim/airtable_pat --region eu-west-1 --query SecretString --output text | python -c "import json,sys; s=sys.stdin.read().strip(); d=json.loads(s); print(d.get('AIRTABLE_PAT') or d.get('pat') or d.get('token') or s)")"
export DB_HOST="$(python -c "import pipeline_config as p; print(p.require_db_host())")"
echo "$DB_HOST"                               # DOIT contenir sp-rag-delacour-copros
ls "C:/Users/thai-/palim-delacour/Résultats bruts/per_copro/AJ6978050/"   # shard présent ?
python 06b_load_db.py --copro AJ6978050
python 08_airtable_sync.py
```

Sortie attendue de 06b : `✅ doc_type réaligné ... N chunk(s) de AJ6978050`, puis
`✅ Registre copros mis à jour (1 ligne(s))` avec
`nom='SDC 9 rue Edmond Nocard, 94410 Saint-Maurice'`.

Vérification (psql ou `python -c` psycopg2) :
```sql
SELECT code_ncg, immatriculation, nom_residence, adresse FROM copros WHERE code_ncg='AJ6978050';
```

## 4. Rerun des 25 copros (hors heures Delacour, 1-2 h RDS, coût LLM 0)

Prérequis : les 25 shards existent — **ne pas lancer 06b sur une copro sans shard, elle
serait vidée**.

```bash
for C in $(python -c "import pipeline_config as p; print(' '.join(sorted(p.COPRO_META)))"); do
  test -f "C:/Users/thai-/palim-delacour/Résultats bruts/per_copro/$C/chunks_avec_embeddings_sq.jsonl" || { echo "SHARD MANQUANT $C"; break; }
  echo "=== $C $(date +%H:%M)"
  python 06b_load_db.py --copro "$C" || { echo "ECHEC $C"; break; }
done
python 08_airtable_sync.py
python 09b_resolutions.py --all
python 09_copro_synthese.py --all
python tests/recette_fiche_v2.py             # RECETTE OK, I0b compris
```

## 5. Preuves de sortie

```sql
SELECT COUNT(*), COUNT(*) FILTER (WHERE nom_residence IS NULL) FROM copros;        -- 25 | 0
SELECT COUNT(DISTINCT d.source_file) FROM documents d JOIN chunks c USING (source_file)
 WHERE d.doc_type_corrige IS NOT NULL AND c.doc_type <> d.doc_type_corrige
   AND c.doc_type <> 'BORDEREAU_AR';                                                -- 0
```
Smoke MCP (connecteur Delacour) : `PALIM_list_copros("Escudier")` → nom « SDC 67 rue
Escudier, 92100 Boulogne-Billancourt » ; `PALIM_list_copros("Nocard")` → immatriculation
`AJ6978050` ; `PALIM_search_chunks(AH7171655, doc_type="PV_AG")` → PV du 25/03/2026 cité
en PV_AG. NB : « Escudier » renverra encore les 25 copros tant que l'image v13 n'est pas
déployée (filtre annuaire = liste complète quand rien ne matche) — mais le nom sera juste.

## 6. Commit

```bash
taskkill //F //IM git.exe 2>/dev/null; sleep 2; rm -f ../.git/index.lock
git -C .. add Scripts/06b_load_db.py Scripts/pipeline_config.py Scripts/tests/recette_fiche_v2.py \
              Scripts/tests/test_06b_registre.py Scripts/clients/delacour/client.json
git -C .. commit -m "fix(06b): registre copros complet et bloquant + doc_type corrige propage aux chunks"
git -C .. checkout main && git -C .. merge --ff-only fix/06b-registre-copros && git -C .. push
```

## 7. Audit de la branche Delacour (remplace l'étape « report » du handoff)

```bash
git -C .. fetch origin PALIM_Delacour_Patrimoine
git -C .. diff --stat main origin/PALIM_Delacour_Patrimoine
git -C .. log --oneline -20 origin/PALIM_Delacour_Patrimoine
```
Me renvoyer les deux sorties : selon le contenu (profil seul / gel v12 / code spécifique)
on tague `delacour/v12` sur `main`, ou on crée `release/delacour-v12` depuis le commit
`main` correspondant, puis on supprime l'orpheline.

## 8. Pour la remarque 4 du client (PV absents) — à faire sur le PC, 5 min

```bash
for C in AB8546467 AC9872896 AE1302603 AJ6978050; do
  echo "== $C"; python -c "import json;d=json.load(open('C:/Users/thai-/palim-delacour/Résultats bruts/per_copro/$C/filtrage_rapport.json',encoding='utf-8'));import re;print([f for f in json.dumps(d,ensure_ascii=False).split('\"') if re.search(r'(?i)\bpv\b|proc.s.verbal|assembl',f)])"
done
```
Si la liste est vide pour une copro, aucun fichier ressemblant à un PV n'a été présenté au
pipeline : le manque est côté Drive Delacour. Sinon, c'est le filtrage 01 qui l'a écarté.
