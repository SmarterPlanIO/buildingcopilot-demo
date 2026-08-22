---
name: palim-onboarding-tenant
description: >
  Onboarding d'un nouveau client syndic sur PALIM (nouveau tenant) : profil client, RDS dediee,
  secrets, role IAM, Lambda MCP, ingestion documentaire, app Streamlit, recette. A utiliser quand
  Thai dit "nouveau syndic", "nouveau client", "nouveau cabinet", "3e/4e tenant", "decliner PALIM
  pour X", "onboarder X", "provisionner la base de X", ou demande de creer une RDS / une Lambda MCP
  / un profil clients/<x>/client.json. A utiliser AUSSI en cours d'onboarding quand un symptome
  connu apparait (403 sur la Function URL, Runtime.ExitError au boot de la Lambda, raccourci Drive
  .lnk illisible, Streamlit qui redirige vers une autre app, ecriture Airtable refusee, copro
  Assynco introuvable par son code). NE PAS utiliser pour ajouter une copro a un client existant :
  c'est `Scripts/add_copro.py` (puis `ingest.py --copro`).
---

# Onboarding d'un nouveau tenant PALIM

Deux cas a ne pas confondre :

| Besoin | Outil | Duree |
|---|---|---|
| Ajouter une copro a un client existant | `python add_copro.py --immat ... --folder ...` puis `ingest.py --copro` | 10 min + ingestion |
| Onboarder un nouveau syndic | ce skill | ~1 h de mise en place + ingestion |

Compte AWS unique : **046004768626**, region **eu-west-1** (rerank en eu-central-1). Image ECR de
prod : **palim-mcp:v10**.

## 1. Principes non negociables

- **Une DB par tenant.** Jamais deux syndics dans la meme base copros : les tools MCP filtrent par
  code copro, pas par syndic. Une RDS dediee, un role lecteur dedie, des secrets `palim/<client>/*`
  dedies, une Lambda et un slug d'URL dedies.
- **Identifiant canonique = immatriculation RNIC** (format `AA0000000`, arrete du 10/10/2016, sans
  tirets). Les codes internes du syndic deviennent des `lobby_code` (alias de resolution). Les
  clients historiques a codes numeriques (NCG) restent en l'etat.
