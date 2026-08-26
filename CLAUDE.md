# PALIM — Livraison Delacour Patrimoine

> Branche `PALIM_Delacour_Patrimoine` : **branche orpheline** dédiée au client
> Delacour Patrimoine. Elle contient le code produit PALIM + la configuration
> Delacour, et **rien des autres clients** (ni config, ni runbooks, ni données).
> Créée le 26/08/2026 depuis l'état de `main` (5e482de), sans historique partagé :
> un `git clone --single-branch --branch PALIM_Delacour_Patrimoine` ne transfère
> aucun contenu d'un autre client.

## Règles pour toute session dans ce clone

- **Client actif** : `PALIM_CLIENT=delacour` sur toute commande pipeline, toujours
  exporté explicitement (le défaut du code est un autre client).
- **Données** : les shards vivent dans `C:\Users\thai-\palim-delacour`
  (= `project_root` de `Scripts/clients/delacour/client.json`), la source
  documentaire dans `G:\Drive partagés\Cabinet Delacour Patrimoine\Copropriétés`
  (= `raw_root`). Rien de tout ça n'entre dans ce repo.
- **Secrets** : jamais en clair. DB via Secrets Manager (`palim/delacour/ragadmin`
  côté pipeline ; user lecture seule côté MCP). `DB_PASSWORD` en variable d'env
  uniquement, jamais commité.
- **Windows** : préfixer tout Python de `PYTHONIOENCODING=utf-8` ; chemins
  accentués toujours entre guillemets doubles, copiés tels quels.
- **Git sur Google Drive** : GoogleDriveFS pose des locks. Avant chaque commande
  git : `taskkill //F //IM git.exe ; sleep 2 ; rm -f .git/index.lock`, et purger
  les `desktop.ini` qui apparaissent dans `.git/` (`find .git -name desktop.ini
  -delete`). Ne JAMAIS tuer GoogleDriveFS.exe.

## Synchronisation avec le produit

Cette branche est **orpheline** : pas de `git merge main` possible (historiques
sans lien). Pour récupérer une évolution produit depuis `main` :
`git checkout main -- <chemins produit>` puis commit ici, en vérifiant qu'aucun
fichier d'un autre client n'entre dans le périmètre. Toute purge de contenu
tiers doit être maintenue à chaque synchronisation.

## Ce qui a été retiré volontairement de cette branche

Configs et skills NCG/CSG (`Scripts/clients/ncg|csg`), runbooks et outils de
debug NCG/CSG (`ops/runbooks/ncg|csg`, `ops/tools/ncg`), bundle Streamlit NCG,
plans internes SmarterPlan (`Scripts/PLAN_*.md`), runbooks de déploiement MCP,
notes internes (`AGENTS.md`, `Résultats bruts/`), skill d'onboarding tenant.
Résidu assumé : le code produit mentionne des noms de clients dans quelques
constantes et commentaires (ex. garde-fou Assynco) — sans données associées.
