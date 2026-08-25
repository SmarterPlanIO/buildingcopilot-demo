# Plan — `05b` questions synthétiques : stabilité, cache, pré-filtre

> Statut : **spécification, rien codé**. Rédigé le 24/08/2026, après revue externe
> et vérification de ses affirmations sur le corpus réel.
> Déclencheur : `05b` est le premier poste de coût Haiku du pipeline (24 $ sur la
> seule copropriété 5440, 16 950 appels), et la seule étape LLM sans cache.

---

## 1. À quoi servent ces questions (rappel)

Elles corrigent un **décalage de vocabulaire** entre la question du gestionnaire et
le texte du document. `05b` fait générer par Haiku 3 à 5 questions par chunk, dont
la réponse est dans le texte, et les stocke dans `chunks.synthetic_questions`.
Elles entrent dans l'index BM25 avec un **poids D (0,1)** contre A (1,0) pour le
texte : elles élargissent le rappel sans peser sur le classement.

Périmètre actuel : `doc_type ∈ {PV_AG, RCP, CONTRAT}`, `chunk_index > 0`,
`resolution_category ∉ {PROCEDURE_AG, ELECTION_CS}`.

## 2. Ce que la mesure a établi (24/08, base NCG)

Trois vérifications faites avant d'écrire ce plan, dont deux **contredisent** la
revue externe.

**a. Le pont lexical fonctionne réellement.** Échantillon de PV_AG :

| Texte source (OCR brut) | Question générée |
|---|---|
| `01 CHARGES GENERALES 601000 EauEAU FROIDE DU 21/02/2023 - 1615 M3 7971.66 VEOLIA…` | *Quel est le montant total des charges d'eau froide pour l'année 2023 ? Qui est le fournisseur ?* |
| `22 MISE EN CONFORMITE DU REGLEMENT… Majorité simple (Art. 24), 10138 tantièmes…` | *Quel est le montant de la proposition de l'ARC pour la mise en conformité ?* |

La contrainte « réponse mot pour mot » n'a **pas** produit l'écho littéral que la
revue redoutait : elle produit un ancrage factuel, ce qui est différent. Sur de
l'OCR comptable sans syntaxe, c'est précisément là que le pont est le plus utile.
**Conclusion : ne pas toucher au prompt sur ce point.**

**b. Le tri conditionnel existe déjà, mais il est payant.** Couverture réelle :
**20 395 chunks portent des questions sur 69 419 éligibles, soit 29 %**. Les 71 %
restants ont été écartés par le filtre d'éligibilité, par `chunk_index > 0`, et
surtout par **Haiku lui-même répondant `SKIP`** (règle explicite du prompt sur les
préambules et le boilerplate).

C'est le vrai gisement : **on paie un appel Haiku pour obtenir un refus, 7 fois
sur 10**. L'enjeu n'est donc pas de mieux trier que Haiku, c'est de ne plus le
payer pour trier.

**c. Les questions génériques sont négligeables.** La question la plus répétée du
parc apparaît **15 fois sur 20 395 chunks**. Le filtrage post-génération proposé
par la revue corrigerait un problème inexistant. **Écarté.**

## 3. Le défaut de fond : la génération n'est pas déterministe

`generate_questions` construit son appel Bedrock avec **`max_tokens: 300` et rien
d'autre** : ni `temperature`, ni `top_p`. Deux exécutions sur le même chunk
produisent donc des questions différentes.

Comme ces questions alimentent l'index BM25, **le classement des résultats de
recherche change entre deux runs sans qu'aucun document n'ait bougé**. C'est
l'anomalie restante d'un pipeline qui a par ailleurs été rendu déterministe
partout (chunk_id content-addressed, caches de `03`, `04`, `05c`).

## 4. Les trois actions, par coût croissant

### A1 — Température à 0 (une ligne)

Ajouter `"temperature": 0` au corps de l'appel. Effet : deux runs sur le même
chunk rendent la même sortie, à modèle constant.

Bénéfice immédiat, aucun risque, et cela **rend le cache moins critique pour la
stabilité** (il reste nécessaire pour le coût). À faire en premier, seul, et à
vérifier par un double run sur une petite copropriété.

Réserve honnête : une température à 0 réduit la variété des formulations, or la
variété est précisément ce qu'on cherche pour élargir le vocabulaire. Le prompt
demande déjà de varier les formulations (`qui, quand, combien, quel`) ; à vérifier
sur un échantillon que la diversité ne s'effondre pas. Repli si c'est le cas :
température basse mais non nulle (0,2) plus cache, la stabilité venant alors du
cache seul.

### A2 — Cache par chunk, versionné

Fichier `synthetic_questions_cache.json` dans le shard, sur le modèle des trois
caches existants (`doc_type_cache.json`, `metadata_cache.json`,
`resolution_format_cache.json`).

**Clé** (reprise de la revue, plus complète que ma première proposition) :

```
chunk_id  +  prompt_version  +  model_id  +  policy_version
```

`chunk_id` vaut déjà `md5(source_file || texte)` : un même texte dans un même
fichier donne toujours la même clé, le cache est donc correct par construction.
Les trois autres composantes évitent la dette invisible : changer le prompt ou le
modèle invalide les entrées au lieu de servir d'anciennes sorties.

**Valeur stockée** : les questions, plus `SKIP` explicite (un refus de Haiku doit
être caché comme un succès, sinon on repaiera l'appel qui coûte le plus cher en
proportion).

