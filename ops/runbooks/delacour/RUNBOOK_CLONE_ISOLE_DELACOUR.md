# Clone isolé Delacour depuis `main` — remplace la branche orpheline `PALIM_Delacour_Patrimoine`

> 23/09/2026. Objectif conservé de la branche (cf. son CLAUDE.md) : un dossier de travail
> qui contient le produit PALIM + la configuration Delacour **et rien des autres clients**
> (ni config, ni runbooks, ni outils de debug, ni plans internes), pour les sessions
> Claude / humaines menées dans le contexte Delacour.
> Moyen retenu : **clone partiel + sparse-checkout de `main`**, au lieu d'une branche à
> historique séparé. Même isolation de l'arbre de travail, zéro report manuel : `git pull`
> suffit pour suivre le produit.

## 1. Créer le clone (une fois, hors Google Drive)

```powershell
git clone --filter=blob:none --sparse --branch main https://github.com/SmarterPlanIO/buildingcopilot-demo.git C:\Users\thai-\palim-delacour-repo
Set-Location C:\Users\thai-\palim-delacour-repo
git sparse-checkout set --no-cone '/*' `
  '!/Scripts/clients/ncg/' '!/Scripts/clients/csg/' `
  '!/ops/tools/ncg/' '!/ops/runbooks/ncg/' '!/ops/runbooks/csg/' `
  '!/Scripts/PLAN_*.md' '!/Résultats bruts/' '!/AGENTS.md' '!/streamlit_app_csg.py' `
  '!/Scripts/mcp_server/RUNBOOK_DEPLOY_V*.md' '!/ops/runbooks/RUNBOOK_DEPLOY_V*.md' `
  '!/.claude/skills/palim-onboarding-tenant/' '!/Scripts/Streamlit Cloud/' '!/Scripts/mcp_server/skills_bundle/ncg/'
git sparse-checkout list
```

- `--filter=blob:none` (partial clone, supporté par GitHub) : les contenus des fichiers
  exclus ne sont **jamais téléchargés** — pas seulement masqués. L'historique (commits,
  arborescences) l'est, comme pour la branche orpheline qui, elle aussi, vivait dans le
  même dépôt.
- Les motifs `--no-cone` sont ceux de la purge documentée dans la branche ; ajuster la
  liste si un nouveau client ou un nouveau dossier interne apparaît sur `main`.
- Vérifié le 23/09 sur un dépôt témoin : l'arbre obtenu ne contient que
  `Scripts/` (hors `clients/ncg|csg`, hors `PLAN_*`), `ops/handoffs`, `ops/runbooks/delacour`,
  `CLAUDE.md`, `Scripts/clients/INSTRUCTIONS_TEMPLATE_PALIM.md`, `Scripts/clients/delacour/`.

## 2. Suivre le produit

```powershell
git pull            # c'est tout : plus de checkout main -- <chemins> ni de format-patch/am
```

Un correctif fait dans ce clone se commite sur une branche courte et se pousse vers
`main` comme n'importe quel autre ; les motifs sparse n'empêchent pas de committer.

## 3. Livrer un bundle à un tiers (si un jour nécessaire)

```powershell
git archive --format=zip -o palim-delacour-<tag>.zip <tag> -- . ":(exclude)Scripts/clients/ncg" ":(exclude)Scripts/clients/csg" ":(exclude)ops/tools/ncg" ":(exclude)ops/runbooks/ncg" ":(exclude)ops/runbooks/csg" ":(exclude)Scripts/PLAN_*.md" ":(exclude)Résultats bruts" ":(exclude)AGENTS.md" ":(exclude)streamlit_app_csg.py"
```

Sans `.git`, sans historique : c'est le seul format qui garantit qu'aucun objet d'un
autre client ne part.

## 4. Retrait de la branche orpheline (après §1 en place)

```powershell
git tag archive/PALIM_Delacour_Patrimoine origin/PALIM_Delacour_Patrimoine
git push origin archive/PALIM_Delacour_Patrimoine
git push origin --delete PALIM_Delacour_Patrimoine
```

Ce qu'elle portait d'unique est déjà sur `main` : `clients/delacour/docs/INSTRUCTIONS_DELACOUR_PROJECT.txt`
(commit `d368aa1`, 22/09). Le reste (CLAUDE.md abrégé, `visites_3d.txt` sans entrée NCG)
est du dérivé. `02_extraction_optimized.py` y était en retard sur `main` (sans le
correctif antiword du 10/09).

## 5. Le dossier Drive `Prospects/Delacour Patrimoine/202608 PALIM Delacour Patrimoine`

Checkout de la branche daté du 26/08 : à archiver (ou supprimer) une fois le clone §1
créé. Ne plus y travailler.
