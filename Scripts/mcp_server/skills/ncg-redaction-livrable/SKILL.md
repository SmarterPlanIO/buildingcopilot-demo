---
name: ncg-redaction-livrable
description: >-
  Met en forme un livrable écrit pour un gestionnaire de copropriété NCG : note
  interne structurée, courrier aux copropriétaires, note au conseil syndical,
  email à un prestataire, ou export Word. À utiliser quand l'utilisateur demande
  de rédiger / écrire un courrier, une note, un email, un compte-rendu ou un
  document « prêt à l'envoi », ou de mettre une réponse en Word. NE PAS utiliser
  pour une réponse factuelle courante, une recherche ou une question triviale.
  Skill de mise en forme : il rend propre un contenu déjà recherché et sourcé via
  les tools PALIM ; il n'invente jamais de fait pour remplir un gabarit.
---

# Rédaction de livrable — Assistant Copro NCG

Ce skill transforme un contenu **déjà recherché et sourcé** (via les tools PALIM)
en un livrable écrit propre. Ce n'est PAS un skill de recherche : si un fait
manque, retourne d'abord aux tools (`PALIM_search_chunks`, `PALIM_get_full_document`,
`PALIM_search_dossiers`) avant de rédiger. Ne remplis jamais un gabarit par
extrapolation.

## 0. Préconditions (garde-fous, non contournables)
- **Périmètre** : la/les copro(s) concernée(s) sont identifiées (code NCG). Sinon, reviens au workflow de scope avant de rédiger.
- **Sources** : chaque résolution, montant, date, clause, nom cité provient d'un document retourné par un tool ou fourni dans le prompt.
- **Destinataire fixé** (Axe 1 des Project Instructions). Si une rédaction externe n'a pas de destinataire explicite, pose une seule question fermée (« note interne ou envoi externe ? ») avant de commencer.
- **Jamais d'assertion non sourcée dans un livrable externe.** Un élément encore `[À VÉRIFIER]` n'entre pas dans un envoi : soit tu le résous (retour aux tools), soit tu le retires de la version externe et le réserves à la version interne.

## 1. Choisir le type de livrable
| Type | Quand | Gabarit (templates.md) |
|---|---|---|
| Note interne structurée | analyse/synthèse pour le gestionnaire | `note_interne` |
| Courrier aux copropriétaires | information/convocation/relance externe | `courrier_externe` |
| Note au conseil syndical | point ou recommandation au CS | `note_conseil_syndical` |
| Email à un prestataire | demande/relance fournisseur | `email_prestataire` |

Charge le gabarit correspondant depuis `templates.md`.

## 2. Rédiger
- Style sobre, factuel, FR professionnel. Une idée par paragraphe. Puces pour les listes, numérotation pour les procédures.
- Cite les résolutions d'AG et clauses de RCP au plus près (entre guillemets) avec le document source.
- **Analyse juridique incluse** : distingue « documents de la copro » vs « cadre légal général », et termine par le rappel que la validation par le syndic / un juriste est requise.

## 3. Nettoyage selon le destinataire
- **Externe** : retire tout jargon interne (code_ncg, source_file, doc_type, « chunk », score, « le RAG ») et tout `[À VÉRIFIER]` brut. Aucune approximation : si un point n'est pas sourcé, il ne figure pas.
- **Interne** : tu peux conserver les références techniques et les `[À VÉRIFIER]`.

## 4. Traçabilité des sources (en italique discret, fin de section)
Pour chaque assertion technique, chiffrée ou juridique :
- (a) Document de la copro : « Source : PV AG du 14/03/2024, résolution n°7, copro 5390. »
- (b) Document chargé : « Source : contrat ascenseur, document complet chargé. »
- (c) Inférence : « Hypothèse à valider : déduit du devis du 16/02/2024, à confirmer. »
- (d) Cadre légal général : « Cadre légal (à valider) : art. 25 loi du 10/07/1965, non spécifique à cette copro. »
Sur un livrable **externe**, la traçabilité reste sobre et n'expose aucun identifiant interne (pas de code_ncg, pas de source_file) ; on cite le document par sa nature et sa date (« PV de l'AG du 14/03/2024 »).

## 5. Compteur de cohérence (note interne uniquement)
En fin de **note interne structurée**, énumère : nombre d'assertions factuelles/juridiques, dont X sourcées sur un document et Y en `[À VÉRIFIER]`. Si plus de 20 % sont en `[À VÉRIFIER]`, **alerte explicitement** que la note n'est pas exploitable en l'état. Ce compteur ne figure **jamais** sur un livrable externe.

## 6. Proposer l'export Word
Après une rédaction externe structurée, propose en **une seule** question fermée :
« Souhaites-tu que je rédige ce courrier / cette note dans un document Word prêt à l'envoi ? »
- Si oui : génère un `.docx` propre (titres hiérarchisés Heading 1/2/3, listes à puces et numérotées, tableaux Word natifs), **sans aucun élément interne**. Propose-le en téléchargement.
- Si refus ou pas de réponse : laisse en l'état pour copier-coller. Ne relance pas.

## 7. Conventions de rédaction syndic (rappels)
- Un syndic n'engage jamais la copropriété sur un montant, une décision de travaux ou une échéance sans décision d'AG correspondante : si le livrable l'implique, renvoie à la résolution d'AG ou marque la validation requise.
- Mentions d'usage d'un courrier : objet clair, référence de la copropriété, date, formule de politesse adaptée au destinataire (copropriétaires / conseil syndical / prestataire).
- Ne présente jamais un devis comme une décision, un diagnostic comme un vote, un courrier comme un PV.
