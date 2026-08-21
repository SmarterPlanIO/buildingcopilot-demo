# RUNBOOK - Deploiement MCP PALIM image v10 (analytique + immatriculation)

> Date : 21/08/2026. A executer en AWS CloudShell (console, compte 046004768626,
> region eu-west-1, Docker preinstalle, creds heritees). Duree ~10 min.
> Prod actuelle : v8 (le 1er build v9 avait casse les 2 Lambdas — cause mcp 2.x,
> pin mcp[cli]==1.27.2 commite depuis ; on rebuilde sous un tag NEUF, jamais en
> ecrasant un tag existant, cf. post-mortem RUNBOOK_DEPLOY_V9.md).
>
> v8 -> v10 embarque (tout doit etre merge sur main avant build) :
>   1. Tout le contenu prevu pour v9 (jamais deploye) : sourcage v1.9 (verbatim =
>      result.text, get_chunks = re-fetch), multi-client (PALIM_CLIENT,
>      ASSYNCO_SYNDIC_ALLOWLIST, fail-closed), immatriculations RNIC (canon() sur
>      les params, resolution Assynco par immat, copro_id vendorise).
>   2. Pin mcp[cli]==1.27.2 (7173870) — le fix de l'incident v9.
>   3. Immatriculation en ATTRIBUT (b26e8f0) : table copros, PALIM_list_copros
>      (+ recherche par immat 2 graphies), PALIM_copro_overview.
>   4. NOUVEAU TOOL PALIM_run_analytical_query (13e tool) : analytique inter-copro
>      whitelist (count/sum/list par copro, parc entier autorise), coverage +
>      facettes. Module PALIM_analytics.py, SQL pur read-only, zero Bedrock.
>
> Deploiement de CODE PUR : AUCUN changement d'env.json, de secret ou d'IAM.
> La table copros existe deja en prod NCG (06a execute le 19/08, peuplee 10/10).
> Rollback : repointer v8 via update-function-code sur les 2 fonctions.

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626
IMG="${ACC}.dkr.ecr.${REG}.amazonaws.com/palim-mcp:v10"

# 1. Recuperer main
if [ -d buildingcopilot-demo ]; then
  cd buildingcopilot-demo && git checkout main && git pull origin main
else
  git clone https://github.com/SmarterPlanIO/buildingcopilot-demo.git
  cd buildingcopilot-demo
fi
git log --oneline -1
grep "mcp\[cli\]==1.27.2" Scripts/mcp_server/requirements.txt  # GARDE-FOU incident v9

# 2. Build + push (vendorise dossiers_api + rerank + copro_id, build amd64, push ECR)
bash Scripts/mcp_server/build_and_push.sh v10

# 3. Repointer les DEUX Lambdas sur v10
for FN in palim-mcp palim-delacour-mcp; do
  aws lambda update-function-code --region $REG --function-name $FN \
    --image-uri "$IMG" --query "LastUpdateStatus" --output text
done
for FN in palim-mcp palim-delacour-mcp; do
  aws lambda wait function-updated-v2 --region $REG --function-name $FN
  aws lambda get-function --region $REG --function-name $FN \
    --query "Code.ImageUri" --output text
done

echo "==== v10 DEPLOYE sur palim-mcp et palim-delacour-mcp ===="
```

## Post-deploy (fait par Claude depuis le poste, prevenez-le)

1. Smoke boot (LE test incident v9) : initialize + tools/list sur les 2 URLs ->
   13 tools listes, dont PALIM_run_analytical_query.
2. Smoke analytique NCG : PALIM_run_analytical_query(operation="count",
   source="documents", doc_type="PV_AG") -> 10 lignes + coverage 10/10 ;
   spec invalide (doc_type sur source=dossiers) -> INVALID_ANALYTICAL_SPEC + allowed.
3. Smoke immatriculation : PALIM_list_copros(query="AE6-576-847") -> 5033 ;
   PALIM_copro_overview("8050") -> immatriculation AC0744680.
4. Smoke regression : PALIM_assynco_get_copro("5390") -> resout (isolation OK) ;
   PALIM_search_chunks NCG -> citation sans snippet (sourcage v1.9).
5. Smoke Delacour : PALIM_search_chunks copro_codes=["aa6-219-950"] (graphie avec
   tirets) -> resultats Vaneau ; PALIM_assynco_get_copro("ac9 872 896") -> Felix Faure.
6. Recoller Project Instructions NCG v3.0 + les 4 skills dans Claude Teams
   (clients/ncg/docs/INSTRUCTIONS_NCG_PROJECT.md — v3.0 requiert v10 : le Bloc 12
   reference le tool analytique). Verifier l'echo "v3.0 (2026-08-21)" en
   conversation neuve.
```
