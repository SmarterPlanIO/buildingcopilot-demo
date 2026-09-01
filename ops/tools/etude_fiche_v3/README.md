# Étude fiche de synthèse v3 (01/09/2026) — scripts de mesure

Scripts READ-ONLY qui ont produit les mesures T1-T26 de `Scripts/PLAN_FICHE_SYNTHESE_V3.md`.
Rejouables pour vérifier ou actualiser une hypothèse avant de coder.

| Script | Mesures | Prérequis |
|---|---|---|
| `etude_questions.py` | T1 : 193 vraies questions (Langfuse + harness), classification par type | `LF_PK`/`LF_SK`/`LF_HOST` (env de la Lambda `palim-mcp`) + `DB_PASSWORD` |
| `etude_mesures.py` | T2-T10 : métadonnées, indéterminées, dossiers, doublons PV, fraîcheur, contrats, mandat, Assynco | `DB_PASSWORD` (`palim/ragadmin`) |
| `etude_mesures2.py` | T11-T14 : questions AG indexables, chronologie travaux, richesse dossiers, LEMEAU | idem |
| `etude_mesures3.py` | T20-T23 : anatomie des indéterminées, gap « adoptée », taille index dossiers, résumés PV | idem |
| `etude_mesures4.py` | T24-T26 : index thématique 5757, proxy contrats en vigueur, dates Assynco | idem |
| `etude_t19.py` | T19 : séquences d'appels autour de `copro_overview` (Langfuse) | clés Langfuse |

Lancer depuis `Scripts/` avec `PYTHONIOENCODING=utf-8`. Aucune écriture en base.
