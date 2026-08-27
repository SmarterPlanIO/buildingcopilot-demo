# Plan — Self-learning PALIM (boucle feedback → correction → non-régression)

> Statut : **rédigé le 27/08/2026, rien codé**.
> Chantiers liés : `PLAN_REGISTRE_INGESTION.md` (observabilité pipeline),
> prochain deploy image MCP = **v12** (v11 en prod sur les 2 Lambdas depuis les 24-26/08,
> runbook clos ; modèle : `mcp_server/RUNBOOK_DEPLOY_V11.md`) — véhicule naturel du P0,
> Instructions produit `clients/INSTRUCTIONS_TEMPLATE_PALIM.md` (couche mémoire versionnée).
> Existant exploité : tool `PALIM_log_feedback` (introduit en v8, en prod v11) + Langfuse Cloud EU
> (score `user_feedback`, traces retrieval rattachées via `trace_ref`).

---

## 1. Le problème que ça résout

Les beta users NCG (Quentin, Johan, Christophe) signalent des réponses fausses ou incomplètes.
Aujourd'hui ces signalements arrivent dans Langfuse via `PALIM_log_feedback`… et s'y arrêtent.
Rien ne garantit qu'un feedback soit lu, diagnostiqué, corrigé, ni surtout que **l'erreur corrigée
ne revienne pas** à la prochaine version des instructions, du pipeline ou de l'image MCP.

Un système n'a durablement appris que si l'erreur corrigée est devenue un test. Ce plan construit
la boucle complète :

```
capture (MCP → Langfuse)  →  triage root-cause (semi-auto)  →  correction à la bonne couche
        ↑                                                              ↓
        └────────────  non-régression (golden cases rejoués à chaque deploy)  ←──────┘
```

## 2. Principe d'architecture

**Contrainte fondatrice : le LLM est celui du client** (Claude Teams / Cowork). Pas de fine-tuning,
pas de RLHF, pas de contrôle sur le modèle. L'apprentissage s'encode exclusivement dans les
artefacts que SmarterPlan contrôle et versionne :

| Artefact | Ce qu'il mémorise | Versionné par |
|---|---|---|
| Project Instructions (template + instance client) | Consignes, cas d'usage, pièges connus | Git + version (v3.x) |
| Skills (`ncg-*`, `assynco-erp`) | Procédures métier par type de tâche | Git |
| Docstrings/signatures des tools MCP | Le contrat d'appel (seule chose que Claude voit) | Image Lambda (vN) |
| Données de retrieval (chunks, metadata, synthèses) | Les faits | Pipeline + DB |
| Golden cases (`evals/golden_cases.jsonl`) | **Les erreurs passées, sous forme de tests** | Git |

**Humain dans la boucle, obligatoirement.** Le domaine est juridique ; un commentaire utilisateur
peut être faux ; le réinjecter automatiquement graverait l'erreur (et ouvrirait un vecteur
d'empoisonnement du prompt). On automatise la capture, le pré-diagnostic, la génération des tests
et la non-régression. La décision de correction reste à Thai.

## 3. Existant (ce qu'on ne recode pas)

- **Capture** : `PALIM_log_feedback(rating, comment, question, copro_codes, mode, utilisateur,
  trace_ref)` → Langfuse. `rating` binaire (`utile`=1.0 / `a_ameliorer`=0.0), score nommé
  `user_feedback`. Si `trace_ref` fourni (renvoyé par `search_chunks`/`search_dossiers`), le score
  se rattache à la trace de retrieval (avec ses chunks) ; sinon trace autonome `PALIM_feedback`
  taguée `["mcp","feedback"]`. Le Bloc feedback des Instructions demande déjà au LLM client de
  proposer le feedback sur les réponses métier non triviales.
- **Observabilité** : Langfuse Cloud EU, projet "PALIM MCP", pin `langfuse==2.60.4`.
- **Non-régression partielle** : tests contrats (`tests/test_palim_analytics_contracts.py`,
  `test_linkify.py`), smokes live manuels dans les runbooks de deploy. Rien ne teste le
  comportement de bout en bout question → réponse.
