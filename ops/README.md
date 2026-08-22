# ops/ — Interne SmarterPlan, jamais livré

Tout ce qui se trouve ici est de l'exploitation SmarterPlan : provisioning d'infrastructure,
onboarding d'un nouveau syndic, déploiement, contrôles internes. **Rien de ce dossier ne part chez
un client**, ni par mail, ni par partage de dossier, ni en pièce jointe.

Raison d'être : ces documents contiennent le numéro de compte AWS, les noms de policies et de rôles
IAM, la mécanique des slugs d'URL MCP, les identifiants de secrets et les endpoints RDS. Ils étaient
auparavant rangés dans `Scripts/clients/<client>/docs/`, à côté des fichiers qu'on livre
effectivement au client (Project Instructions, skills), avec le risque qu'un partage du dossier
emporte l'interne avec.

## Règle de classement

| Emplacement | Contenu | Livré au client ? |
|---|---|---|
| `ops/` | runbooks infra, onboarding tenant, déploiement, contrôles | **Non, jamais** |
| `.claude/skills/` | skills Claude Code d'équipe (dont `palim-onboarding-tenant`) | Non (outillage interne) |
| `Scripts/*.py`, `Scripts/mcp_server/` | code produit, backend MCP | Non (hébergé, pas remis) |
| `Scripts/clients/<c>/client.json` | profil technique du tenant | Non |
| `Scripts/clients/<c>/docs/INSTRUCTIONS_*.md` | Project Instructions | **Oui**, collées dans le Claude Teams du client |
| `Scripts/clients/<c>/skills/` | skills métier du client | **Oui**, chargées dans son Claude Teams |
| `Scripts/mcp_server/skills/assynco-erp/` | skill produit ERP (courtier) | **Oui** |

En clair : dans `Scripts/clients/<c>/`, seuls `docs/INSTRUCTIONS_*.md` et `skills/` sont
communicables. Le reste (profil, outils de debug) est interne.

## Contenu

- `runbooks/<client>/` — provisioning et déploiement par tenant :
  - `csg/RUNBOOK_PROVISION_CSG.md` (le plus à jour, à décliner pour un nouveau syndic)
  - `delacour/RUNBOOK_PROVISION_DELACOUR.md` et `delacour/RNIC_CHECK_2026-08-17.md`
  - `ncg/RUNBOOK_DEPLOY_V7.md`

## Onboarding d'un nouveau syndic

La procédure complète (principes d'isolation, séquence poste/CloudShell, pièges connus, recette) est
dans le skill **`palim-onboarding-tenant`**, versionné sous `.claude/skills/` à la racine du repo.

Il n'est pas dans `ops/` pour une raison technique : Claude Code ne découvre les skills que dans un
dossier `.claude/skills/`. C'est le seul emplacement qui les rend invocables par nom. Le contenu
reste strictement interne, `.claude/` n'étant jamais un support de livraison.

Outillage associé, dans le code produit parce qu'il sert à tous les clients et importe
`pipeline_config` : `Scripts/add_copro.py` (pré-vol et recette d'une copro) et `Scripts/ingest.py`
(ingestion de bout en bout).
