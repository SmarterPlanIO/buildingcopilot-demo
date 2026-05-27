"""
analytics.py — Route analytique (agrégations SQL multi-copro)

Importé par streamlit_app.py. Indépendant du framework UI (comme dossiers_api.py).

Principe de sécurité (production-ready) :
  Le LLM ne génère JAMAIS de SQL brut. Il mappe la question vers une SPEC JSON
  sur liste blanche (detect_analytical_query). Un builder déterministe traduit
  cette spec en SQL PARAMÉTRÉ (build_analytical_sql). Le résultat agrégé est
  formaté par le LLM (run_analytical_route). Le LLM n'intervient qu'aux deux
  bouts (comprendre / mettre en forme), jamais sur les données.

Propriété : la route est identique pour 1, 10 ou 150 copros (GROUP BY code_ncg
  si copro_filter=None, sinon WHERE code_ncg=...). Aucun plafond, aucune
  sélection manuelle obligatoire.

Phase 1 : sources `documents` et `dossiers` (champs déjà structurés en base).
Phase 2 (à venir) : source `prestataires` (typage métier).
"""
import json
import re
from typing import Optional, Tuple, List, Dict, Any


# ──────────────────────────────────────────────────────────────
# LISTE BLANCHE — seules ces colonnes/opérations sont autorisées.
# Toute valeur hors de ces dicts → spec rejetée → fallback retrieval.
# Les clés de `filters` sont les champs de la spec ; la valeur est
# (expression SQL fixe, opérateur). La valeur du filtre est paramétrée.
# ──────────────────────────────────────────────────────────────
WHITELIST: Dict[str, Dict[str, Any]] = {
    "documents": {
        "table": "documents",
        "filters": {
            "doc_type":   ("COALESCE(doc_type_corrige, doc_type)", "="),
            "sous_type":  ("sous_type", "="),
            "statut":     ("statut", "="),
            "annee":      ("annee", "="),
            "annee_min":  ("annee", ">="),
            "annee_max":  ("annee", "<="),
        },
        # operation=list : champ à énumérer
        "list_fields": {
            "nom_fichier": "nom_fichier",
            "sous_type":   "sous_type",
            "doc_type":    "COALESCE(doc_type_corrige, doc_type)",
            "partie":      "__UNNEST_PARTIES__",  # cas spécial (UNNEST parties_concernees)
        },
        # operation=sum : métrique à sommer
        "sum_metrics": {
            "montant_principal": "montant_principal",
        },
    },
    "dossiers": {
        "table": "dossiers",
        "filters": {
            "type_dossier": ("type_dossier", "="),
            "statut":       ("statut", "="),
            "annee":        ("EXTRACT(YEAR FROM date_ouverture)::int", "="),
            "annee_min":    ("EXTRACT(YEAR FROM date_ouverture)::int", ">="),
            "annee_max":    ("EXTRACT(YEAR FROM date_ouverture)::int", "<="),
        },
        "list_fields": {
            "nom_dossier":  "nom_dossier",
            "type_dossier": "type_dossier",
            "assureur":     "assureur",
            "expert_nom":   "expert_nom",
        },
        "sum_metrics": {
            "montant_estime":    "montant_estime",
            "montant_reel":      "montant_reel",
            "total_regle":       "total_regle",
            "provisions":        "provisions",
            "franchise":         "franchise",
            "reglement_realise": "reglement_realise",
            "cout_client":       "cout_client",
        },
    },
}

_MAX_LIST_ROWS = 5000      # garde-fou SQL sur les listes
_MAX_ROWS_TO_LLM = 300     # lignes max passées au LLM pour formatage


# ──────────────────────────────────────────────────────────────
# Helper Bedrock
# ──────────────────────────────────────────────────────────────
def _invoke(bedrock, model: str, system: str, user: str, max_tokens: int) -> str:
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    })
    resp = bedrock.invoke_model(
        modelId=model, body=body,
        contentType="application/json", accept="application/json",
    )
    return json.loads(resp["body"].read())["content"][0]["text"].strip()


