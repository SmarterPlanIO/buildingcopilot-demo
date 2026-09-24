# RUNBOOK — Déploiement image MCP PALIM v13 (correctifs du relevé Delacour du 21/09/2026)

> Rédigé le 24/09/2026. À exécuter en **AWS CloudShell** (console, compte 046004768626,
> eu-west-1) : palim-louise n'a pas de moteur de conteneur, le build doit se faire ailleurs.
> **Déploiement de CODE PUR** : aucun changement d'`env.json`, de secret, d'IAM ni de schéma.
> Code de v13 = commit `93a8e71` sur `main`.

## Ce que change v13 pour l'utilisateur

| Symptôme relevé le 21/09 | v12 | v13 |
|---|---|---|
| Une recherche d'annuaire sur un immeuble non indexé renvoyait les 25 (30) copropriétés | liste complète | **liste vide** + avertissement nommant le nombre de copros indexées |
| Les citations affichaient « SDC - 92100 » (nom du dossier Drive) | `chunks.copropriete` | **`copros.nom_residence`** (repli sur l'ancien si le registre est muet) |
| « Erreur interne » ponctuelle sur un appel Assynco (3 en 21 jours) | `TimeoutError` brut | **2 reprises** avec backoff sur timeout/429/5xx ; aucune reprise sur erreur définitive |

Les deux premiers points du relevé (libellé d'Escudier, immatriculation de Nocard) étaient
côté **données** : corrigés le 22/09 par le rerun 06b, déjà en production.

## Pré-vol

```bash
# rien à faire côté base : v13 lit des colonnes qui existent depuis 06a (copros.nom_residence)
# garde-fou incident v9 : la version de mcp[cli] ne doit pas bouger
grep "mcp\[cli\]==1.27.2" Scripts/mcp_server/requirements.txt
```

Un point à connaître avant de déployer : v13 fait une jointure supplémentaire
(`LEFT JOIN copros`) dans les trois requêtes de recherche. Le coût est nul en pratique
(table de 30 lignes, jointure sur clé primaire), mais c'est la seule modification du plan
SQL — si un ralentissement apparaissait, c'est là qu'il faudrait regarder.

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626
IMG="${ACC}.dkr.ecr.${REG}.amazonaws.com/palim-mcp:v13"

# 1. Récupérer main
if [ -d buildingcopilot-demo ]; then
  cd buildingcopilot-demo && git checkout main && git pull origin main
else
  git clone https://github.com/SmarterPlanIO/buildingcopilot-demo.git
  cd buildingcopilot-demo
fi
git log --oneline -1                                            # attendu : 93a8e71 ou plus récent
grep "mcp\[cli\]==1.27.2" Scripts/mcp_server/requirements.txt   # GARDE-FOU incident v9

# 2. Build + push
bash Scripts/mcp_server/build_and_push.sh v13

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

echo "==== v13 DEPLOYE sur palim-mcp, palim-delacour-mcp, palim-csg-mcp ===="
```

## Rollback

`update-function-code --image-uri ...:v12` sur les trois Lambdas. v13 ne touche ni au
schéma ni aux données : le rollback est complet et instantané.

## Post-deploy (par Claude depuis le poste — prévenez-le)

1. **Smoke boot** (test de l'incident v9) : `initialize` + `tools/list` sur les trois URLs
   → 13 tools.
2. **v13-1** : `PALIM_list_copros("Escudier")` → 1 résultat ;
   `PALIM_list_copros("Neuilly-Plaisance")` → **`copros: []`, `n_results: 0`**, un
   `warnings` citant « 30 copropriétés sont indexées » ; `PALIM_list_copros()` → 30.
3. **v13-2** : `PALIM_search_chunks` scopé `AH7171655`, `doc_type="PV_AG"` → la citation
   porte `copro = "SDC 67 rue Escudier, 92100 Boulogne-Billancourt"` (et non « SDC - 92100 »).
   Enchaîner `PALIM_get_chunks` sur ce `chunk_id` : même nom.
4. **v13-3** : `PALIM_assynco_get_copro("AA6219950")` et
   `PALIM_assynco_search_sinistres("AE3410578")` répondent (le retry est transparent ;
   il ne se vérifie qu'à l'usage, sur la durée).
5. **Régression NCG** : `PALIM_list_copros("Tariel")` → 1 résultat ;
   `PALIM_copro_overview("8050")` → `fiche_version="v2"` ; une requête analytique.
   **Attention** : NCG a des copros dont le registre n'a pas de `nom_residence` (leur
   annuaire cherche par code) — le repli `COALESCE` doit les afficher comme avant.
6. **Recette** : `python tests/recette_fiche_v2.py` sur NCG et Delacour, et
   `python mcp_server/tests/test_palim_v13.py` (sans DB ni réseau).

## Ce que v13 ne corrige pas

- Les Google Docs (`.gdoc`) du Drive restent hors index → chantier 06D.
- La mise à jour automatique de la base depuis le Drive → chantier 06E.
