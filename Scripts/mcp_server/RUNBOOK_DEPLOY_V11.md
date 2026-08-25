# RUNBOOK - Deploiement MCP PALIM image v11 (soft delete + perimetres nommes)

> Date : 24/08/2026. A executer en AWS CloudShell (console, compte 046004768626,
> region eu-west-1, Docker preinstalle, creds heritees). Duree ~10 min.
> Prod actuelle : **v10** sur les 2 Lambdas (verifie le 24/08).
>
> REGLE ABSOLUE, heritee du post-mortem v9 : on rebuilde toujours sous un tag
> NEUF, JAMAIS en ecrasant un tag existant. Le 1er build v9 avait casse les deux
> Lambdas (cause : mcp 2.x ; pin `mcp[cli]==1.27.2` commite depuis, garde-fou
> ci-dessous). Ecraser un tag rend le rollback impossible.
>
> v10 -> v11 embarque (tout est deja merge sur main) :
>   1. **Filtre soft delete** (`59df1b7`) : `PALIM_retrieval.hybrid_search` ajoute
>      `NOT c.retrieval_exclu` au WHERE. Les version chains (v1 d'un PV dont la v2
>      existe) et les variantes (suffixe `_RGPD` chez Delacour) sortent du
>      retrieval par defaut. Elles restent accessibles par `get_chunks` /
>      `get_full_document`, et un simple UPDATE les reactive. Patron BORDEREAU_AR.
>   2. **Perimetres nommes** (`b7a166a`) : Bloc 13, flotte demo 9 copros NGE,
>      coverage calcule sur perimetre explicite.
>
> ATTENTION — ce n'est pas un correctif discret. Les colonnes `retrieval_exclu` /
> `motif_exclusion` / `ref_source_file` existent sur les DEUX bases depuis le
> 21/08 (06a execute), et le retrieval va changer de comportement des le deploy :
> a ce jour **1 597 chunks NCG** et **57 chunks Delacour** portent le flag.
>
> Deploiement de CODE PUR : AUCUN changement d'env.json, de secret ou d'IAM.
> Rollback : repointer v10 via `update-function-code` sur les 2 fonctions.

## Pre-vol (a verifier AVANT de lancer le script)

- [ ] `main` a jour et contient `59df1b7` et `b7a166a`.
- [ ] Working tree propre cote `Scripts/mcp_server/` (3 suppressions de docs
      heritees de la reorganisation `ops/` a committer ou annuler avant build).
- [ ] Les 2 Lambdas sont bien en v10 (sinon adapter le rollback) :
      `aws lambda get-function --region eu-west-1 --function-name palim-mcp --query Code.ImageUri --output text`

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626
IMG="${ACC}.dkr.ecr.${REG}.amazonaws.com/palim-mcp:v11"

# 1. Recuperer main
if [ -d buildingcopilot-demo ]; then
  cd buildingcopilot-demo && git checkout main && git pull origin main
else
  git clone https://github.com/SmarterPlanIO/buildingcopilot-demo.git
  cd buildingcopilot-demo
fi
git log --oneline -1
grep "mcp\[cli\]==1.27.2" Scripts/mcp_server/requirements.txt   # GARDE-FOU incident v9
grep -n "NOT c.retrieval_exclu" Scripts/mcp_server/PALIM_retrieval.py  # le filtre est bien la

# 2. Build + push (vendorise dossiers_api + rerank + copro_id, build amd64, push ECR)
bash Scripts/mcp_server/build_and_push.sh v11

# 3. Repointer NCG SEULEMENT (decision du 24/08 : NCG d'abord, validation, puis Delacour)
aws lambda update-function-code --region $REG --function-name palim-mcp \
  --image-uri "$IMG" --query "LastUpdateStatus" --output text
aws lambda wait function-updated-v2 --region $REG --function-name palim-mcp
aws lambda get-function --region $REG --function-name palim-mcp \
  --query "Code.ImageUri" --output text