- **Historique d'erreurs** : ~10 bugs documentés (CLAUDE.md §5, memory) qui font d'excellents
  premiers golden cases (cf. §6.4).

## 4. Familles de root cause et couche de correction

Un feedback "à améliorer" ne dit pas ce qui est cassé. Le triage (§5.2) classe chaque item dans
une famille ; chacune se corrige à une couche différente, et **jamais à une autre** :

| # | Famille | Symptôme type | Correction | Golden case associé |
|---|---|---|---|---|
| F1 | Retrieval raté | Le fait existe en base, pas remonté | Pipeline : chunking, metadata, doc_type, rerank, seuils | mode `retrieval` |
| F2 | Document absent | Le fait n'est nulle part en base | Registre d'ingestion (motif REJETE ?), signaler le doc manquant au client | mode `retrieval` après ré-ingestion |
| F3 | Consigne mal suivie / cas non prévu | Bon contexte, mauvaise réponse ou mauvais format | Project Instructions ou skill (version N+1) | mode `end_to_end` |
| F4 | Tool pas appelé ou mal appelé | Réponse "je ne sais pas" alors que le tool existe (cf. bugs get_chunks, visite_3d) | Docstring/signature du tool → image vN+1 | mode `end_to_end` (`tools_attendus`) |
| F5 | Fait périmé / correction métier | La base dit vrai-mais-obsolète, ou l'utilisateur apporte un fait externe | Donnée : ré-ingestion du doc à jour, ou `NOTE_VALIDEE` (§8) | mode `retrieval` |

Règle d'or : ne jamais patcher les Instructions pour compenser un retrieval cassé (F1 traité en
F3). Ça masque le bug, ne le corrige pas, et pollue le prompt de tous les autres cas.

## 5. P0 — Exploiter la capture existante

### 5.1 Enrichir `PALIM_log_feedback` (contrat v2)

Faiblesse actuelle : le feedback porte la question et un commentaire, pas la réponse fautive ni
ce qu'elle aurait dû dire. Ajouts, tous **optionnels** (compat ascendante, aucun client à casser) :

```python
reponse_extrait: str | None = None   # les 2-3 affirmations clés de la réponse jugée, verbatim (≤1500c)
attendu: str | None = None           # ce que la bonne réponse aurait dû dire, selon l'utilisateur (≤1000c)
doc_refs: list[str] | None = None    # doc_ids ou noms de fichiers cités par la réponse jugée
```

Docstring : demander au LLM client de remplir `reponse_extrait` systématiquement sur un
`a_ameliorer`, et `attendu` quand l'utilisateur l'a exprimé ("non, c'est la résolution 12, pas
la 14"). Ces trois champs partent dans le `context` de la trace/score Langfuse (pas de nouveau
stockage). C'est ce qui transforme un pouce rouge en cas exploitable sans rejouer la session.

