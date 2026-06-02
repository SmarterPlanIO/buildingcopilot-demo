"""
PALIM_scope.py — Invariant de scoping multi-copropriété (cf. PLAN_ACTION §2).

Le serveur NE reçoit PAS de scope_mode de Claude : il le DÉRIVE depuis
copro_codes et le retourne (inferred_scope). Aucune requête de retrieval de
réponse finale ne part sans au moins une copro (non-dilution dure).

Aucune exception brute ne doit remonter à Claude : les erreurs sont des
réponses MCP structurées (build_error).
"""
import re

_CODE_RE = re.compile(r"^\d{4,6}$")  # code_ncg = entier 4-6 chiffres (ex. "5390")


def normalize_copro_codes(copro_codes):
    """Nettoie, déduplique et valide le format des codes. Retourne une liste (ordre stable)."""
    if copro_codes is None:
        return []
    if isinstance(copro_codes, str):
        copro_codes = [copro_codes]
    seen, out = set(), []
    for c in copro_codes:
        if c is None:
            continue
        c = str(c).strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def invalid_codes(copro_codes):
    """Retourne les codes au format incorrect (pour warning, pas blocage)."""
    return [c for c in copro_codes if not _CODE_RE.match(c)]


def infer_scope(copro_codes):
    """0 -> 'global', 1 -> 'single', >=2 -> 'multi'."""
    n = len(copro_codes)
    if n == 0:
        return "global"
    if n == 1:
        return "single"
    return "multi"


def validate_search_scope(copro_codes):
    """
    Pour PALIM_search_chunks (réponse finale) : exige >= 1 copro.
    Retourne (ok: bool, inferred_scope|None, error_dict|None).
    """
    codes = normalize_copro_codes(copro_codes)
    if not codes:
        return False, None, build_error(
            "MISSING_COPRO_SCOPE",
            "Une recherche de réponse nécessite au moins une copropriété. "
            "Utiliser PALIM_discover_copros pour identifier les copros pertinentes, "
            "ou PALIM_list_copros pour choisir par nom/adresse/alias.",
            suggested_next_tool="PALIM_discover_copros",
        )
    return True, infer_scope(codes), None


def build_error(error_type, message, suggested_next_tool=None):
    err = {"ok": False, "error_type": error_type, "message": message}
    if suggested_next_tool:
        err["suggested_next_tool"] = suggested_next_tool
    return err


def build_scope_warnings(copro_codes):
    """Warnings non bloquants (format de code suspect)."""
    warnings = []
    bad = invalid_codes(copro_codes)
    if bad:
        warnings.append(
            f"Codes au format inattendu (attendu 4-6 chiffres) : {bad}. "
            "Vérifier via PALIM_list_copros."
        )
    return warnings
