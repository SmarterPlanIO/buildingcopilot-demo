# Retour de test Delacour du 21/09 — état d'instruction et projet de réponse

> Instruit le 22/09/2026 sur pièces : profil client, base Delacour via le connecteur MCP
> (25 copros, 21 avec PV_AG), Drive partagé Delacour, Airtable Assynco (live).
> Légende : ✅ établi · 🔧 corrigé par le patch 06B (à déployer) · ⏳ à faire · ❓ à trancher.

## 1. Couverture — 25/31 immeubles

✅ Établi. Les 25 indexés sont exactement les 25 du profil ; les 5 manquants (Coubertin,
Pasteur Saint-Ouen, Quai de Grenelle, Blomet, Alma Courbevoie) n'ont jamais été présentés
au pipeline. Le handoff en connaît 4 avec immatriculation (Coubertin `AB8763831`, Alma
`AD1265248`, Pasteur `AC8312977`, Blomet `AC1168715`) ; **le 23 quai de Grenelle est
nouveau** — immatriculation à obtenir. Croix-Nivert 114 : à ajouter en octobre, syndicat
distinct du 208 (`AC5645007`), déjà indexé.

⏳ Chantier séparé : `add_copro.py` × 5 puis `ingest.py` (extraction + Haiku, coût LLM à
estimer sur le volume des 5 dossiers Drive). Préalable : vérifier que les 5 dossiers
existent dans le Drive partagé et relever leurs immatriculations RNIC.

**Réponse client :** « Confirmé : ces cinq immeubles ne sont pas dans le périmètre indexé.
Pouvez-vous nous confirmer l'immatriculation RNIC du 23 quai de Grenelle et que les
dossiers de ces cinq immeubles sont bien dans le Drive partagé ? Nous les intégrerons en
un lot, puis le 114 Croix-Nivert à l'ouverture du mandat. »

## 2. Identité du 67 rue Escudier (`AH7171655`)

✅ Cause prouvée : le registre annuaire n'écrivait que code + immatriculation ; le nom
retombait sur le dossier Drive « SDC - 92100 ». 🔧 Corrigé (C1) : nom et adresse
« SDC 67 rue Escudier, 92100 Boulogne-Billancourt » écrits depuis le profil, pour les 25.