**Limite à documenter** : le cache ne protège **pas** contre un changement de
chunking. Quand `03` évolue, les `chunk_id` changent et le cache manque à 100 %.
C'est exactement ce qui s'est produit à la réparation du 24/08. Le cache sert les
re-runs à chunking constant, ce qui couvre le rechargement, la factorisation et le
rattrapage, pas une évolution du découpage.

**Volume** : ~30 000 entrées sur NCG, quelques mégaoctets. Le cache ne se purge
pas ; les entrées de chunks disparus subsistent. Acceptable, mais dit.

### A3 — Pré-filtre local, à calibrer avant d'activer

Objectif : écarter sans appel LLM une partie des 71 % de chunks qui finissent en
`SKIP`.

**Le jeu de calibration est déjà là et il est gratuit** : les 69 419 décisions
déjà prises par Haiku sont en base (questions présentes = accepté, absentes =
refusé). On peut donc mesurer la précision d'un score local **avant** de l'activer,
au lieu de le régler au jugé.

Signaux envisagés, tous locaux et sans LLM : longueur utile du chunk, densité de
chiffres et de montants, présence de dates, présence de vocabulaire métier
(travaux, contrat, charges, résolution, autorise, majorité, tantièmes, devis,
sinistre, assurance, résiliation), `doc_type`, `resolution_category`.

**Règle d'activation** : le pré-filtre n'est activé que si, sur le jeu de
calibration, il écarte des chunks que Haiku aurait refusés avec un **taux de faux
négatifs mesuré et jugé acceptable** (écarter un chunk que Haiku aurait accepté =
perte de rappel définitive jusqu'au prochain re-run). En cas de doute, on laisse
passer : le coût d'un appel inutile est de l'ordre du millième de dollar, celui
d'un chunk devenu introuvable est une réponse fausse.

**Volontairement plus permissif que Haiku.** On ne cherche pas à reproduire son
jugement, seulement à écarter l'évident.

## 5. Écarté, et pourquoi

**Modifier le prompt** (« réponse explicitement justifiable » au lieu de « mot pour
mot ») : la mesure §2a montre que le pont fonctionne déjà. Assouplir la contrainte
d'ancrage augmenterait le risque d'hallucination sur des documents juridiques,
pour un bénéfice non démontré.

**Filtrage post-génération des questions génériques** : 15 occurrences maximum sur
20 395 chunks (§2c). Sans objet.

**Dictionnaire métier de synonymes** : à maintenir à la main, par client, et il
duplique ce que l'embedding vectoriel fait déjà. Le corpus montre que le pont
fonctionne sur de l'OCR comptable dégradé, là où un dictionnaire de synonymes
échouerait.

**Query expansion, même en hybride** : déplace le coût sur chaque question, ajoute
de la latence, et surtout — en architecture MCP, **c'est le LLM du client qui
formule la requête**. Il fait déjà cette expansion. Ce serait payer deux fois.

**Politique de génération par granularité métier** (0 / 1-2 / 3-5 questions selon
la valeur du chunk) : séduisant, mais suppose de classer la valeur métier d'un
chunk, ce qui demande soit un LLM (on retombe sur le coût qu'on veut éviter), soit
des règles fines par type de document à maintenir par client. À reconsidérer si
A3 montre qu'un score local est fiable.

**Métriques `source_of_match` et benchmark de mismatch lexical** : justes sur le
fond, mais c'est un chantier d'évaluation du retrieval à part entière, qui suppose
des golden queries. À rattacher au backlog gouvernance du retrieval, pas ici.

## 6. Critères de validation

**A1 (température)**
1. Deux exécutions consécutives sur une petite copropriété produisent des
   `synthetic_questions` **identiques** pour tous les chunks.
2. Sur un échantillon de 50 chunks, la diversité des formulations (proportion de
   questions commençant par un interrogatif différent) ne baisse pas de plus de
   20 % par rapport à l'existant.

**A2 (cache)**
3. Un second run à chunking constant fait **0 appel Bedrock** et produit un fichier
   de sortie identique au premier.
4. Changer `prompt_version` invalide 100 % des entrées et régénère tout.
5. Le `SKIP` est caché : un chunk refusé au premier run n'est pas re-soumis.

**A3 (pré-filtre)**
6. Mesuré sur les 69 419 décisions en base : taux d'accord avec Haiku, et surtout
   **taux de faux négatifs** (chunks écartés que Haiku aurait acceptés).
7. Non activé tant que ce taux n'est pas jugé acceptable par arbitrage explicite.

**Transversal**
8. Non-régression : sur une copropriété témoin, le nombre de chunks portant des
   questions ne baisse pas après A1 et A2 (seul A3 peut légitimement le faire
   baisser, et de façon mesurée).

## 7. Phasage et gain attendu

| Phase | Contenu | Coût | Gain |
|---|---|---|---|
| **A1** | `temperature: 0` | nul | stabilité du classement entre runs |
| **A2** | cache versionné | faible | re-runs à chunking constant quasi gratuits |
| **A3** | pré-filtre calibré | moyen | jusqu'à ~70 % d'appels évités, si la calibration le permet |

A1 et A2 sont indépendants et peuvent être livrés ensemble. A3 ne se décide
qu'après mesure, et sa spécification définitive dépendra des chiffres de
calibration.

Sur les 107 $ de Haiku dépensés les 22-24/08, la part `05b` aurait été très
largement évitée par A2 seul pour les rechargements à chunking constant (P2,
rattrapage `.odt`), et pas du tout pour la réparation (chunking modifié).
