# Plan — Publipostage et redondance intra-document (factorisation)

> Statut : **spécification, rien codé.** Rédigé le 23/08/2026, révisé après revue externe.
> Déclencheur : `Courrier-complet.pdf` de 5750 (PV d'AG 2026 de 23 pages recopié pour chaque
> copropriétaire, PDF final de 3 000+ pages) rejeté par Textract, et l'anomalie des 66 chunks par
> document relevée sur 5750.
> Chantiers voisins : `PLAN_REGISTRE_INGESTION.md` (statuts et motifs), guide Section 22 (doctrine
> de dédup), backlog gouvernance du retrieval.

---

## 1. Constat mesuré (base NCG, 365 041 chunks, 23/08/2026)

Redondance interne = part des chunks d'un document dont le texte est strictement identique à un
autre chunk **du même document**.

| Bande | Documents | Chunks | dont redondants |
|---|---:|---:|---:|
| **Massive (≥ 80 %)** | 33 | 102 383 | **88 982** |
| Forte (60-80 %) | 42 | 29 553 | 21 197 |
| Moyenne (30-60 %) | 25 | 12 248 | 4 820 |
| Faible (< 30 %) | 187 | 45 576 | 2 127 |
| Aucune | 12 824 | 175 281 | 0 |

**24 % de la base NCG est du texte répété à l'intérieur d'un même fichier**, concentré sur
33 documents : 10 sur 5750 (51 814 chunks redondants), 8 sur 8050 (22 128), le reste sur 8030,
5390, 5784, 5480, 5354, 5427.

Répartition par type des 33 documents massifs : **24 `COURRIER`** (95 636 chunks) et
**9 `PV_AG`** (6 747 chunks). Les 9 PV_AG portent tous le même nom, `Courrier-complet.pdf`, sur
5 copropriétés différentes : c'est une **convention de nommage maison de NCG** pour le publipostage
du PV, pas un accident local. Seul celui de 5750 a échoué à l'extraction, faute de tenir dans
Textract ; les 8 autres sont en base.

## 2. Diagnostic

Ce n'est ni un document à rejeter, ni un doublon inter-document. C'est un **bundle de publipostage** :
un corps commun (le PV, la convocation) répété une fois par destinataire, entrelacé de blocs
nominatifs et d'annexes individualisées (états de charges par copropriétaire).

Le geste correct n'est donc pas un motif de rejet au niveau du document mais une **factorisation au
niveau du chunk, à l'intérieur du document**.

## 3. Doctrine : factoriser, ne pas détruire

Reprise de la doctrine du chantier dédup (guide Section 22) et de la revue externe du 23/08 :

> On indexe le corps une seule fois, on conserve la trace de ses occurrences, et on empêche le
> publipostage de saturer le retrieval.

La formulation « la dédup intra-document ne peut pas perdre d'information » utilisée dans la
première version de ce plan était **surinterprétée** : elle ne vaut que pour le contenu textuel.
La position, le voisinage et le rattachement à un destinataire sont aussi de l'information. D'où la
factorisation avec compteur d'occurrences plutôt que la suppression sèche.

## 4. Ce que la mesure a tranché (et qui économise beaucoup de travail)

Trois hypothèses de la revue externe ont été vérifiées avant d'être retenues ou écartées.

**a. « Une même clause peut être rattachée à deux résolutions différentes » — mesuré à ZÉRO.**
Nombre de textes identiques portant des `resolution_category` divergentes sur toute la base : **0**.
Le risque existe en théorie, il ne se matérialise pas dans ce corpus. Le garde-fou est conservé
(§7) parce qu'il est gratuit, mais il ne faut pas lui prêter un bénéfice qu'il n'a pas.

**b. Les numéros de page n'existent nulle part dans le pipeline.** La table `chunks` porte
`chunk_index` et `total_chunks`, jamais de `page_start`/`page_end`, et l'extraction Textract
restitue du texte concaténé sans frontière de page. Toute structure d'occurrences indexée par page
suppose une **refonte de l'extraction**, pas un raffinement de ce plan.

**c. Le destinataire n'est pas extractible aujourd'hui.** Inspection des chunks à occurrence unique
du `Courrier-complet.pdf` de 8050 : ce sont des états de charges et des lignes de budget, commençant
en plein milieu d'un mot (`.00 110.00 623000 Rémunérations de tiers…`). Le découpage se fait à
1 500 caractères sans égard pour les frontières de courrier. Un identifiant d'unité de publipostage
avec destinataire présuppose une segmentation qui n'existe pas.

**d. La preuve de notification est déjà modélisée ailleurs.** Le doc_type **`BORDEREAU_AR`** existe
depuis la v0.5 et compte **88 documents en base** (15 sur 8050, 16 sur 5390). C'est le bordereau
d'accusé de réception qui établit la notification et fait courir le délai de contestation de
l'article 42, pas le publipostage. Conserver 1 262 copies d'un PV pour se ménager une preuve d'envoi
dupliquerait massivement une fonction déjà remplie, et moins bien.