Véhicule : changement de docstring = changement de contrat = redeploy image. **À embarquer dans
le prochain deploy image (v12)**, sur le modèle du RUNBOOK_DEPLOY_V11 (tag neuf, jamais
d'écrasement), + mention dans les Instructions (Bloc feedback) recollées au même moment.

### 5.2 `feedback_report.py` — export + pré-triage hebdo

Nouveau script `Scripts/feedback_report.py`, lancé du poste (comme le pipeline) :

1. **Export** : API publique Langfuse (`GET /api/public/scores?name=user_feedback` avec
   basic auth pk/sk, puis `GET /api/public/traces/{traceId}` pour chaque score rattaché →
   question, chunks retournés, latences). Clés déjà en env local. Fenêtre : depuis le dernier
   run (curseur `feedback_report_state.json` local).
2. **Pré-triage Haiku** : pour chaque `a_ameliorer`, un appel Haiku classe en F1-F5 (avec le
   commentaire, la question, `reponse_extrait`, `attendu`, et la liste des chunks de la trace),
   propose une hypothèse de fix en une phrase, et un niveau de confiance. Coût : centimes.
3. **Vérification F1/F2 automatique** : si la famille pressentie est F1/F2, le script interroge
   la base en lecture (`chunks` + `ingestion_registre`) pour dire si le fait attendu a des chunks
   candidats (recherche plein texte sur `attendu`/`comment`) → distingue "existait mais pas
   remonté" (F1) de "absent de la base" (F2, avec le motif du registre si le doc y est REJETE).
4. **Sortie** : `Resultats bruts/feedback_reports/feedback_report_<date>.md` — un tableau par
   item (date, user, copro, question, rating, famille pressentie, hypothèse, lien trace
   Langfuse) + compteurs (taux utile par user/mode/semaine).

Le rapport se lit en session Claude Code hebdo : chaque item `a_ameliorer` y reçoit une décision
(fix maintenant / backlog / rejeté-feedback-erroné) et, si fixé, son golden case (§6).

**Livrable P0** : contrat v2 du tool (dans v10), `feedback_report.py`, premier rapport généré.

## 6. P1 — Golden cases + `eval_golden.py` (le cœur du "plus jamais deux fois")

### 6.1 Schéma d'un golden case

Fichier `Scripts/evals/golden_cases.jsonl`, versionné git, une ligne par cas :

```json
{
  "case_id": "GC-001",
  "source": "bug_historique | feedback:<trace_id> | manuel",
  "date_ajout": "2026-08-27",
  "famille": "F1",
  "mode": "retrieval | end_to_end",
  "question": "Quels sinistres en cours sur la copro 8050 ?",
  "copro_codes": ["8050"],
  "tools_attendus": ["PALIM_search_dossiers"],
  "faits_attendus": ["dégât des eaux 2024", "n_total >= 90"],
  "faits_interdits": ["aucun sinistre trouvé"],
  "doc_ids_attendus": [],
  "commentaire": "Ancienne erreur : faux négatif search_dossiers (fix 75ccf5b, v6).",
  "statut": "actif"
}
```

- `faits_attendus` / `faits_interdits` : assertions en langage naturel, évaluées par le judge
  (mode `end_to_end`) ou cherchées dans les chunks retournés (mode `retrieval`).
- `faits_interdits` porte **l'ancienne erreur** : c'est lui qui matérialise "ne pas répéter".
- `statut: obsolete` (jamais de suppression) quand le cas ne s'applique plus (copro sortie du
  périmètre, consigne volontairement changée) — l'historique reste lisible.

### 6.2 Runner `eval_golden.py` — deux modes

**Mode `retrieval` (F1/F2/F5) — déterministe, zéro judge.** Appelle directement les tools MCP
(client HTTP streamable vers la Function URL, même mécanique que les smokes des runbooks) :
`search_chunks`/`search_dossiers` avec la question et les copros du cas, puis vérifie que les
`doc_ids_attendus` ou des chunks contenant les `faits_attendus` (match texte simple) figurent
dans le top-k. Rapide (~1 s/cas), gratuit, lançable à chaque itération pipeline.

**Mode `end_to_end` (F3/F4) — mini-harness agentique + LLM judge.** Reproduit ce que fait Claude
Teams : Sonnet 4.6 via Bedrock (API Converse) avec (a) les Instructions client comme system
prompt, (b) les tools du serveur MCP exposés en tool-use (le harness fait le pont
Converse ↔ MCP), (c) boucle d'orchestration ≤ 8 tours. On enregistre les tools réellement
appelés (assertion `tools_attendus`, attrape la famille F4) et la réponse finale. Puis un judge
Haiku reçoit réponse + `faits_attendus` + `faits_interdits` et rend
`{fait: present|absent, verdict: pass|fail}` par assertion. Verdict cas = tous les attendus
présents ET tous les interdits absents ET tools attendus appelés.

Sortie des deux modes : tableau console + `evals/last_run_<date>.json` (par cas : pass/fail,
détail par assertion, tours, tokens). **Code de retour ≠ 0 si un cas actif échoue.**