**Réponse client :** « Défaut de notre côté (libellé non renseigné dans l'annuaire),
corrigé au prochain rechargement : l'immeuble apparaîtra sous “SDC 67 rue Escudier, 92100
Boulogne-Billancourt”. Le dossier Drive garde son nom “SDC - 92100” ; nous n'y touchons pas. »

## 3. Filtre de l'annuaire + Nocard sans immatriculation

✅ Reproduit le 22/09 : `PALIM_list_copros("Escudier")` renvoie les 25. Deux causes
distinctes :
- « Escudier » ne matchait rien (cf. 2) et l'outil renvoie la liste complète quand aucun
  score ne matche — comportement voulu à l'origine, mais trompeur. 🔧 Le nom sera juste
  après C1 ; ⏳ le « liste vide au lieu de liste complète » est prévu image v13.
- Nocard (`AJ6978050`) : ✅ ligne de registre jamais écrite le 27/08 (échec avalé par un
  `try/except`). 🔧 Corrigé (C2) : l'upsert est bloquant et un invariant de recette
  (I0b) l'attrape désormais.

**Réponse client :** « Deux corrections : le libellé (cf. 2) et l'immatriculation du 9 rue
Edmond Nocard sont rétablis au prochain rechargement. Le comportement “liste complète
quand rien ne correspond” sera remplacé par une liste vide dans la prochaine version du
connecteur. »

## 4. Procès-verbaux d'assemblée

**4a. Aucun PV pour Exelmans, Félix Faure, Versailles, Nocard** — ✅ confirmé en base
(0 document PV_AG pour ces 4 codes, 21 copros sur 25 en ont).

Réponse à la question « écartés ou jamais présentés » — instruction du 22/09 :

| Copro | Ce que contient l'index | Ce que montre le Drive | Lecture |
|---|---|---|---|
| Exelmans `AB8546467` (139 docs) | RCP, comptabilité, mutations, une **feuille de présence du 20/05/2026** | dossier Lobby standard (Archives/Courriers/Gestion/Comptabilité/Communication/GARDIEN) | une AG a eu lieu le 20/05/2026, son PV n'est pas dans le Drive |
| Félix Faure `AC9872896` (315 docs) | convocation + formulaire de vote **AGO 30/06/2026** (dans un dossier de procédure d'impayé), RCP | idem | le PV de l'AGO du 30/06/2026 n'est pas dans le Drive |
| Versailles `AE1302603` (63 docs) | RCP, factures, **feuille de présence AG 10/03/2026**, un modificatif RCP 2013 qui reprend des résolutions d'AG 2012 | 34 fichiers à la racine, aucun PV | idem, AG du 10/03/2026 sans PV |
| Nocard `AJ6978050` (11 docs) | RCP, factures fluides, une feuille de présence, un courrier AXA | dossier Lobby standard | aucun PV |

✅ Tranché le 23/09 sur les `filtrage_rapport.json` des 4 copros (tout ce que 01 a vu) :
- Versailles, Nocard : **aucun fichier** ressemblant à un PV n'a été présenté → absent du Drive.
- Félix Faure : l'AGO du 05/11/2025 et celle du 30/06/2026 ont laissé convocations, pouvoirs,
  formulaires de vote et **attestations de non-recours** (donc les PV existent) — mais aucun
  PV n'est dans le Drive.
- Exelmans : 4 fichiers `Courrier dossier PV dAG 20 Mai 2026 (n).gdoc` — des **Google Docs**
  (courriers d'envoi du PV, pas le PV). Le format `.gdoc` n'est pas ingéré par le pipeline
  (pointeur Drive, pas un fichier) : c'est le seul cas « écarté », et il ne contient pas le
  PV lui-même.

Conclusion : **jamais présentés**, pour les 4. Action côté Delacour : déposer les PV en PDF.
⏳ Côté pipeline : mesurer la part de `.gdoc`/`.gsheet` dans le Drive Delacour (Lobby génère
ses courriers en Google Docs) et décider d'un export Drive API → PDF à l'ingestion.

**4b. Classement par dossier Drive (PV Escudier en « Comptabilité »)** — ✅ cause prouvée :
le type corrigé par l'analyse documentaire existait bien (`PV_AG`) et servait au filtre de
recherche, mais pas à l'étiquette affichée dans les citations. 🔧 Corrigé (C3), 15
documents Delacour concernés.

**Réponse client (après §8 du runbook) :** « Sur ces quatre immeubles, l'index contient les
feuilles de présence et convocations des dernières AG (20/05/2026 Exelmans, 30/06/2026
Félix Faure, 10/03/2026 Versailles) mais aucun procès-verbal : ils ne figurent pas dans les
dossiers du Drive partagé, ils n'ont donc pas été écartés par l'indexation. Il faut agir
côté Drive : dès qu'ils y sont déposés, ils sont repris à l'ingestion suivante. Pour le
classement du PV du 25/03/2026 d'Escudier en “Comptabilité” : défaut d'affichage de
notre côté, corrigé au prochain rechargement. »

## 5. Lien Assynco

✅ Le rattachement **est effectif** : `PALIM_assynco_get_copro(Vaneau)` renvoie la fiche
Airtable Assynco (record `rec8j3WI4ACEYlwra`, immatriculation `AA6219950`,
`nb_polices_liees = 0`, `total_prime = 0`), et `search_sinistres(100 Victor Hugo)` renvoie
un sinistre DDE du 29/08/2024 (expertise réalisée, 8 338 €). Donc :
- « aucune police » = **aucune police rattachée à ces copros dans la base Assynco**, pas un
  défaut de liaison. À faire trancher par Assynco (Philippe) : les contrats Delacour
  sont-ils saisis dans Airtable et liés aux fiches copro ?
- « erreur interne » sur Victor Hugo = très probablement le `TimeoutError` Airtable connu
  (3 occurrences en 21 jours), prévu image v13 ; l'appel refait le 22/09 a répondu.

**Réponse client :** « La liaison avec la base Assynco fonctionne (vos immeubles y sont
reconnus par immatriculation ; un sinistre du 100 bd Victor Hugo remonte). Ce qui manque,
ce sont les polices elles-mêmes : elles ne sont pas encore rattachées à vos immeubles dans
l'outil du courtier — nous le voyons avec Assynco. L'erreur ponctuelle sur Victor Hugo est
un dépassement de délai côté Airtable, connu et traité dans la prochaine version. »

## 6. À venir — synchronisation automatique Drive → base (« coming soon », ajouté le 23/09)

⏳ État réel : `ingest.py` gère déjà les **ajouts** et les **suppressions** (01 rebâtit depuis
la source vivante, 06b retire les chunks des documents disparus), mais **pas les
modifications sur place** (checkpoint 02 par chemin, pas par contenu — limite V1 documentée
dans le driver), et rien n'est planifié : chaque ingestion est manuelle, depuis le laptop
(seul poste qui monte le Drive partagé).
Chantier **06E** : (a) hash de contenu dans le checkpoint 02 ; (b) ordonnanceur sur
palim-louise avec accès au Drive partagé (rclone ou API Drive — brique commune avec 06D) ;
(c) fréquence à définir avec le client (quotidienne, nuit).

**Réponse client :** « Aujourd'hui, la base est mise à jour à notre initiative, à chaque
campagne d'ingestion. Nous préparons sa mise à jour automatique à fréquence régulière à
partir de votre Drive partagé : les documents ajoutés, modifiés ou supprimés dans vos
dossiers seront répercutés dans l'assistant sans intervention de votre part ni de la nôtre.
Les procès-verbaux du point 4, par exemple, seront repris dès leur dépôt. Nous vous
préciserons le calendrier et la fréquence retenue lors de la prochaine session. »

## Ordre de traitement proposé

1. ✅ Patch 06B + rerun 25 copros (22/09) → règle 2, 3, 4b.
2. ✅ Runbook §8 → 4a tranché (23/09) ; envoi de la réponse client.
3. Mail à Assynco (polices Delacour) → 5.
4. Image v13 (filtre annuaire, citation `copro`, timeout Assynco) → reste de 3 et 5.
5. Lot « 5 copros manquantes » (+ Croix-Nivert 114 en octobre) → 1.
6. 06C (`.doc`, 38 % de Didot), puis 06E (sync périodique) et 06D (`.gdoc`) qui partagent
   l'accès Drive depuis palim-louise → 6.
