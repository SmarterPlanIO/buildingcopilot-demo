# RUNBOOK - Deploiement MCP PALIM image v9 (produit multi-client, 2 Lambdas)

> Date : 17/08/2026. A executer en AWS CloudShell (console, compte 046004768626,
> region eu-west-1, Docker preinstalle, creds heritees). Duree ~10 min.
>
> v8 -> v9 embarque (tout est deja merge sur main, dernier commit attendu 993094f) :
>   1. Sourcage v1.9 (54186c0) : verbatim = result.text, get_chunks = re-fetch hors
>      contexte, snippet retire de citation.
>   2. Multi-client (8fe4cce+) : ASSYNCO_SYNDIC_ALLOWLIST (retro-compat env
>      ASSYNCO_SYNDIC_NCG), PALIM_CLIENT, allowlist vide = FAIL-CLOSED.
>   3. Immatriculations RNIC (4b252a6) : canon() sur tous les params de tools
>      (graphies humaines "AE3-410-578" acceptees), resolution Assynco par
>      "Numero d'immatriculation" (repli Ref client pour codes numeriques NCG),
>      copro_id.py vendorise au build (build_and_push.sh v9 le fait).
>
> Deploiement de CODE PUR : AUCUN changement d'env.json, de secret ou d'IAM.
>   - palim-mcp (NCG) : pas de PALIM_CLIENT en env -> defaut "ncg" -> allowlist
>     NCG par defaut, comportement identique a v8.
>   - palim-delacour-mcp : env deja pose (PALIM_CLIENT=delacour + allowlists).
> Rollback : repointer l'image precedente (v8) via update-function-code.

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626
IMG="${ACC}.dkr.ecr.${REG}.amazonaws.com/palim-mcp:v9"

# 1. Recuperer main
if [ -d buildingcopilot-demo ]; then
  cd buildingcopilot-demo && git checkout main && git pull origin main
else
  git clone https://github.com/SmarterPlanIO/buildingcopilot-demo.git
  cd buildingcopilot-demo
fi
git log --oneline -1   # attendu : 993094f (ou plus recent)

# 2. Build + push (vendorise dossiers_api + rerank + copro_id, build amd64, push ECR)
bash Scripts/mcp_server/build_and_push.sh v9

# 3. Repointer les DEUX Lambdas sur v9
for FN in palim-mcp palim-delacour-mcp; do
  aws lambda update-function-code --region $REG --function-name $FN \
    --image-uri "$IMG" --query "LastUpdateStatus" --output text
done
for FN in palim-mcp palim-delacour-mcp; do
  aws lambda wait function-updated-v2 --region $REG --function-name $FN
  aws lambda get-function --region $REG --function-name $FN \
    --query "Code.ImageUri" --output text
done

echo "==== v9 DEPLOYE sur palim-mcp et palim-delacour-mcp ===="
```

## Post-deploy (fait par Claude depuis le poste, prevenez-le)

1. Smoke NCG : initialize + tools/list + PALIM_assynco_get_copro("5390") -> resout
   (allowlist defaut ncg), et une copro Delacour -> NOT_FOUND (isolation inchangee).
2. Smoke Delacour : PALIM_search_chunks copro_codes=["AE3-410-578"] (AVEC tirets,
   graphie humaine — c'est LE test v9) -> resultats Vaneau... (note : AE3410578 =
   100 Bd Victor Hugo, pas encore ingeree -> tester avec "aa6-219-950" Vaneau) ;
   PALIM_assynco_get_copro("ac9 872 896") -> 127 Felix Faure.
3. Verifier citation sans snippet (sourcage v1.9) sur un search_chunks NCG.
4. Recoller les Project Instructions v1.9 dans le projet Claude Teams NCG si pas
   deja fait (cf. clients/ncg/docs/INSTRUCTIONS_NCG_PROJECT.md).
