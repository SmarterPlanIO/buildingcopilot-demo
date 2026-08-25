# PLAN — Streamlit agentique (Option A) : PALIM complet sans compte Claude Teams

> Objectif : offrir dans l'app Streamlit la meme intelligence que le PALIM Claude Teams
> (boucle agentique + instructions v3.3 + 5 skills), en reutilisant le serveur MCP
> deploye comme unique backend. Redige le 25/08/2026, AVANT tout code (validation Thai).
> Contexte : le client NCG ne peut pas ajouter le connecteur (verrou Owner) ; Streamlit
> redevient le produit de transition. La voie connecteur d'organisation reste l'atterrissage.

## 0. Principes non negociables

- **Un seul backend** : l'app appelle le serveur MCP NCG en HTTP (les 13 tools). Aucune
  logique de retrieval dupliquee. Parite Claude Teams <-> Streamlit par construction.
- **Regle 3.1 renforcee** : `streamlit_app.py` = UI. La boucle vit dans `agent.py`,
  le transport dans `mcp_client.py`, les skills dans `skills.py`.
- **Le pipeline actuel reste** derriere un toggle (mode "classique") : outil de debug
  et filet de non-regression. Le mode agent devient le defaut.
- **v3.3 reste la source de verite** des instructions. Le prompt systeme de l'app en est
  une instanciation derivee, marquee comme telle, regeneree a chaque bump de version.

## 1. Nouveaux fichiers (tous dans `Scripts/Streamlit Cloud/`)

| Fichier | Role |
|---|---|
| `mcp_client.py` | Client MCP streamable-HTTP minimal (requests) : `initialize`, `tools/list`, `tools/call`. URL+slug depuis `st.secrets["mcp"]["url"]` (jamais en dur). Parse les reponses SSE (`data:` lines). Timeout 60 s/appel, 1 retry. |
| `agent.py` | Boucle agentique Bedrock **Converse API** (`eu.anthropic.claude-sonnet-4-6`, streaming). toolConfig construit **dynamiquement** depuis `tools/list` (le contrat suit le serveur) + pseudo-tool `charger_skill`. Garde-fous : max 8 iterations d'outils par tour, max_tokens 4096, cumul cout/latence, trace Langfuse (span par tool call). |
| `skills.py` | Charge `skills_bundle/ncg/` : parse le frontmatter de chaque SKILL.md, expose (a) la section "skills disponibles" (nom + description) injectee au prompt systeme, (b) `charger_skill(nom)` = SKILL.md complet + fichiers `references/` concatenes (les gabarits de redaction-livrable en font partie). |
| `skills_bundle/ncg/` | Copie embarquee des **5 skills** (ncg-redaction-livrable, ncg-note-juridique, ncg-fiche-decision, ncg-analyse-portefeuille, assynco-erp dezippe) + `instructions_system.md`. |
| `skills_bundle/ncg/instructions_system.md` | Instanciation de INSTRUCTIONS v3.3 pour l'app (voir section 2). En-tete : "derive de v3.3 (2026-08-24) — regenerer a chaque release des instructions". |

Modifies : `streamlit_app.py` (integration UI, section 3), `VERSION` (0.8.0),
`requirements.txt` (rien a ajouter : requests/boto3/langfuse deja presents).

## 2. Adaptation des instructions v3.3 vers le prompt systeme (les SEULS ecarts)

Tout le reste est repris verbatim (Blocs 1-8, 10-13 : axes, garde-fous, sourcage,
visite 3D, analytique, perimetres nommes).

1. **Bloc 0 (versioning)** : pas de ligne en fin de premier message ; la version s'affiche
   en permanence dans la sidebar ("Assistant Copro NCG v3.3-app / harness 0.8.0").
2. **Bloc 9 (feedback)** : remplace par "ne sollicite jamais de feedback : l'interface a
   ses boutons". Les pouces UI existants continuent d'ecrire dans Langfuse. Le tool
   `PALIM_log_feedback` reste expose si l'utilisateur formule un retour spontane.
3. **Export Word** : le bouton Word existant (`_build_docx`) reste le canal d'export ;
   l'agent produit le livrable en markdown dans la reponse (gabarits du skill), et
   l'instruction "propose l'export Word" renvoie au bouton.
4. **Perimetre pre-selectionne** : si l'utilisateur a coche des copros dans le
   multiselect, elles sont injectees en tete de prompt ("perimetre impose : codes X, Y —
   scope tous les appels dessus, sauf question analytique parc entier explicite").
5. **Mecanique skills** : paragraphe decrivant `charger_skill` — "quand un signal de
   l'Axe 2 matche un skill, appelle charger_skill AVANT de repondre ; le contenu charge
   fait autorite". Fidele a la semantique Claude Teams (description toujours visible,
   corps charge a la demande).

## 3. Integration UI (`streamlit_app.py`)

- Toggle sidebar "Mode agent (defaut) / Mode classique". Le mode classique = code actuel
  intact. Persistance dans la session sauvegardee.
- Rendu : streaming du texte (les pauses aux tool calls sont normales — spinner d'etape
  "je consulte les documents..." par nom metier, jamais le nom du tool, coherent Bloc 3).
