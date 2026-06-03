# Assynco ERP — Data model (périmètre R1 : Copropriétés + Police + Sinistre)

> Base Airtable Assynco : `appi1ee5p93EBHtLR`.
> Généré par introspection (API metadata) le 3 juin 2026.
> R1 = read-only sur 3 tables. La base en compte 34 ; les autres (Quittance, Cotation,
> Organisation, Contacts, Bordereaux, Produit, etc.) sont hors R1 mais documentables plus tard.

---

## Modèle de liaison — Copropriétés est le HUB

Les 3 tables sont reliées par des **record links** centrés sur `🏢 Copropriétés`
(`tblsPUcmAXwWcZFjj`). C'est le point d'entrée du scope, PAS le `FIND("(code)")` historique
de `08_airtable_sync.py` (raccourci texte).

```
🏢 Copropriétés (hub)
   ├─ Polices      (link → ⛱Police)
   ├─ Sinistres    (link → 🏄Sinistre)
   ├─ Quittances, Cotations, Syndic, Gestionnaire ... (hors R1)
⛱Police   ── "Nom du Client ou de la Copropriété LPA" → 🏢 Copropriétés
🏄Sinistre ── "Copropriete"                            → 🏢 Copropriétés
```

**Scope copro (R1)** : résoudre code NCG → record Copropriété via **`{Ref client}="<code>"`**
(le code NCG vit dans `Ref client`, PAS dans `Nom` qui est l'adresse/nom d'immeuble — vérifié
live le 03/06 ; le `(code)` dans `{Name}` ne vaut que pour la table Sinistre). Puis lire les
liens `Polices` / `Sinistres` du record copro (record IDs → `OR(RECORD_ID()=...)`).
Caveat : quelques codes matchent >1 record copro (ex. 5548, doublon Assynco) → on retient le 1er.

**Gotcha linked-fields (pour le futur CRUD)** : en lecture/filtre on manipule le *display value*
(nom) ; en écriture il faut un tableau de *record IDs*. Jamais de nom en write.

---

## Table 🏢 Copropriétés — `tblsPUcmAXwWcZFjj` (PK `Nom`)

Registre copro Assynco. Sert aussi l'annuaire `PALIM_list_copros`.

**Champs exposés (read R1) :**

| Champ | Type | Note |
|---|---|---|
| Nom | singleLineText | PK = adresse / nom d'immeuble (PAS le code NCG) |
| **Ref client** | singleLineText | **= code NCG** (clé de scope : `{Ref client}="5390"`) |
| Numéro d'immatriculation | singleLineText | immat. copro |
| Type de Syndicat | singleSelect | |
| Nombre de copropriétaires | number | |
| Nombre de sinistre | count | rollup |
| Adresse de référence | multilineText | |
| Code Postal / Ville | singleLineText | |
| Année de construction | singleLineText | |
| Surface m2 / Bâtiments / Ascenseurs / Chauffage / Lots habitation - commerce | number/select | descriptif risque |
| SIREN / SIRET | singleLineText | |
| **TOTAL Prime** | rollup | montant agrégé des primes |
| **TOTAL Sinistres** | rollup | montant agrégé sinistres |
| **Prime MRI** | lookup | prime multirisque immeuble |
| Montant restant dues / Montant du fonds de travaux | currency | |
| Clause Renonciation à recours | singleSelect | |
| Date du règlement de copropriété / Date de visite de risque | date | |
| Polices / Sinistres / Quittances / Cotations / Syndic / Gestionnaire | link | navigation |

---

## Table ⛱ Police — `tblNHIMVgw0Xv36u0` (PK `Numéro de Police`)

Contrats d'assurance souscrits. **Source des montants de garanties (R1 = libellés/franchises/primes).**

**Champs exposés (read R1) :**