Limite assumée : le harness Converse n'est pas exactement Claude Teams (pas les skills, modèle
Bedrock vs claude.ai). Il teste le contrat Instructions + tools + données, pas le client final.
C'est le bon niveau : c'est ce contrat qu'on versionne et qu'on déploie.

### 6.3 Câblage dans les runbooks

Ajout d'une étape bloquante aux deux runbooks de livraison :

- **Deploy image MCP vN** : `eval_golden.py --mode retrieval` puis `--mode end_to_end` contre la
  Function URL fraîche, AVANT de basculer le connecteur client. Rouge = rollback image.
- **Recollage Instructions vN** : `eval_golden.py --mode end_to_end --instructions <fichier>` en
  pointant la nouvelle version, AVANT de recoller côté Claude Teams.
- **Ré-ingestion copro** (`ingest.py --copro`) : `--mode retrieval --copro <code>` sur les cas de
  cette copro, après le 06b/08.

### 6.4 Amorce : les golden cases gratuits

Avant tout nouveau feedback, ~10 cas tirés des bugs déjà corrigés et documentés :

| case_id | Ancienne erreur | Mode |
|---|---|---|
| GC-001 | Faux négatif `search_dossiers` 8050 (fix v6) | retrieval |
| GC-002 | Isolation tenant Assynco : 0149/RJ TRODE doit rester introuvable (fix v8) | end_to_end |
| GC-003 | Contrôle positif tenant : 5390 doit résoudre | end_to_end |
| GC-004 | Visite 3D non appelée sur mot-clé (fix Instructions v1.7) | end_to_end (`tools_attendus`) |
| GC-005 | Chunk MARROUNI ne fuit pas dans un scope BRESSON | retrieval (`faits_interdits`) |
| GC-006 | Bordereaux AR exclus du ranking par défaut | retrieval |
| GC-007 | RCP présents (protection reclassement MUTATION) | retrieval |
| GC-008 | `copro_overview` : chronologie fiable (Instructions v3.4) | end_to_end |
| GC-009 | Analytique : coverage annoncée par `run_analytical_query` (v10) | end_to_end |
| GC-010 | Sourçage v1.9 : citations [N] sans snippet inventé | end_to_end |

**Livrable P1** : `evals/golden_cases.jsonl` (10 cas amorce), `eval_golden.py` (2 modes),
runbooks amendés, un run complet vert documenté.

## 7. P2 — Le process qui rend la boucle pérenne

Du code seul ne suffit pas ; la boucle vit par une discipline courte, documentée dans
`ops/PROCESS_FEEDBACK.md` :

1. **Cadence hebdo** (~30 min, session Claude Code) : générer le rapport P0, décider chaque
   `a_ameliorer` : fix maintenant / backlog daté / rejeté (feedback erroné → noter pourquoi).
2. **Invariant : 1 feedback résolu = 1 fix versionné + 1 golden case.** Un fix sans golden case
   n'est pas terminé. Le commit du fix embarque le cas (même commit ou même merge).