- Activite agent : expander discret "Recherches effectuees" (n appels, copros touchees,
  duree) — pour la confiance utilisateur et le debug, sans jargon.
- Reutilises tels quels : historique de chat multi-sessions, boutons feedback (trace_id =
  trace Langfuse de l'agent), bouton Word, linkify si l'agent cite [N] en mode source.
- Le panneau "Sources" du mode classique n'existe pas en mode agent : le sourcage suit le
  Bloc 10 (a la demande, tableau dans la reponse). Assume et documente.

## 4. Secrets et IAM

- Ajouter dans les secrets Streamlit Cloud : `[mcp] url = "https://<lambda>/<slug>"`.
  Le slug ne sera PAS dans le repo (audit : grep avant commit).
- IAM : aucun changement. Converse/ConverseStream utilisent les memes actions
  `bedrock:InvokeModel*` que l'existant (user PALIM-app).
- Langfuse : projet existant, tag `mode:agent` pour separer des traces classiques.

## 5. Sequence de realisation (ordre strict, commit + merge main a chaque palier)

- **P0 — socle (0,5 j)** : `mcp_client.py` + test live (initialize, tools/list == 13,
  search_chunks smoke sur 5757) ; `skills_bundle/` + `skills.py` + tests de parsing ;
  `instructions_system.md` v1. Critere : `pytest tests/test_mcp_client.py
  tests/test_skills_bundle.py` vert en local contre le serveur prod.
- **P1 — boucle (1 j)** : `agent.py` hors UI (CLI de test : question -> reponse complete
  avec tool calls traces). Criteres : question factuelle scopee OK ; question juridique
  charge ncg-note-juridique ; question "pole Rodin" resolue via Bloc 13 ; question
  analytique parc entier appelle PALIM_run_analytical_query ; jamais plus de 8 iterations.
- **P2 — UI (0,5-1 j)** : toggle + streaming + expander + branchements feedback/Word.
  Critere : les 2 modes coexistent, sessions sauvegardees compatibles.
- **P2bis — pieces jointes (0,5-1 j, VALIDE sur le principe 25/08 soir)** : upload
  Word/PDF/Excel dans le prompt. `st.chat_input(accept_file=True)` ; extraction
  python-docx (present) + pypdf + openpyxl (a ajouter) ; texte injecte en bloc
  "[Document joint : nom]" tronque 30-50k chars + cachePoint messages ; extension
  Bloc 14 : TOUJOURS distinguer "d'apres la piece jointe" vs "d'apres les documents
  de la copropriete" (Bloc 4/sourcage) ; PDF scanne (couche texte vide) = EXCLU v1,
  message honnete (OCR Textract = option v2) ; scenario de recette supplementaire :
  croisement piece jointe x RAG ("ce devis est-il coherent avec le vote d'AG ?").
- **P3 — recette (0,5 j)** : les 8 scenarios (section 6) + le scenario piece jointe
  passes en local, ajustements prompt.
- **P4 — deploy** : VERSION 0.8.0, merge main, secret `[mcp]` pose dans Streamlit Cloud,
  smoke prod, verification affichage version sidebar. Rollback = toggle mode classique
  (2 clics) ou revert du merge.

Total estime : **2,5 a 3,5 jours dev**. P0+P1 faisables avant mardi si besoin demo.

## 5bis. Etat d'avancement (MAJ 26/08 : P2+P2bis LIVRES, commit 4b659fd, v0.8.0)

P2+P2bis commites et pushes sur main. Smoke UI local complet SAUF pouces feedback
(le Python 3.14 local casse l'import langfuse — connu, prod = 3.12, meme code que
le mode classique). Le deploy main SANS secret [mcp] laisse l'app en mode
classique : fail-safe voulu, zero impact utilisateur.
RESTE : P3 recette (8+1 scenarios sur l'app) ; P4 = poser le secret `[mcp] url`
dans Streamlit Cloud (active le mode agent), verifier les pouces feedback en
prod, recette pieces jointes dans l'UI navigateur (non testable en headless).

### Reperes P2 d'origine (conserves pour histoire)

P0 livre (3e08cd7) et audite propre ; P1 livre (ec035db), 4/4 tests live, cout
0,05-0,10 $/question avec prompt caching. P2 EN COURS, reperage fait dans
`streamlit_app.py` (2959 lignes) :
- Toggle "Mode agent" : sidebar, pres du multiselect copros (~l.2250) + caption
  version ("Assistant Copro NCG v3.3-app").
- Branche agent : dans `if user_input:` (~l.2530+), APRES la creation de trace
  Langfuse et le filtre hors-sujet `classify_prompt_relevance` (garde le filtre
  Haiku, il economise un tour Sonnet), AVANT la route analytique — le mode agent
  court-circuite route analytique + strategie Haiku + pipeline classique, puis
  `st.stop()`.
- Glue UI = fonction `_run_agent_turn(user_input, copro_filter, _trace)` fine :
  st.status avec labels metier via on_step, appel `agent.run_agent` (history =
  chat_history mappe en [{role,text}], derniers ~6 tours), append chat_history
  au format existant {"role","content","source_count":0,"n_displayed":0,
  "trace_id": trace.id} (compatible sessions sauvegardees + feedback +
  render_action_buttons via le rendu historique).
- MCP_URL : st.secrets["mcp"]["url"] en try/except (regle 3.6) ; si absent ->
  warning + repli mode classique (fail-safe).
- Streaming texte : soit converse_stream a assembler dans agent.py (evenements
  contentBlockDelta text/toolUse input), soit st.status seul en premiere passe.
- VIOLATION PREEXISTANTE reperee (pas a moi de la corriger en douce) :
  `st.secrets["aws"].get(...)` a la l.58 de streamlit_app.py, contraire a la
  regle 3.6 — signalee a Thai le 25/08.
- Clone partage avec l'autre agent (HEAD change de branche sans prevenir) :
  committer puis `git push origin <branche-courante>:main`, verifier ensuite
  `git merge-base --is-ancestor <sha> origin/main`.

## 6. Recette (8+1 scenarios) — RESULTATS P3 (26/08, app PROD palim-demo)

| # | Scenario | Resultat | Ou |
|---|---|---|---|
| 1 | Factuel scope (5757) | **PASS** (reponse sourcee PV signe, widgets complets) | UI prod + UI locale + CLI |
| 2 | Perimetre nomme pole Rodin | **PASS** (codes 5750/5784/5440 auto) | CLI live (pytest 4/4) |
| 3 | Juridique -> skill charge avant | **PASS** | CLI live (pytest 4/4) |
| 4 | Fiche de decision | couvert par le mecanisme skill (idem 3), non rejoue | — |
| 5 | Analytique parc entier | **PASS** (couverture 19/19 annoncee) | CLI live (pytest 4/4) |
| 6 | Assynco live | **PASS** (5750/5784/5440 polices+sinistres) | CLI live (scenario Rodin) |
| 7 | Sourcage a la demande | **PASS apres fix `ef64421`** (5 marqueurs S, tableau, fidele) — le filtre hors-sujet Haiku rejetait le suivi : branche agent deplacee AVANT le filtre | UI prod |
| 8 | Etancheite (jargon/tools) | **PASS** sur tous les messages observes | UI prod + CLI |
| 9 | Piece jointe (croisement devis x AG) | **PASS** cote moteur (CLI --fichier, attribution stricte) ; upload UI = a verifier a la main (boite de dialogue OS non automatisable) | CLI live |

En plus : pouces feedback **PASS en prod** (clic 👍 -> « ✓ », ecrit Langfuse), secret
[mcp] actif (placeholder pieces jointes + upload visibles), multi-tours PASS.
RESTE A LA MAIN (Thai, 5 min) : upload d'un fichier via le navigateur, toggle
sidebar mode classique (sidebar repliee non pilotable en headless).
OBSERVATION qualite (backlog retrieval, pas un bug P2/P3) : variance sur
« derniere AG 5757 » — certains runs citent le PV signe du 17/06/2026, d'autres
affirment « le plus recent = 15/03/2022 ». Meme backend que la version Claude ;
a traiter cote retrieval (boost recence / requete date) si ca se reproduit.

### Definition d'origine des scenarios

1. Factuel scope : "derniere AG de la 5757 ?" -> reponse sourcee, 1-2 tool calls.
2. Perimetre nomme : "sinistres sur le pole Rodin" -> codes 5750/5784/5440 auto, annonce en mots.
3. Juridique : "quelle majorite pour changer le reglement ?" -> skill ncg-note-juridique charge, rappel validation juriste.
4. Fiche de decision : "compare les devis toiture de la 5750 pour decider" -> skill fiche-decision, gabarit respecte.
5. Analytique parc : "combien de sinistres au total par copro ?" -> run_analytical_query, couverture annoncee.
6. Assynco live : "la police de la 5750 ?" -> assynco_list_polices, donnees ERP.
7. Sourcage a la demande : "tes sources ?" apres le scenario 1 -> republication annotee + tableau, verbatim depuis text.
8. Etancheite : aucun nom de tool ni jargon dans TOUTES les reponses ; question hors perimetre -> refus propre.

## 7. Risques et decisions assumees

| Risque | Traitement |
|---|---|
| Derive instructions app vs v3.3 | en-tete "derive de", regeneration a chaque bump, verif en recette. Automatisation (script d'instanciation) = hors scope, note pour plus tard |
| SSE/session du serveur MCP (stateless_http) | teste en P0 en premier ; le harness curl du 25/08 a deja valide initialize + tools/list en HTTP pur |
| UX streaming hachee par les tool calls | spinners d'etape en langage metier ; assume |
| Cout Bedrock transfere a SmarterPlan | ~0,01-0,05 $/question estime ; suivi Langfuse par trace ; a revoir si >100 questions/jour |
| Skills zippes non versionnes (assynco-erp) | le bundle embarque la version dezippee, source = mcp_server/skills ; noter la duplication dans AGENTS.md |
| Multi-client | bundle par client (`skills_bundle/<client>/`) ; Delacour = instancier plus tard, hors scope ici |
