---
name: assynco-erp
description: >-
  Accès en LECTURE aux données d'assurance Assynco (le courtier de NCG) pour une
  copropriété : police(s) souscrite(s), type de contrat (MRI / RCS / PJ), primes,
  statut, dates d'effet/résiliation, assureur, et sinistres (cause, garantie
  impactée, montants, pipeline d'avancement). À utiliser dès qu'une question porte
  sur l'ASSURANCE d'une copro : « quelle est la police de X ? », « quelle couverture /
  garantie ? », « combien de prime ? », « quel assureur ? », « franchise ? »,
  « échéance / résiliation ? », « sinistres en cours ou réglés ? », « est-ce couvert ? ».
  Données LIVE de la base Assynco, distinctes des documents RAG. Read-only (V1).
  NE PAS utiliser pour le contenu d'un document (PV, RCP, contrat scanné) → tools
  documentaires PALIM ; ni pour créer/modifier un dossier (lecture seule en V1).
---

# Assynco — Données d'assurance d'une copropriété (lecture)

Ce skill interroge l'ERP du courtier **Assynco** (base Airtable) **via 3 tools MCP
PALIM**. Toute la mécanique Airtable (filtres, champs liés, pagination) est gérée
côté serveur : tu n'écris **jamais** de `filterByFormula` ni de `baseId` — tu appelles
les tools avec un **code NCG**.

Périmètre V1 (read-only) : **Copropriété + Police + Sinistre**. Pas de création ni de
modification. Les plafonds/capitaux détaillés par garantie ne sont pas exposés (V1).

## Les 3 tools

| Tool | Entrée | Retourne |
|------|--------|----------|
| `PALIM_assynco_get_copro` | `code_ncg` | Fiche copro Assynco : identité, adresse, prime totale, prime MRI, total sinistres, nombre de polices |
| `PALIM_assynco_list_polices` | `code_ncg` | Polices souscrites : `numero_police`, `statut_contrat`, `type_contrat` (MRI/RCS/PJ), primes annuelles, dates effet/résiliation, échéance, **assureur / syndic / courtier** (noms) |
| `PALIM_assynco_search_sinistres` | `code_ncg` (+ `query` optionnel) | Sinistres : libellé, situation, lésé, cause, garantie impactée, franchise, montants (estimation, coût assureur, provisions, total réglé), pipeline 🚦, refs, assureur/expert |

## Workflow obligatoire : résoudre la copro D'ABORD

Les 3 tools exigent un **code NCG** (ex. `"5390"`). Si l'utilisateur ne donne qu'un
nom, une adresse ou un alias :

1. Appeler **`PALIM_list_copros`** (query = le nom/adresse) → choisir le bon `code_ncg`
   parmi les candidats (un alias n'est pas unique ; une même rue peut viser plusieurs
   copros — faire valider si ambigu).
2. Puis appeler le tool Assynco voulu avec ce `code_ncg`.

Ne jamais deviner un code NCG. Sans code résolu, demander à l'utilisateur.

## Comprendre les garanties (important)

Chez Assynco, les champs texte « Garantie » des polices sont le plus souvent **vides**.
Le **type de couverture se lit dans `type_contrat`** :
- **MRI** = Multirisque Immeuble (la couverture principale de la copro)
- **RCS** = Responsabilité Civile Syndic
- **PJ** = Protection Juridique

Les **montants** disponibles en V1 = **primes** (annuelle TTC/HT, dernière quittance) et
**franchises** quand renseignées. Les **plafonds/capitaux par risque** ne sont PAS exposés
(ils vivent dans la table Produit, hors V1) → si on te les demande, dis-le clairement,
n'invente pas de montant de couverture.

## Assynco (live) vs documents RAG : que choisir

- **Faits d'assurance à jour** (police active, prime, assureur, statut, sinistre courant)
  → tools **Assynco** (source temps réel).
- **Contenu d'un document** (texte d'un PV d'AG, d'un RCP, d'un contrat scanné, d'un
  constat) → tools **documentaires PALIM** (`PALIM_search_chunks`, `PALIM_get_full_document`).
- **Dossiers sinistres enrichis RAG** (déjà synchronisés en base, avec rapprochement
  documentaire) → `PALIM_search_dossiers`. Pour le **détail assurance live** d'un sinistre,
  préférer `PALIM_assynco_search_sinistres`.

En cas de divergence entre une source live Assynco et un document RAG, **signaler l'écart**
(date, montant) plutôt que de trancher en silence.

## Rigueur

- N'affirme que ce que les tools renvoient. Cite la source (« d'après Assynco… »).
- Si un champ est vide/`null`, dis « non renseigné », n'extrapole pas.
- Montants : reprends-les tels quels, ne recalcule pas une couverture.
- Lecture seule : si l'utilisateur demande de créer/modifier un dossier, une police ou
  un sinistre, indique que ce n'est pas disponible en V1 (read-only).

## Référence

Modèle de données détaillé (tables, champs, scope) : lire `references/data-model.md`
uniquement si un détail de champ ou de liaison est nécessaire.