echo "==== v11 DEPLOYE sur palim-mcp (NCG). Delacour reste en v10. ===="
```

## Etape 2 — Delacour, SEULEMENT apres validation de NCG

L'image `v11` est deja construite et poussee : cette etape ne fait que repointer
la seconde Lambda. A lancer une fois les smokes 1 a 5 verts sur NCG.

```bash
REG=eu-west-1
IMG="046004768626.dkr.ecr.${REG}.amazonaws.com/palim-mcp:v11"
aws lambda update-function-code --region $REG --function-name palim-delacour-mcp \
  --image-uri "$IMG" --query "LastUpdateStatus" --output text
aws lambda wait function-updated-v2 --region $REG --function-name palim-delacour-mcp
aws lambda get-function --region $REG --function-name palim-delacour-mcp \
  --query "Code.ImageUri" --output text
echo "==== v11 DEPLOYE sur palim-delacour-mcp ===="
```

## Rollback (si un smoke echoue)

Repointer la seule fonction concernee. Tant que l'etape 2 n'est pas lancee,
Delacour est en v10 et n'a rien a annuler.

```bash
IMG10="046004768626.dkr.ecr.eu-west-1.amazonaws.com/palim-mcp:v10"
aws lambda update-function-code --region eu-west-1 --function-name palim-mcp \
  --image-uri "$IMG10" --query "LastUpdateStatus" --output text
aws lambda wait function-updated-v2 --region eu-west-1 --function-name palim-mcp
```

## Post-deploy (fait par Claude depuis le poste, prevenez-le)

1. **Smoke boot** (LE test de l'incident v9) : initialize + tools/list sur les 2
   URLs -> 13 tools listes. Si une Lambda ne boote pas, rollback immediat.
2. **Smoke soft delete NCG** : `PALIM_search_chunks` sur 5490 avec une requete qui
   ciblait la version anterieure flaggee -> elle ne doit plus remonter, la version
   de reference oui. Puis `PALIM_get_chunks` sur un `chunk_id` flagge -> doit
   TOUJOURS repondre (l'exclusion ne vaut que pour le retrieval).
3. **Smoke soft delete Delacour** — APRES l'etape 2 uniquement. Tant que Delacour
   est en v10, ses 57 chunks flaggees restent visibles au retrieval : c'est le
   comportement attendu, pas une regression. Tests : AE3410578 (chaine v1/v1/v2 du
   PV AG du 08/07/2021, 46 chunks flaggees) et variantes `_RGPD` de AA8785875
   (11 documents).
4. **Smoke non-regression** : `PALIM_run_analytical_query(operation="count",
   source="documents", doc_type="PV_AG")` -> doit couvrir les 18 copros NCG
   (etait 10 en v10, la flotte NGE a ete ingeree depuis) ;
   `PALIM_assynco_get_copro("5390")` -> resout (isolation tenant OK).
5. **Controle de perimetre NGE** : `PALIM_list_copros` doit rendre les 9 copros du
   pilote NGE, dont 5412 Bercy si son ingestion est terminee.
6. **Recoller les Project Instructions** cote Claude Teams si le Bloc 13
   (perimetres nommes) doit etre actif. Verifier l'echo de version en
   conversation neuve.

## Decisions actees

- **NCG d'abord, Delacour ensuite** (24/08) : l'etape 1 ne repointe que
  `palim-mcp`. Delacour reste en v10 jusqu'a validation des smokes 1, 2, 4 et 5.
  L'image etant deja poussee, l'etape 2 ne coute qu'un `update-function-code`.
- **Ordre vs ingestion Bercy** : le deploy est independant de l'ingestion, mais si
  Bercy tourne encore, la RDS est sous charge — decaler le smoke 4 (analytique)
  apres la fin de l'ingestion pour ne pas confondre lenteur et regression.

## Point ouvert

- **Cohabitation de versions** : entre l'etape 1 et l'etape 2, les deux clients
  tournent sur des images differentes (NCG v11, Delacour v10). Sans consequence
  fonctionnelle, les deux Lambdas etant independantes, mais a garder en tete si un
  incident survient cote Delacour pendant la fenetre : il ne viendra PAS de v11.