**e. Le contenu n'est jamais captif du bundle.** Sur 8050, le PV existe en document autonome en cinq
versions (`Compte Rendu AG 2026 V1/V2/V4/rejoué`). Sur 5750, `PV AG SIGNE.pdf` (35 chunks) est en
base et remonte correctement. (Ces versions relèvent par ailleurs de la brique version chains.)

## 5. Mécanisme A — Factorisation intra-document (cœur du plan)

**Règle.** Dans un même document, les chunks au texte strictement identique après normalisation
sont écrits **une seule fois**, et le chunk survivant porte le nombre d'occurrences.

**Schéma.** Une colonne sur `chunks` :

```sql
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS nb_occurrences INTEGER NOT NULL DEFAULT 1;
```

Un entier, pas deux tables. La proposition de la revue (`source_chunks` / `retrieval_chunks`)
impliquerait de refaire le chargement, le retrieval MCP, `get_chunks` et `get_full_document` pour le
même bénéfice fonctionnel immédiat, sur un phénomène concentré sur 33 documents.

**Où.** `03_chunking.py`, à l'écriture, par document.

**Identité des chunks — le point qui rend la correction quasi gratuite.** Le `chunk_id` est déjà
content-addressed avec un compteur d'occurrence :
`_id_key = base if occ == 0 else f"{base}#{occ}"` où `base = source_file||texte`.
La factorisation ne garde que l'occurrence 0, dont le `chunk_id` est **inchangé**. Conséquences :

- `05_embedding` ne ré-embedde rien (les `chunk_id` survivants sont déjà connus) ;
- `04_metadata` est caché par chemin, aucun appel Haiku nouveau ;
- `06b` recharge une copro plus légère.

Le rattrapage des 33 documents coûte donc **du temps de calcul, pas de l'API**.

**Numérotation.** `chunk_index` renuméroté de façon contiguë dans l'ordre de première apparition,
`total_chunks` = nombre de chunks écrits après factorisation. Objectif : `get_full_document`, qui
réassemble par `chunk_index`, reste cohérent et sans trou.

**Conditionnalité (garde-fou §7).** Appliquée uniquement aux documents dépassant les seuils de §6,
pas à tout le corpus.

## 6. Mécanisme B — Qualifier le document (observabilité)

Un attribut, pas un rejet. Écrit dans `documents` et dans `ingestion_registre` :

| Champ | Contenu |
|---|---|
| `redondance_interne` | ratio mesuré, 0 à 1 |
| `chunks_avant_factorisation` | compte brut |
| `chunks_apres_factorisation` | compte écrit |
| `profil_repetitif` | `PUBLIPOSTAGE` / `REPETITIF_SUSPECT` / NULL |

**Seuils, déduits de la mesure et non choisis a priori :**

- `PUBLIPOSTAGE` : redondance **≥ 80 %** ET **≥ 20 chunks** ET **≥ 2 textes uniques**.
- `REPETITIF_SUSPECT` : redondance **60-80 %**, **observation seule, aucune factorisation**.

La bande 60-80 % ne doit pas déclencher de traitement : elle contient des documents légitimes
vérifiés, un rapport d'expertise de sinistre sur 8050 (1 560 chunks pour 519 uniques) et un document
de synthèse de diagnostic (1 341 pour 447), dont la répétition vient de tableaux et d'en-têtes
récurrents. À 60 % on les qualifierait à tort. La catégorie intermédiaire, suggérée par la revue,
sert à mesurer si ce choix se confirme dans le temps.

**Aucun effet automatique en V1.** L'attribut sert au débogage, à l'audit de coût, et alimentera le
cap de diversité au retrieval quand celui-ci sera décidé.

## 7. Garde-fous du mécanisme A

1. **Application conditionnelle** : seuls les documents `PUBLIPOSTAGE` sont factorisés. Un document
   ordinaire garde ses chunks tels quels, même s'il contient une répétition. Coût du garde-fou :
   nul. Bénéfice mesuré : nul aujourd'hui (§4a), mais il protège d'un corpus futur différent.
2. **Jamais de document vide** : un document intégralement composé de répétitions conserve au moins
   une occurrence de chaque texte distinct, donc au moins un chunk.
3. **Sourçage préservé** : `get_chunks` et `get_full_document` travaillent par `chunk_id` et par
   document ; la renumérotation contiguë doit être vérifiée sur un document témoin.
4. **Réversibilité** : la factorisation se rejoue par un simple re-run de `03` sur la copro, aucune
   donnée source n'étant modifiée.

## 8. Mécanisme C — Volume excessif : quarantaine, pas rejet

Le cas de `Courrier-complet.pdf` de 5750 finit aujourd'hui en `ERREUR`/`EXTRACTION_KO`,
indistinguable d'une panne réseau, avec un message invitant à relancer, donc à répéter une dépense
inutile.

**Correction retenue** (reformulation de la revue, adoptée) : ce n'est pas un rejet, c'est une
orientation. Nouveau statut au registre, dans l'esprit du modèle LLB déjà transposé :