3. **Section "Leçons apprises" dans le template produit** (`INSTRUCTIONS_TEMPLATE_PALIM.md`) :
   uniquement les leçons de famille F3 généralisables ("sur une question de charges, toujours
   vérifier l'exercice comptable concerné"), bornée à ~10 items, élaguée à chaque version. Les
   corrections factuelles n'y vont JAMAIS (elles vont en données, F5).
4. **Boucle courte vers les users** : quand un feedback d'un beta user a produit un fix, le lui
   dire (mail ou point pilote). C'est ce qui entretient le réflexe de feedback — le taux de
   capture est la ressource rare de tout le système.
5. **Revue mensuelle des métriques** (§10) au fil du pilote NCG.

**Livrable P2** : `ops/PROCESS_FEEDBACK.md`, section Leçons dans le template (vide au départ),
première itération hebdo réellement tenue.

## 8. P3 — `NOTE_VALIDEE` : mémoire dynamique côté retrieval (si le besoin se confirme)

Pour les corrections factuelles récurrentes (F5) qui n'ont leur place ni dans les Instructions ni
dans un document client : "le PV 2019 est remplacé par celui de 2021", "l'interlocuteur assurance
a changé", "ce montant du RCP est erroné, cf. modificatif".

- Nouveau doc_type `NOTE_VALIDEE` : notes courtes (≤ 10 lignes), rédigées et validées par Thai,
  stockées en `Resultats bruts/notes_validees/<copro>/*.md`, ingérées par le pipeline normal
  (embeddings + metadata), boostées dans le ranking (patron du boost synthétique existant).
- Écriture **exclusivement côté pipeline** (`ragadmin`). Le MCP reste read-only (règle 3.7) : une
  note poussée un soir est en prod au batch suivant, sans deploy.
- Traçabilité : chaque note référence son origine (`source: feedback:<trace_id>`) et produit son
  golden case mode `retrieval`.

Déclencheur de mise en chantier : ≥ 3 feedbacks de famille F5 en backlog. Avant ça, la
ré-ingestion du document à jour suffit et P3 serait de la sur-ingénierie.

## 9. Ce qu'on ne fait PAS (anti-scope)

- **Pas d'auto-modification des Instructions par le système.** Toute évolution de prompt passe
  par une version git relue par un humain.
- **Pas de réinjection brute des commentaires utilisateurs dans le contexte** (ni RAG sur les
  feedbacks) : faux positifs gravés + vecteur d'empoisonnement.
- **Pas de fine-tuning** : le LLM appartient au client.
- **Pas d'écriture DB par le MCP** : le feedback vit dans Langfuse ; les notes validées passent
  par le pipeline. `mcp_ncg_reader` reste lecture seule.
- **Pas de note de satisfaction fine (1-5)** : le binaire utile/à améliorer + commentaire libre
  suffit au pilote et maximise le taux de capture.

## 10. Métriques de succès

Toutes lisibles dans Langfuse ou dans la sortie d'`eval_golden.py` :

| Métrique | Source | Cible pilote |
|---|---|---|
| Taux `utile` (score `user_feedback` moyen), par user/mode/mois | Langfuse | Tendance croissante ; argument commercial NCG |
| Volume de feedbacks capturés / semaine | Langfuse | ≥ 3 (sinon problème de réflexe, cf. §7.4) |
| Délai médian feedback → fix mergé | rapport hebdo | < 14 j |
| Golden cases actifs / taux de pass au dernier deploy | `eval_golden.py` | 100 % de pass exigé pour livrer |
| Récidives (feedback matchant un golden case existant) | triage hebdo | 0 — chaque récidive = trou du harness à analyser |

## 11. Estimation

| Brique | Effort | Coût récurrent |
|---|---|---|
| P0 contrat v2 + `feedback_report.py` | ~½ jour (le tool change de docstring, le script est du glue Langfuse/Haiku) + un deploy image v12 | < 0,10 $/rapport |
| P1 goldens + runner | ~1,5 jour (le harness Converse↔MCP est la seule vraie pièce) | mode retrieval ≈ 0 $ ; end_to_end ≈ 0,05-0,15 $/cas/run (Sonnet + Haiku judge), soit < 2 $/deploy à 10-15 cas |
| P2 process | ~½ jour + 30 min/semaine | — |
| P3 NOTE_VALIDEE | ~1 jour, si déclenché | coût d'ingestion marginal |

## 12. Ordre de livraison

1. **P0** — capture enrichie (embarquée dans le prochain deploy image, v12) + `feedback_report.py`.
2. **P1** — 10 golden cases d'amorce + `eval_golden.py` + runbooks amendés. À livrer AVANT que le
   flux de feedbacks ne grossisse : c'est le filet sous tous les fixes à venir.
3. **P2** — process hebdo + section Leçons. Démarre à la première itération réelle.
4. **P3** — `NOTE_VALIDEE`, sur déclencheur (≥ 3 cas F5).
