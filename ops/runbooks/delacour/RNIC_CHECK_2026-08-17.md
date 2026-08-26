# P0 — Validation RNIC des 25 copropriétés Delacour (17/08/2026)

> Croisement : immatriculations Assynco (champ `Numéro d'immatriculation`) × Registre national
> d'Immatriculation des Copropriétés (dataset ANAH `62da71c068871f4c54258c7c`, ressource
> "Actualisation quotidienne", API tabulaire data.gouv). Vérifications par copro : existence au
> registre, code postal vs dossier Drive, syndic déclaré = Delacour Patrimoine, mandat en cours.
> Le registre stocke la forme canonique SANS tirets (`AE3410578`) — requête avec tirets = 0 résultat.

**Résultat : 24/25 GO.** Ces immatriculations sont adoptables comme identifiants canoniques PALIM (plan `PLAN_IMMATRICULATION_RNIC.md`, phase P1+).

| Immatriculation | Copro (Assynco) | Dossier Drive | Lots | Immatriculée le | MAJ registre | Verdict | Écarts |
|---|---|---|---|---|---|---|---|
| `AE3410578` | 100 BD VICTOR HUGO | SDC 100 Boulevard Victor Hugo - 92200 | 43 | 2019-01-29 | 2026-07-11 | **GO** | - |
| `AE8711459` | 105 AVENUE DE VERDUN | 105 Avenue de Verdun - 92130 | 145 | 2019-03-08 | 2026-07-11 | **A VERIFIER** | syndic RNIC = ?; mandat: Mandat expiré avec successeur déclaré |
| `AC9872896` | 127 AVENUE FELIX FAURE | 127 Avenue Felix Faure - 92000 | 121 | 2018-07-30 | 2025-12-13 | **GO** | - |
| `AE5618780` | 130 RUE D'ABOUKIR | 130 Rue d'Aboukir - 75002 | 32 | 2019-02-11 | 2025-08-24 | **GO** | - |
| `AD5587183` | 19 RUE MONTMARTRE | 19 Rue Montmartre - 75001 | 35 | 2018-10-23 | 2026-06-27 | **GO** | - |
| `AC4966461` | 20 RUE PERIER | SDC 20 rue Périer - 92120 | 41 | 2018-05-04 | 2026-07-11 | **GO** | - |
| `AC5645007` | 208 RUE DE LA CROIX NIVERT | SDC 208 RUE DE LA CROIX NIVERT - 75015 | 38 | 2018-05-24 | 2026-03-14 | **GO** | - |
| `AB7612138` | 33 TER RUE DE PARIS | SDC 33 ter rue de paris - 92190 | 30 | 2017-12-20 | 2026-07-05 | **GO** | - |
| `AB9195306` | 35 RUE LAZARE CARNOT | SDC 35 rue Lazare Carnot - 92140 | 35 | 2018-01-10 | 2026-05-02 | **GO** | - |
| `AF5773049` | 45 BOULEVARD DU LYCEE | SDC 45 Boulevard du Lycée - 92170 | 17 | 2019-10-04 | 2026-07-11 | **GO** | - |
| `AA8054405` | 48-50 RUE DE SEVRES | SDC 48-50 Rue de Sèvres - 92100 | 83 | 2017-08-02 | 2025-10-04 | **GO** | - |
| `AA6219950` | 50 RUE VANEAU | SDC 50 rue Vaneau - 75007 | 54 | 2017-07-04 | 2026-05-02 | **GO** | - |
| `AE4011581` | 6/6 BIS RUE DE LA BRIQUETERIE | 6 6Bis rue de la briqueterie - 75014 | 58 | 2019-02-04 | 2025-08-24 | **GO** | - |
| `AC8694200` | 60 BOULEVARD MAGENTA | 60 Boulevard Magenta - 75010 | 85 | 2018-07-19 | 2025-08-23 | **GO** | - |
| `AH7171655` | 67 RUE ESCUDIER | SDC - 92100 | 58 | 2022-08-25 | 2026-07-12 | **GO** | - |
| `AA8321549` | 68 RUE DIDOT | 68 RUE DIDOT - 75014 | 103 | 2017-11-15 | 2026-03-14 | **GO** | - |
| `AB9687013` | 71 RUE GUYNEMER | SDC 71 rue Guynemer - 92130 | 32 | 2018-01-17 | 2026-07-05 | **GO** | - |
| `AD2661718` | 74 AVENUE DE L'AGENT SARRE | 74 avenue de l’agent Sarre - 92700 | 30 | 2018-09-10 | 2025-10-25 | **GO** | - |
| `AB8546467` | 79-81 BOULEVARD EXELMANS | SDC 79-81 BOULEVARD EXELMANS - 75016 | 183 | 2018-01-03 | 2026-05-30 | **GO** | - |
| `AE1302603` | 86 AVENUE DE VERSAILLES | SDC 86 avenue de Versailles 92500 Rueil  | 30 | 2018-12-20 | 2026-03-14 | **GO** | - |
| `AJ6978050` | 9 RUE EDMOND NOCARD | SDC 9 rue Edmond Nocard - 94410 | 22 | 2026-04-10 | 2026-04-10 | **GO** | - |
| `AA8785875` | 94 BOULEVARD VICTOR HUGO | SDC 94 Boulevard Victor Hugo - 92200 | 37 | 2017-08-11 | 2026-07-04 | **GO** | - |
| `AE3913340` | 99 AVENUE DE VERDUN | SDC 99 Avenue de Verdun - 92130 | 54 | 2019-02-04 | 2025-08-24 | **GO** | - |
| `AF8745119` | RESIDENCE RUE DES VILLAS | 4 rue des Villas - 94800 Villejuif | 25 | 2020-01-22 | 2025-08-24 | **GO** | - |
| `AC0390328` | SDC 1 RUE DE CRILLON | SDC 1 rue de crillon - 92210 | 54 | 2018-02-01 | 2026-08-01 | **GO** | - |

## Cas à vérifier

- **`AE8711459` — 105 Avenue de Verdun (145 lots)** : le registre indique "Mandat expiré avec
  successeur déclaré" et ne publie pas la raison sociale du représentant légal. Cohérent avec une
  reprise récente par Delacour dont la déclaration au registre n'est pas finalisée (cf. l'étape
  "Rattachement au Registre" du onboarding Lobby). **Action : demander à Delacour de finaliser la
  mise à jour du registre.** N'empêche PAS l'adoption de l'immatriculation comme identifiant
  (l'identité de la copro est certaine, croisée Assynco + Lobby + adresse).

## Bonus collectés (utilisables pour `copro_synthese`)

Le RNIC fournit par copro : nombre total de lots, type de syndic, SIRET du représentant,
dates d'immatriculation / dernière MAJ / fin de mandat, adresse de référence géocodée.
Détail complet dans le JSON d'exécution (non versionné) ; requêtable à la demande via
`https://tabular-api.data.gouv.fr/api/resources/3ea8e2c3-0038-464a-b17e-cd5c91f65ce2/data/?numero_immatriculation__exact=<IMMAT>`.

## Hors périmètre des 25 (pour mémoire)

Assynco référence aussi : 114 Croix Nivert `AC3263555`, 2/2bis Coubertin `AB8763831`, 21bis Pasteur
`AC8312977`, 40 Blomet `AC1168715`, 45 Alma `AD1265248` (dossiers Drive hors liste ou vides), et
23 quai de Grenelle (immatriculation VIDE côté Assynco).