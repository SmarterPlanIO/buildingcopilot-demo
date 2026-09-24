# 06E — Mise à jour périodique de la base depuis le Drive, sans copie persistante

> Conception du 24/09/2026. Répond à deux demandes : la promesse faite au client d'une
> **mise à jour automatique** (ajouts, modifications, suppressions), et l'exigence de **ne
> pas conserver de copie des documents** sur nos serveurs.
> Les deux se résolvent par la même idée : séparer l'**inventaire** (la liste des fichiers)
> du **contenu** (les fichiers eux-mêmes).

## 1. Ce qui bloque aujourd'hui

`ingest.py` gère déjà les ajouts et les suppressions, mais :

| Limite | Conséquence |
|---|---|
| l'étape 01 reconstruit `Archives_Filtrees` en parcourant une **copie locale complète** du Drive | il faut conserver 15 Go de documents client entre deux exécutions |
| la détection des suppressions compare la base à cette copie locale | une copie partielle ferait supprimer des documents valides |
| le checkpoint de l'étape 02 identifie un fichier par **taille + date**, pas par contenu | un document corrigé et redéposé **sous le même nom** n'est pas ré-extrait (limite V1 documentée dans le driver) |
| rien n'est planifié | chaque exécution est manuelle |

## 2. Le fait qui débloque tout

**À partir de l'étape 03, aucun script ne lit les documents bruts** — seulement les JSON de
texte produits par 02 (vérifié le 24/09 : 03 lit `Archives_Extraites`, 04/05/05b/05c ne
référencent jamais la source). Donc, une fois l'extraction faite, le document peut être
effacé sans rien perdre.

Et le pipeline n'a besoin du **contenu** que des fichiers nouveaux ou modifiés ; de tous les
autres, il ne lui faut que le **nom**, pour savoir qu'ils existent encore.

## 3. Conception

Un orchestrateur par copropriété, sans modifier 01 ni 02 :

1. **Inventaire distant** : `rclone lsjson --recursive` sur le dossier de la copro
   (chemin, taille, date, `md5Checksum` quand Drive le fournit). Aucun téléchargement.
2. **Delta** : comparaison avec l'inventaire de la veille
   (`per_copro/<code>/drive_manifest.json`) →
   - *nouveaux / modifiés* (taille, date ou empreinte différente),
   - *supprimés* (présents hier, absents aujourd'hui).
3. **Téléchargement du seul delta** : `rclone copy --files-from` dans un dossier temporaire.
4. **01 + 00b + 02** sur ce dossier partiel : seuls les fichiers du delta sont filtrés puis
   extraits ; leurs JSON rejoignent `Archives_Extraites`.
5. **Propagation des suppressions** : retrait des JSON des fichiers disparus de l'inventaire
   (même mécanique que `reconcile_extraits.py`, mais pilotée par l'inventaire et non par un
   arbre local).
6. **03 → 06b → 09b → 09** : inchangés, ils travaillent sur `Archives_Extraites` et les
   shards. 06b propage ajouts et suppressions en base.
7. **Purge** : suppression du dossier temporaire et du dossier filtré.
8. **Enregistrement** du nouvel inventaire.

Empreinte persistante : **aucun document client**. Seuls demeurent l'index
(`Archives_Extraites` + `per_copro` + la base) et l'inventaire, qui ne contient que des noms
de fichiers.

## 4. Détection des modifications sur place

L'inventaire porte le `md5Checksum` fourni par l'API Drive. Un document corrigé et redéposé
sous le même nom change d'empreinte : il entre dans le delta, il est ré-extrait, et 06b le
remplace en base. La limite V1 du driver disparaît, **sans toucher au checkpoint de 02**
(le delta est décidé en amont, par l'orchestrateur).

Réserve à vérifier à l'implémentation : Drive ne renseigne pas `md5Checksum` pour les Google
Docs natifs (ils n'ont pas de contenu binaire stable). Pour ceux-là, se rabattre sur la date
de modification, qui est fiable.

## 5. Planification

- Une exécution par nuit, hors heures d'usage du cabinet (02:00), copro par copro, sans
  parallélisme.
- Journal par exécution, et **compte rendu par courriel en cas d'échec uniquement** : une
  synchronisation silencieuse qui échoue en silence est pire que pas de synchronisation.
- Garde-fou : si le delta dépasse un seuil (par exemple 30 % des documents d'une copro),
  ne rien faire et alerter — c'est la signature d'un dossier Drive réorganisé ou d'un
  incident d'accès, pas d'une mise à jour normale. Le rapatriement massif du 24/09 sur
  100 Victor Hugo (192 documents « disparus » qui n'étaient que déplacés) est l'exemple
  type de ce qu'il ne faut pas propager en base sans regarder.

## 6. Découpage

| Lot | Contenu | Vérification |
|---|---|---|
| **E1** | inventaire + delta + purge, sans téléchargement (mode `--dry-run`) | sur les 30 copros, le delta annoncé correspond à ce qu'on sait du Drive |
| **E2** | téléchargement partiel, exécution 01→09, purge | une copro témoin : un document ajouté, un modifié, un supprimé sont bien répercutés |
| **E3** | planification + alerte en cas d'échec + garde-fou de seuil | une nuit à blanc, puis une nuit réelle |
| **E4** | bascule de l'exécution vers AWS eu-west-1 (facultatif, mais c'est ce qui rend l'engagement « aucune copie » facile à défendre en audit) | — |

E1 et E2 constituent le cœur. E3 rend la promesse client effective. E4 relève de la décision
commerciale (cf. l'avenant RGPD, point 3 des questions internes).

## 7. Ce que 06E ne fait pas

- Il ne remplace pas une ingestion initiale : une nouvelle copropriété passe toujours par
  `add_copro.py` puis une première ingestion complète.
- Il n'invente pas d'identité : une copro absente du profil client reste ignorée, même si son
  dossier apparaît dans le Drive.