| Champ | Type | Note |
|---|---|---|
| Numéro de Police | singleLineText | PK |
| Statut Contrat | singleSelect | actif / résilié / ... |
| Type souscripteur / Type d'activité du bâtiment | select/text | |
| Ref client (from Copropriété) | lookup | rattachement |
| Numéro d'immatriculation (from Copropriété) | lookup | |
| **Garantie / Garanties** | singleLineText | **libellés des garanties (texte libre)** |
| **Franchise / Franchise Générale / Franchise DDE** | currency | franchises |
| **Prime Annuelle TTC Souscription** | currency | prime principale |
| Prime Annuelle HT hors frais / Prime TTC Periode Initiale | currency | |
| LAST Prime Quittance TTC | rollup | dernière prime quittancée |
| LCI / Taxe | currency | |
| Total Sinistre | rollup | sinistralité de la police |
| Date Suspension de Garantie | lookup | |
| Date Effet Police / ANNEE EFFET | date/formula | |
| Date de Résiliation | date | |
| Echéance Annuelle / Fractionnement | text/select | |
| Adresse du Risque / Code Postal Risque_ / Ville Risque_ | text | |
| Année de Construction / Usage / Nature | text | |
| Nom du Client ou de la Copropriété LPA | link → Copro | scope |
| Assureur / Syndic / Courtier / Gestionnaire | link → Organisation/Contacts | display value |
| Type Contrat Assurance / Produit | link → Produit | type de contrat |
| Sinistres / Numéro de Cotation | link | navigation |

> **Caveat garanties (vérifié live)** : `Garantie`/`Garanties`/`Franchise*` sont le plus souvent
> **VIDES** chez Assynco. Le vrai signal du type de couverture = **`Type Contrat Assurance`**
> (lien → `Produit` : MRI, RCS, PJ...). R1 expose donc : `type_contrat` (produit résolu en nom)
> + primes + statut + dates + assureur. Les plafonds/capitaux structurés par risque sont dans
> `Produit` — **hors R1** (R1.5 si besoin).

**Champs ignorés (bruit interne)** : boutons, attachments (TMG, DG, FIP, Docs Signés…),
formules de calcul (loss, Estimated Premium 2021…), champs `Field 93/100/105`, colonnes `copy`,
lookups d'affichage redondants.

---

## Table 🏄 Sinistre — `tblvvkhcHZjDyHLdp` (PK `Name`)

Déjà mappée par `08_airtable_sync.py` (~80 champs, `AIRTABLE_FIELDS`) vers la table PostgreSQL
`dossiers`. **Réutiliser cette sélection** comme champs exposés R1, avec un ajout garantie.

**Champs exposés (read R1)** = `AIRTABLE_FIELDS` de `08_airtable_sync.py`, soit notamment :
- Identité/statut : `Name`, `Situation Dossier`, `Statut details`, `Triage`
- Dates : `Survenance`, `Ouverture`, `Clôture`, `Date de déclaration`, `Mission Expert`, `PV`,
  `Depot Rapport`, `Date du Reglement Principal`, `Prescription April`
- Lésé/contacts : `Nom du Lésé`, `Tel Lésé`, `Email Lésé`, `Appt d'origine`, `Nom Gestionnaire syndic`
- Cause/garantie : `Cause`, `IRSI`, `Cause DDE Identifiée/Réparée`, **`Garantie Impactée`**,
  **`Franchise`**, **`Plafond`** (← ajouter `Plafond`, absent de la liste 08)
- Financier : `Estimation`, `Coût Assureur`, `Provisions Assureur`, `Règlement Réalisé`,
  `Recours En Cours/Réalisé`, `Coût Client`, `💸 Total Réglé`, `💸Honoraire de Syndic`
- Pipeline 🚦 : `🚦Déclaration`, `🚦Expertise`, `🚦 Accord`, `🚦Règlement`, `🚦 Etat - Mise en Cause`
- Références : `Ref Cie`, `Ref Expert`, `Ref Sinistre Client`
- Textes : `Circonstances`, `Dommages`, `Conclusion de l'expert`, `Commentaire Assynco`
- Lien copro : `Copropriete` (→ Copropriétés)

> Différence vs la table DB `dossiers` : `08` est un **sync batch**. Le tool MCP live lit
> Airtable directement (données fraîches), même sélection de champs + `Plafond`.

---

## Filtres usuels (filterByFormula)

- Par copro (R1, recommandé) : `{Ref client}="5390"` sur Copropriétés → suivre les liens
  `Polices`/`Sinistres` (`OR(RECORD_ID()=...)`).
- Par copro sur Sinistre directement (legacy 08) : `FIND("(5390)",{Name})` (le code est dans le Name des sinistres).
- Par statut police : `{Statut Contrat}="Actif"` (display value du singleSelect).
- Par date (fiable) : `IS_SAME({Date Effet Police}, "2025-01-01", "day")` — éviter la comparaison directe.
- Recherche texte insensible casse : `FIND(LOWER("tariel"), LOWER({Nom})) > 0`.