- **Allowlist Assynco fail-closed.** `assynco.syndic_labels` du profil doit contenir le libelle
  Organisation Airtable **exact**. Vide = aucun acces Assynco. Ne jamais elargir "au cas ou" : un
  libelle voisin peut appartenir a un autre cabinet (piege reel : "SOCIETE MARIE SAINT GERMAIN"
  n'est pas "CABINET SAINT GERMAIN").
- **Le MCP ne fait que lire.** Le pipeline ecrit avec `ragadmin`, le MCP lit avec
  `mcp_<client>_reader`. A prouver en recette, pas a supposer.

## 2. Sequence

Le partage des roles vient d'une contrainte IAM : le user local (Bastien_Kovac) n'a pas les droits
CreateSecret / IAM / Lambda. Tout ce qui les exige passe par un runbook que **Thai** colle dans
CloudShell ; le reste se fait depuis le poste.

1. **Cadrage** (Claude). Perimetre (combien de copros), source documentaire, immatriculations RNIC,
   libelle syndic dans Airtable. Verifier chaque immatriculation au registre :
   `https://tabular-api.data.gouv.fr/api/resources/3ea8e2c3-0038-464a-b17e-cd5c91f65ce2/data/?numero_immatriculation__exact=<IMMAT>`
   (forme sans tirets obligatoire). Modele de rapport : `ops/runbooks/delacour/RNIC_CHECK_2026-08-17.md`.
2. **Profil client** (Claude). Creer `clients/<code>/client.json` en partant de `clients/csg/client.json`
   (mono-copro, le plus lisible) ou `clients/delacour/client.json` (multi-copro, riche). Valider par
   `PALIM_CLIENT=<code> python -c "import pipeline_config as p; print(p.INCLUDED_COPROS, p.resolve('<alias>'))"`.
3. **RDS** (Claude, le user local a RDSFullAccess). Cloner les parametres de l'existant :
   `db.t4g.micro`, PG 17.9, 20 Go gp3, public, SG `sg-e59223af`, chiffree, deletion protection,
   backups 7 j. Mot de passe master temporaire genere localement et **volontairement perdu** : le
   runbook le reinitialise vers le secret.
4. **Runbook CloudShell** (Claude ecrit, Thai execute). Decliner
   `ops/runbooks/csg/RUNBOOK_PROVISION_CSG.md` (le plus a jour). Il cree les secrets, reinitialise le
   mot de passe master, cree la policy et le role IAM, la Lambda sur l'image v10, la Function URL et
   le slug. Thai renvoie l'URL MCP (a garder secrete).
5. **Schema et role lecteur** (Claude). `PALIM_CLIENT=<code> DB_PASSWORD=<secret ragadmin> python 06a_init_db.py`,
   puis `CREATE ROLE mcp_<code>_reader` avec le mot de passe du secret, `GRANT CONNECT/USAGE/SELECT`
   et `ALTER DEFAULT PRIVILEGES`. Prouver le read-only (SELECT ok, INSERT refuse).
6. **Ingestion** (Claude). `add_copro.py` pour chaque copro, puis `ingest.py --copro <code> --keep-shards`.
   Les etapes 01 a 05b ne touchent pas la base : elles peuvent tourner avant meme le runbook.
7. **Recette** (section 4) puis **livrables client** : Project Instructions depuis
   `clients/INSTRUCTIONS_TEMPLATE_PALIM.md`, skills dans `clients/<code>/skills/`, app Streamlit si
   besoin d'un harness de demo.

## 3. Pieges connus

Chacun a coute une session ou un incident de prod. Les relire avant, pas apres.

**AWS / Lambda**
- Function URL en mode streaming : `lambda:InvokeFunctionUrl` ne suffit pas, il faut **aussi**
  `lambda:InvokeFunction` en resource policy, sinon 403 au front door (constate 14/08/2026 sur Delacour).
- `mcp[cli]` doit rester **epingle** (`==1.27.2`). Un build non epingle a resolu mcp 2.x, qui supprime
  `mcp.server.fastmcp` : `Runtime.ExitError` au boot, **les deux Lambdas de prod cassees** (incident v9,
  18/08/2026). Toujours smoke-tester apres un build, et connaitre la commande de rollback.
- `secretsmanager:GetRandomPassword` est refuse au user local : generer le mot de passe temporaire en
  Python (`secrets.token_urlsafe`) cote poste, ou laisser le runbook le faire dans CloudShell.
- `stateless_http=True` est obligatoire en Lambda.

**Sources documentaires**
- Un dossier Drive "partage avec moi" n'est pas monte sur `G:\`. Le raccourci cree par Thai apparait
  comme un fichier `.lnk` **non traversable** : la vraie cible est
  `G:\.shortcut-targets-by-id\<id du dossier>\`. C'est ce chemin qui va dans `raw_root`.
- Les fichiers Google natifs (`.gsheet`, `.gdoc`) sont illisibles hors API Drive et exclus par l'etape 01.
  Si le client stocke sa comptabilite en Sheets, le dire tot : ce contenu ne sera pas dans le RAG.
- `copy2` / `CopyFile2` casse sur GoogleDriveFS (repli flux deja code dans 01).

**Airtable / Assynco**
- Le PAT `palim/airtable_pat` est en **lecture seule** : toute ecriture (remplir une immatriculation)
  passe par le connecteur Airtable de la session Claude, pas par l'API avec ce PAT. L'API meta
  (`/v0/meta/bases/...`) lui est aussi interdite.
- Le champ qui compte pour la resolution MCP est **`Numéro d'immatriculation`** (`fldwOlxmv7peMakJt`),
  pas `Ref client`. Les fiches y stockent la forme canonique sans tirets. Le champ `id`
  (`fld2I7AGlgdORVJHg`) sert seulement a construire l'URL du registre : le remplir ne suffit pas.
- `FIND()` est sensible aux accents : chercher `LACEP` ne trouve pas `LACÉPÈDE`.
- Les tools MCP resolvent par immatriculation si le code en est une, sinon par `Ref client`
  (retro-compat NCG). Une copro dont aucun de ces deux champs n'est rempli est introuvable.

**Streamlit Cloud** (harness interne uniquement)
- Une seule app par triplet (repo, branche, fichier) : pour un 2e tenant il faut un **wrapper racine
  dedie** (modele : `streamlit_app_csg.py`), sinon Streamlit redirige vers l'app existante.
- Ne jamais pointer le main file path sur `Scripts/Streamlit Cloud/...` : l'espace casse l'installeur
  de dependances (il tente d'installer `Cloud/requirements.txt`). Le wrapper racine existe pour ca.
- Coller les secrets dans **Advanced settings avant** de deployer. Une app deployee sans secrets peut
  rester fantome (`not_found` au clic) : la supprimer et la recreer.
- Le tenant est determine **uniquement** par la section `[db]` des secrets. Logo via `[branding].logo_file`
  (aucun logo par defaut).

**Divers**
- `PALIM_search_chunks` prend `copro_codes`, pas `copros`.
- `08_airtable_sync.py` est saute par `ingest.py` s'il n'y a pas de donnees Airtable pour la copro :
  les dossiers sinistres de l'ERP ne deviennent pas des dossiers virtuels dans le RAG (ils restent
  interrogeables en live par les tools Assynco). A traiter si le client veut le croisement.
- Google Drive seme des `desktop.ini` jusque dans `.git/refs`, ce qui casse `git log --all`
  ("bad object refs/desktop.ini") : `find .git/refs -name desktop.ini -delete`.

## 4. Recette (a passer avant d'annoncer que c'est en place)

1. `mcp_<client>_reader` : SELECT ok, INSERT **refuse** (`InsufficientPrivilege`).
2. MCP `initialize` repond, `tools/list` renvoie les 12 tools.
3. `PALIM_list_copros` : les copros attendues, avec leurs volumes.
4. `PALIM_search_chunks` avec `copro_codes` : le texte retourne vient bien de ce client.
5. **Isolation dans les deux sens** : une copro du nouveau tenant resout ; une copro d'un autre
   tenant (ex. `5390` pour NCG) renvoie `NOT_FOUND`.
6. `PALIM_assynco_get_copro` avec plusieurs graphies du code (`ab0-835-843`, `AB0835843`).
7. `add_copro.py --verify` pour chaque copro ingeree (chunks, documents, dossiers, fiche 09, registre).

## 5. Restes mono-client assumes

A connaitre pour ne pas les prendre pour des bugs : la colonne et le parametre s'appellent toujours
`code_ncg` (renommage juge trop invasif), le defaut `DB_USER` de `PALIM_config.py` est
`mcp_ncg_reader` (surchargeable par env), et les scripts `debug_*` / `diag_*` restent orientes NCG.
