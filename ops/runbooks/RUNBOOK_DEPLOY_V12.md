# RUNBOOK — Déploiement image MCP PALIM v12 (fiche v2 « annuaire ») — **CLOS 01/09/2026**

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


---

## CLÔTURE — post-deploy exécuté le 01/09/2026 (tous verts)

| Contrôle | Résultat |
|---|---|
| Images | `palim-mcp` / `palim-delacour-mcp` / `palim-csg-mcp` = **v12**, State=Active, LastUpdateStatus=Successful |
| Fiche v2 NCG (`8050`) | `fiche_version="v2"`, `usage` présent, **aucun `narratif`**, 5 sections, assurance Assynco mergée |
| Pointeur suivi | `get_chunks("2b9ce2c29946")` (rés. 11, `rejetee`, confiance haute) → texte réel : double vote art. 25 puis 25-1, « **cette résolution est rejetée dans les conditions de majorité de l'article 25-1** ». Le détecteur avait retenu le DERNIER décompte et la proclamation finale : règle « le dernier gagne » validée sur un cas à deux votes |
| Repli v1 Delacour (`AE3913340`) | `fiche_version="v1"` + `avertissement` servi. Le narratif figé contient encore « l'AG précédente a approuvé les comptes 2023 » — **l'affirmation de l'incident est désormais livrée avec sa mise en garde**, en attendant le rollout Delacour |
| Régression analytique | `run_analytical_query` sur 3 copros GE → couverture `3/3 demandées` (dénominateur corrigé), facettes présentes, `trace_ref` OK |
| Régression Assynco | `assynco_get_copro("5390")` → LES TERRASSES DE TIVOLI, 2 polices, isolation tenant intacte |
| Recette | `tests/recette_fiche_v2.py` : 19/19 fiches, 1 463 pointeurs, **0 échec** ; `test_resolution_index.py` 22/22 |

**Reste après ce deploy** : recoller les Project Instructions (NCG v4.0, Delacour v1.2,
CSG v1.1) en bumpant la DATE au jour du recollage, puis rollout Delacour et CSG
(06a + 09b + 09 sur leur RDS, recette comme critère de sortie).
