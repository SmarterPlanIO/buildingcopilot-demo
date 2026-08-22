# RUNBOOK - Deploiement MCP PALIM v7

> Deploiement v7 : expose deux fonctionnalites deja codees + mergees sur main :
>   1. Fiche synthese par copro       -> tool PALIM_copro_overview (commit 5894d25)
>   2. Sourcage Source N a la demande  -> citation par chunk dans search_chunks
>                                         + tool PALIM_get_chunks (commit 9be2fa2)
>
> Nature : deploiement de CODE PUR. Les deux tools lisent en SQL sur des tables
> existantes (copro_synthese, chunks). PAS de nouveau secret, PAS de nouvelle
> policy IAM, PAS de nouvelle var d'env. Le Dockerfile fait `COPY *.py` donc il
> embarque deja PALIM_overview.py et PALIM_retrieval.py sans modif.
>
> Tout se fait en AWS CloudShell (creds heritees de la console). Region eu-west-1.
> Compte : 046004768626. Lambda : palim-mcp. Image courante : v6 -> cible v7.

---

## Pre-requis

- Le code est sur main (commits 5894d25 + 9be2fa2 deja merges). Verifier en CloudShell
  apres clone/pull que `git log --oneline -3` montre bien ces commits.
- CloudShell a Docker preinstalle et les creds de la console.

---

## Etape 1 - Recuperer main dans CloudShell

```bash
# Si le repo n'est pas encore clone dans CloudShell :
git clone https://github.com/SmarterPlanIO/buildingcopilot-demo.git
cd buildingcopilot-demo

# Si deja clone : se mettre a jour sur main
git checkout main && git pull origin main

git log --oneline -3   # doit afficher ab8ab3c / 9be2fa2 / 5894d25
```

---

## Etape 2 - (Verification) table copro_synthese peuplee en prod

Le tool PALIM_copro_overview lit la table `copro_synthese`. Si une copro n'y est pas,
le tool ne plante pas : il renvoie {ok:true, precomputed:false} avec les faits live
(SQL) mais sans narratif. Cette etape verifie juste que la pre-computation 09 a bien
ete poussee en prod (10 copros attendues au 05/06).

Depuis une machine ayant l'acces RDS (ou via le tool une fois v7 deploye) :

```bash
# psql direct (remplacer le mot de passe ; ne jamais le committer)
PGPASSWORD="..." psql \
  -h sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com \
  -U <db_user> -d <db_name> \
  -c "SELECT code_ncg, nom, generated_at FROM copro_synthese ORDER BY generated_at;"
```

- Si la table contient les copros attendues -> rien a faire, passer a l'etape 3.
- Si vide ou incomplete -> lancer la pre-computation contre la PROD (couteux Haiku,
  ~0,069 USD pour 10 copros) AVANT ou APRES le deploy (le tool degrade proprement
  en attendant) :
  ```bash
  cd Scripts
  PYTHONIOENCODING=utf-8 DB_HOST="sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com" \
    DB_PASSWORD="..." python 09_copro_synthese.py   # ajouter --copro <code> pour cibler
  ```

NB : cette etape est independante du deploy de l'image. v7 peut etre deploye meme si
la table n'est pas encore peuplee.

---

## Etape 3 - Build + push de l'image v7 (CloudShell)

Le script vendorise dossiers_api.py et rerank.py depuis ../Streamlit Cloud/, cree le
repo ECR si absent, build linux/amd64, tag et push. Idempotent.

```bash
bash Scripts/mcp_server/build_and_push.sh v7
```

A la fin il affiche le digest de palim-mcp:v7 dans ECR.

---

## Etape 4 - Pointer la Lambda sur v7 (CloudShell)

Pas de changement d'env ni d'IAM : seul l'image-uri change. On attend la fin de l'op
avec `wait function-updated`.

```bash
aws lambda update-function-code \
  --function-name palim-mcp \
  --image-uri 046004768626.dkr.ecr.eu-west-1.amazonaws.com/palim-mcp:v7 \
  --region eu-west-1
aws lambda wait function-updated --function-name palim-mcp --region eu-west-1
```

Verifier que la Lambda pointe bien sur v7 (NB : c'est get-function, pas
get-function-configuration : le bloc Code n'est pas dans la config) :

```bash
aws lambda get-function --function-name palim-mcp \
  --region eu-west-1 --query 'Code.ImageUri' --output text
```

---

## Etape 5 - Smoke test live

Rafraichir le connecteur MCP PALIM cote Claude (Desktop / claude.ai) pour recharger
la liste des tools, puis tester avec une copro connue (ex code 5390).

Nouveaux tools v7 :
- PALIM_copro_overview(code_ncg="5390")
  Attendu : {ok:true, precomputed:true, narratif:"...", faits:{...}, freshness:{...}}.
  Si precomputed:false -> table copro_synthese non peuplee pour cette copro (etape 2).
- PALIM_get_chunks(chunk_ids=[<un id retourne par search_chunks>])
  Attendu : {ok:true, chunks:[{...texte, source_file, chunk_index...}], not_found:[]}.

Regression (sourcage par chunk) :
- PALIM_search_chunks(query="extincteurs", code_ncg="5390")
  Attendu : chaque hit porte desormais sa citation/chunk_id pour le sourcage a la
  demande (cf. commit 9be2fa2). Verifier que les ids retournes sont reutilisables tels
  quels dans PALIM_get_chunks.

Non-regression generale (doivent toujours repondre {ok:true}) :
- PALIM_list_copros(), PALIM_search_dossiers(code_ncg="5390"),
  PALIM_assynco_get_copro(code_ncg="5390"), PALIM_get_visite_3d(query="extincteurs 5390").

---

## Rollback

Repointer la Lambda sur l'image precedente (v6) :

```bash
aws lambda update-function-code \
  --function-name palim-mcp \
  --image-uri 046004768626.dkr.ecr.eu-west-1.amazonaws.com/palim-mcp:v6 \
  --region eu-west-1
aws lambda wait function-updated --function-name palim-mcp --region eu-west-1
```

Les images ECR precedentes restent disponibles tant qu'elles ne sont pas purgees.