- statut **`QUARANTAINE`**, motif **`VOLUME_EXCESSIF`**, étape `01`.
- Sens : *ce document ne doit pas passer dans le pipeline standard*, il attend un traitement
  spécialisé ou une décision humaine. Il n'est ni ingéré, ni perdu, ni retenté automatiquement.

**Où, et pourquoi avant l'OCR.** La revue a raison : agir en `03` n'économise pas Textract. Le
nombre de pages d'un PDF est connu **sans aucune dépense** par `00a_cost_preflight.py`. Le contrôle
se place donc en `01_filtrage.py`, avant toute copie et tout appel Textract.

**Seuil** : plafond de pages configurable par client (`dedup_rules.json`), valeur initiale proposée
**500 pages**. Un règlement de copropriété avec annexes peut légitimement atteindre 200 à 300 pages,
d'où une marge. La limite dure de Textract en mode asynchrone est à confirmer dans la documentation
AWS avant de figer la valeur ; l'échec observé porte sur un document de 3 000+ pages.

## 9. Hors scope, explicitement

**La segmentation par destinataire** (unité de publipostage, nom et adresse du destinataire,
plage de pages, rattachement au corps canonique) est **un chantier distinct**, pas un raffinement de
celui-ci. Elle suppose :

1. une extraction consciente des pages dans `02` (aujourd'hui absente, §4b) ;
2. une segmentation du bundle en courriers (aujourd'hui absente, §4c) ;
3. de nouvelles colonnes ou tables pour porter le rattachement.

Elle ne se justifiera que sur une demande métier avérée, et en tenant compte du fait que la preuve
de notification est déjà portée par `BORDEREAU_AR` (§4d). À documenter comme prérequis, à décider
séparément.

**Le découpage physique du bundle en PDF individuels** est écarté : coût élevé, risque d'erreur de
segmentation, bénéfice couvert par la factorisation.

**Les caps de diversité au retrieval** (maximum N chunks par document ou par famille dans le top-k)
relèvent du backlog gouvernance du retrieval, pas de ce plan. Ils restent nécessaires **après** ce
nettoyage : un document massif peut continuer à dominer par ses chunks uniques.

## 10. Critères de validation

**Testables à l'issue de ce chantier :**

1. Sur un document `PUBLIPOSTAGE` témoin : `count(*) = count(distinct text)` après re-run, et
   `sum(nb_occurrences)` égale le compte de chunks avant factorisation.
2. Le texte réassemblé par `get_full_document` contient toujours l'intégralité du corps du PV.
3. `chunk_index` contigu de 0 à `total_chunks - 1`, sans trou, sur les documents factorisés.
4. Aucun document ne disparaît de `documents` ; aucun ne tombe à zéro chunk.
5. La base NCG perd environ **89 000 chunks**, soit 24 %.
6. Le rapport d'expertise de 8050 (1 560/519) et le document de synthèse de diagnostic (1 341/447)
   restent **intégralement chunkés** et qualifiés au plus `REPETITIF_SUSPECT`.
7. Une question sur l'AG 2026 de 5750 remonte `PV AG SIGNE.pdf` en tête (déjà le cas, doit le rester).
8. Aucun `chunk_id` survivant ne change, donc `05_embedding` ne fait aucun appel Titan au rattrapage.

**Non testables ici, à porter au chantier segmentation** (repris de la revue, avec la réserve
d'honnêteté qui s'impose) : « le PV a-t-il été envoyé à Mme Martin », « quels copropriétaires ont
reçu ce courrier », « reconstitue le courrier envoyé à M. Dupont ». Ces trois questions échouent
aujourd'hui et **échoueront encore après ce chantier**, faute de segmentation. Les inscrire comme
critères ici reviendrait à se raconter une histoire.

## 11. Phasage

| Phase | Contenu | Coût |
|---|---|---|
| **P1** | Mécanisme B seul (mesure et qualification, aucune modification de chunk) sur les deux bases | Nul, lecture |
| **P2** | Mécanisme A (factorisation) + tests + re-run des copros concernées | Temps de calcul, ~0 $ d'API (§5) |
| **P3** | Mécanisme C (quarantaine volume excessif) dans `01` + seuil par client | Faible |
| **P4** | Caps de diversité au retrieval, si la mesure P1 le justifie | À décider |

P1 avant P2 délibérément : on qualifie et on mesure avant de modifier quoi que ce soit, et la liste
exacte des documents à factoriser sort de P1.

## 12. Points ouverts pour arbitrage

1. **Seuil de pages du mécanisme C** : 500 pages proposé, à confirmer contre la limite réelle de
   Textract et contre le plus gros RCP légitime du parc.
2. **Rattrapage des 33 documents** : au fil de l'eau (chaque copro en profite à son prochain re-run)
   ou re-run ciblé immédiat. Le §5 montrant que le coût API est nul, le re-run ciblé devient
   défendable, contrairement à ce que supposait la première version de ce plan.
3. **Sort de `Courrier-complet.pdf` de 5750** : quarantaine simple, ou traitement spécialisé pour en
   extraire le PV. Le PV étant déjà en base via `PV AG SIGNE.pdf`, la quarantaine simple suffit
   probablement.