# ──────────────────────────────────────────────────────────────
# 1. Détection d'intention + extraction de la spec (Haiku)
# ──────────────────────────────────────────────────────────────
_DETECT_SYSTEM = (
    "Tu es un routeur qui détecte les questions ANALYTIQUES posées à un outil de "
    "gestion de copropriété et les traduit en spec JSON structurée.\n\n"
    "Une question est ANALYTIQUE si elle demande un RECENSEMENT, un COMPTAGE, une "
    "SOMME ou une COMPARAISON sur l'ensemble du parc de copropriétés, à partir de "
    "champs structurés (type de document, sous-type, année, statut, montants, "
    "type de dossier, assureur, expert). Signaux : \"tous les\", \"liste de tous\", "
    "\"combien de\", \"montant total\", \"par copropriété\", \"quels copros ont\", "
    "\"toutes les copros\".\n\n"
    "Une question N'EST PAS analytique si elle porte sur le CONTENU d'un document, "
    "une explication, un détail juridique, un résumé, ou un raisonnement sur le texte "
    "(ex: \"que dit le RCP sur...\", \"résume le sinistre X\", \"explique la procédure\"). "
    "Dans ce cas → analytique=false.\n\n"
    "Réponds UNIQUEMENT par un objet JSON valide, sans commentaire ni markdown :\n"
    "{\n"
    '  "analytique": true|false,\n'
    '  "operation": "list|count|sum",\n'
    '  "source": "documents|dossiers",\n'
    '  "select_field": "nom_fichier|sous_type|doc_type|partie|nom_dossier|type_dossier|assureur|expert_nom|null",\n'
    '  "metric": "montant_principal|montant_estime|montant_reel|total_regle|provisions|franchise|reglement_realise|cout_client|null",\n'
    '  "doc_type": "RCP|PV_AG|CONTRAT|DEVIS|FACTURE|BUDGET|DIAGNOSTIC|COURRIER|SINISTRE|COMPTABILITE|ENTRETIEN|ASSURANCE|MUTATION|PLAN|null",\n'
    '  "sous_type": "MRI|DDE|RAVALEMENT|ASCENSEUR|CHAUFFAGE|TOITURE|SYNDIC|etc|null",\n'
    '  "type_dossier": "SINISTRE|TRAVAUX|CONTENTIEUX|null",\n'
    '  "statut": "actif|expire|resilie|cloture|en_cours|EN_ATTENTE|EN_COURS|null",\n'
    '  "annee": null, "annee_min": null, "annee_max": null\n'
    "}\n\n"
    "Règles :\n"
    "- source=documents pour les documents (contrats, factures, devis, diagnostics, PV...). "
    "source=dossiers pour les sinistres/travaux/contentieux (montants réglés, provisions, expert, assureur, statut du dossier).\n"
    "- operation=count pour \"combien\". operation=sum pour \"montant total\" (remplir metric). "
    "operation=list pour énumérer (remplir select_field).\n"
    "- select_field=partie pour lister les entreprises/intervenants cités (source=documents uniquement).\n"
    "- statut documents : actif|expire|resilie|cloture|en_cours. statut dossiers : EN_ATTENTE|EN_COURS|CLOTURE.\n"
    "- Ne remplis que les champs déductibles avec certitude. Tout champ incertain → null.\n\n"
    "Exemples :\n"
    "- \"combien de dégâts des eaux en 2023 par copro\" → {\"analytique\":true,\"operation\":\"count\",\"source\":\"documents\",\"sous_type\":\"DDE\",\"annee\":2023}\n"
    "- \"montant total réglé des sinistres par copropriété\" → {\"analytique\":true,\"operation\":\"sum\",\"source\":\"dossiers\",\"metric\":\"total_regle\"}\n"
    "- \"quels copros ont un contrat de syndic actif\" → {\"analytique\":true,\"operation\":\"list\",\"source\":\"documents\",\"select_field\":\"nom_fichier\",\"doc_type\":\"CONTRAT\",\"sous_type\":\"SYNDIC\",\"statut\":\"actif\"}\n"
    "- \"liste toutes les entreprises intervenues dans toutes les copros\" → {\"analytique\":true,\"operation\":\"list\",\"source\":\"documents\",\"select_field\":\"partie\"}\n"
    "- \"que dit le règlement sur les parties communes\" → {\"analytique\":false}\n"
    "- \"résume le dossier sinistre de Mme Durand\" → {\"analytique\":false}"
)


