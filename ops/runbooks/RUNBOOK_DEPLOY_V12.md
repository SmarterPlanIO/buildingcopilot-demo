# RUNBOOK — Déploiement image MCP PALIM v12 (fiche v2 « annuaire »)

> Date : 01/09/2026. À exécuter en **AWS CloudShell** (console, compte 046004768626,
> région eu-west-1, Docker préinstallé, creds héritées). Durée ~10 min.
>
> **Contenu : v11 + C4 (overview v2).** Le seul changement de code MCP depuis le build
> v11 est le commit `3b5231c` (`PALIM_overview.py` + docstring du tool). Tout le reste de
> l'image est identique à v11, déjà en production.
>
> **Déploiement de CODE PUR** : aucun changement d'env.json, de secret, d'IAM ni de schéma
> DB requis par le deploy lui-même.

## État constaté avant deploy (01/09, vérifié en CLI)

| Lambda | Tenant | Image actuelle | Après v12 |
|---|---|---|---|
| `palim-mcp` | NCG | v11 | **v12** |
| `palim-delacour-mcp` | Delacour | v11 | **v12** |
| `palim-csg-mcp` | CSG | **v10** (2 versions de retard) | **v12** |

## Pré-vol de sûreté (fait le 01/09, à ne pas refaire — trace)

Le risque d'un deploy multi-tenant est qu'un serveur récent interroge une colonne absente
de la base d'un tenant. Vérifié avant d'écrire ce runbook :

- **Repli `fiche_version=v1` validé quand la colonne `faits_v2` N'EXISTE PAS** (cas exact de
  Delacour et CSG) : `_fetch_fiche_v2` intercepte `UndefinedColumn`, `rollback()`, et le
  tool sert l'ancien narratif **avec son champ `avertissement`**. Aucun crash, dégradation
  explicite (jamais silencieuse).
- **Schéma CSG** : `chunks.retrieval_exclu`, `motif_exclusion`, `ref_source_file` présents
  → le code v11 (soft delete) est sûr chez elle malgré son retard en v10.
- Les colonnes pipeline absentes chez CSG (`nb_occurrences`, `profil_repetitif`, …) ne sont
  lues par **aucun** module du serveur MCP (vérifié par grep) : sans effet sur le deploy.
- La table `resolutions` n'est lue par **aucun** module MCP (C4 expose la fiche déjà
  calculée, pas les résolutions) : son absence chez Delacour/CSG est sans effet.

## Ce que change v12 pour l'utilisateur

`PALIM_copro_overview` renvoie désormais `fiche_version` :
- **NCG → `v2`** : l'ANNUAIRE (identité + pointeurs, chiffres calculés, dossiers chauds,
  questions clés, PV récents). **Le narratif généré disparaît** — c'est l'objet du
  chantier (incident du 27/08 : une fiche affirmait l'approbation de comptes rejetés).
- **Delacour et CSG → `v1`** : ancien narratif, servi avec `avertissement` (statut de
  source le plus bas) tant que leur rollout n'est pas fait.

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626
IMG="${ACC}.dkr.ecr.${REG}.amazonaws.com/palim-mcp:v12"

# 1. Récupérer main
if [ -d buildingcopilot-demo ]; then
  cd buildingcopilot-demo && git checkout main && git pull origin main
else
  git clone https://github.com/SmarterPlanIO/buildingcopilot-demo.git
  cd buildingcopilot-demo
fi
git log --oneline -1
grep "mcp\[cli\]==1.27.2" Scripts/mcp_server/requirements.txt   # GARDE-FOU incident v9

# 2. Build + push (vendorise dossiers_api + rerank + copro_id, amd64, push ECR)
bash Scripts/mcp_server/build_and_push.sh v12

# 3. Repointer les TROIS Lambdas
for FN in palim-mcp palim-delacour-mcp palim-csg-mcp; do
  aws lambda update-function-code --region $REG --function-name $FN \
    --image-uri "$IMG" --query "LastUpdateStatus" --output text
done
for FN in palim-mcp palim-delacour-mcp palim-csg-mcp; do
  aws lambda wait function-updated-v2 --region $REG --function-name $FN
  aws lambda get-function --region $REG --function-name $FN \
    --query "Code.ImageUri" --output text
done

echo "==== v12 DEPLOYE sur palim-mcp, palim-delacour-mcp, palim-csg-mcp ===="
```

## Rollback

`update-function-code --image-uri ...:v11` sur `palim-mcp` et `palim-delacour-mcp`,
`...:v10` sur `palim-csg-mcp`. Les colonnes `faits_v2` restent en base sans être lues :
le rollback est complet et sans perte.

## Post-deploy (fait par Claude depuis le poste — prévenez-le)

1. **Smoke boot** (le test de l'incident v9) : `initialize` + `tools/list` sur les trois
   URLs → 13 tools, dont `PALIM_run_analytical_query`.
2. **Fiche v2 NCG** : `PALIM_copro_overview("8050")` → `fiche_version="v2"`, champ `usage`
   présent, **aucun `narratif`**, 5 sections, pointeurs exploitables ; enchaîner un
   `PALIM_get_chunks` sur un `chunk_id` de la fiche pour prouver que les pointeurs vivent.
3. **Repli v1** : `PALIM_copro_overview` sur une copro Delacour → `fiche_version="v1"` +
   champ `avertissement` présent (et pas d'erreur).
4. **Régression** : `PALIM_search_chunks` scopé NCG (citation sans snippet),
   `PALIM_assynco_get_copro("5390")` (isolation tenant), une requête analytique.
5. **Recette complète** : `python tests/recette_fiche_v2.py` (NCG) doit rester verte.
6. Puis **recoller les Project Instructions** NCG v4.0 / Delacour v1.2 / CSG v1.1 en
   **bumpant la date au jour du recollage**, et vérifier l'écho de version en conversation
   neuve pour chacun.