def detect_analytical_query(query: str, bedrock, model: str) -> Optional[Dict[str, Any]]:
    """Retourne la spec analytique (dict) si la question est analytique, sinon None.

    Sur erreur LLM / JSON → None (fallback silencieux vers le retrieval normal).
    """
    try:
        raw = _invoke(bedrock, model, _DETECT_SYSTEM, query, max_tokens=250)
        raw = re.sub(r"^```json?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        spec = json.loads(raw)
        if not isinstance(spec, dict) or not spec.get("analytique"):
            return None
        # Normaliser les "null" string → None
        for k, v in list(spec.items()):
            if v == "null":
                spec[k] = None
        return spec
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 2. Builder SQL paramétré (liste blanche)
# ──────────────────────────────────────────────────────────────
def build_analytical_sql(spec: Dict[str, Any],
                         copro_filter: Optional[str]) -> Optional[Tuple[str, list]]:
    """Traduit une spec validée en (sql, params). Retourne None si la spec n'est
    pas traduisible (source/opération/champ hors liste blanche)."""
    source = spec.get("source")
    cfg = WHITELIST.get(source)
    if not cfg:
        return None
    op = spec.get("operation")
    if op not in ("list", "count", "sum"):
        return None

    table = cfg["table"]
    where, params = [], []

    # Filtres (seuls les champs whitelistés sont pris en compte)
    for key, (expr, oper) in cfg["filters"].items():
        val = spec.get(key)
        if val is None or val == "null":
            continue
        where.append(f"{expr} {oper} %s")
        params.append(val)

    if copro_filter:
        where.append("code_ncg = %s")
        params.append(copro_filter)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    if op == "count":
        sql = (f"SELECT code_ncg, MIN(copropriete) AS copro_nom, COUNT(*) AS valeur "
               f"FROM {table} {where_sql} GROUP BY code_ncg ORDER BY code_ncg")
        return sql, params

    if op == "sum":
        mcol = cfg["sum_metrics"].get(spec.get("metric"))
        if not mcol:
            return None
        sql = (f"SELECT code_ncg, MIN(copropriete) AS copro_nom, "
               f"COALESCE(SUM({mcol}), 0) AS valeur "
               f"FROM {table} {where_sql} GROUP BY code_ncg ORDER BY code_ncg")
        return sql, params

    # op == "list"
    field = spec.get("select_field")
    if field == "partie":
        if source != "documents":
            return None
        wl = list(where) + ["p IS NOT NULL", "p <> ''"]
        wsql = "WHERE " + " AND ".join(wl)
        sql = (f"SELECT DISTINCT code_ncg, copropriete AS copro_nom, p AS valeur "
               f"FROM documents, UNNEST(parties_concernees) AS p {wsql} "
               f"ORDER BY code_ncg, valeur LIMIT {_MAX_LIST_ROWS}")
        return sql, params

    fexpr = cfg["list_fields"].get(field)
    if not fexpr or fexpr == "__UNNEST_PARTIES__":
        return None
    wl = list(where) + [f"{fexpr} IS NOT NULL"]
    wsql = "WHERE " + " AND ".join(wl)
    sql = (f"SELECT DISTINCT code_ncg, copropriete AS copro_nom, {fexpr} AS valeur "
           f"FROM {table} {wsql} ORDER BY code_ncg, valeur LIMIT {_MAX_LIST_ROWS}")
    return sql, params


# ──────────────────────────────────────────────────────────────
# 3. Exécution + formatage
# ──────────────────────────────────────────────────────────────
_FORMAT_SYSTEM = (
    "Tu mets en forme le résultat d'une requête analytique sur les archives d'un "
    "syndic. Tu reçois la question et des lignes agrégées (colonnes : code_ncg, "
    "copropriété, valeur).\n"
    "- Présente un tableau markdown clair, trié par copropriété.\n"
    "- Pour un comptage ou une somme, ajoute une ligne de TOTAL global.\n"
    "- Pour une liste, regroupe par copropriété.\n"
    "- N'invente AUCUNE donnée : utilise UNIQUEMENT les lignes fournies. "
    "Si une copropriété n'apparaît pas, ne l'invente pas.\n"
    "- Pas de phrase d'introduction ni de conclusion générique. Va droit au tableau."
)


def _fallback_table(rows: List[tuple]) -> str:
    """Tableau markdown déterministe (si le formatage LLM échoue)."""
    lines = ["| Copropriété | Valeur |", "| --- | --- |"]
    for r in rows[:_MAX_ROWS_TO_LLM]:
        copro = r[1] if len(r) > 1 and r[1] else r[0]
        val = r[2] if len(r) > 2 else ""
        lines.append(f"| {copro} | {val} |")
    if len(rows) > _MAX_ROWS_TO_LLM:
        lines.append(f"| ... | (+{len(rows) - _MAX_ROWS_TO_LLM} lignes) |")
    return "\n".join(lines)


def run_analytical_route(spec: Dict[str, Any], copro_filter: Optional[str],
                         conn, bedrock, model: str,
                         question: str = "") -> Optional[Dict[str, Any]]:
    """Exécute la route analytique. Retourne un dict
    {answer, sql, n_rows, rows} ou None si la spec n'est pas traduisible
    ou si la table n'existe pas (→ l'appelant retombe sur le retrieval normal).
    """
    built = build_analytical_sql(spec, copro_filter)
    if not built:
        return None
    sql, params = built

    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception:
        # Table absente / erreur SQL → fallback vers le retrieval normal
        try:
            conn.rollback()
        except Exception:
            pass
        return None

    if not rows:
        return {
            "answer": "Aucun résultat pour cette recherche dans les copropriétés en base.",
            "sql": sql, "n_rows": 0, "rows": [],
        }

    # Formatage LLM (avec garde-fou sur le nombre de lignes)
    rows_for_llm = rows[:_MAX_ROWS_TO_LLM]
    rows_text = "\n".join(
        f"{r[0]} | {r[1] if len(r) > 1 and r[1] else ''} | {r[2] if len(r) > 2 else ''}"
        for r in rows_for_llm
    )
    if len(rows) > _MAX_ROWS_TO_LLM:
        rows_text += f"\n(... {len(rows) - _MAX_ROWS_TO_LLM} lignes supplémentaires non affichées)"

    user = (
        f"Question : {question}\n\n"
        f"Résultat SQL ({len(rows)} lignes) — colonnes : code_ncg | copropriété | valeur :\n"
        f"{rows_text}"
    )
    try:
        answer = _invoke(bedrock, model, _FORMAT_SYSTEM, user, max_tokens=1500)
    except Exception:
        answer = _fallback_table(rows)

    return {"answer": answer, "sql": sql, "n_rows": len(rows), "rows": rows}
