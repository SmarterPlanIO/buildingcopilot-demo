"""
test_palim_mcp_contracts.py — Tests de l'invariant de scope (PALIM_scope).

Sans DB ni dépendances lourdes. Exécuter :
    PYTHONIOENCODING=utf-8 python tests/test_palim_mcp_contracts.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import PALIM_scope as scope

_failures = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        _failures.append(name)


# ── normalize_copro_codes ──
check("normalize: None -> []", scope.normalize_copro_codes(None) == [])
check("normalize: str -> [str]", scope.normalize_copro_codes("5390") == ["5390"])
check("normalize: dédup + trim", scope.normalize_copro_codes([" 5390 ", "5390", "5427"]) == ["5390", "5427"])
check("normalize: drop None/empty", scope.normalize_copro_codes(["5390", None, ""]) == ["5390"])

# ── infer_scope ──
check("infer: 0 -> global", scope.infer_scope([]) == "global")
check("infer: 1 -> single", scope.infer_scope(["5390"]) == "single")
check("infer: 2 -> multi", scope.infer_scope(["5390", "5427"]) == "multi")

# ── validate_search_scope (invariant non-dilution) ──
ok, inf, err = scope.validate_search_scope([])
check("validate: 0 copro -> refus", (not ok) and err and err["error_type"] == "MISSING_COPRO_SCOPE")
check("validate: refus suggère discover", err.get("suggested_next_tool") == "PALIM_discover_copros")

ok, inf, err = scope.validate_search_scope(["5390"])
check("validate: 1 copro -> ok single", ok and inf == "single" and err is None)

ok, inf, err = scope.validate_search_scope(["5390", "5427"])
check("validate: 2 copros -> ok multi", ok and inf == "multi")

# ── invalid_codes / warnings ──
check("invalid: format non numérique détecté", scope.invalid_codes(["NCG_001", "5390"]) == ["NCG_001"])
check("invalid: codes valides -> aucun", scope.invalid_codes(["5390", "8030"]) == [])
w = scope.build_scope_warnings(["NCG_001"])
check("warnings: format suspect signalé", len(w) == 1 and "format" in w[0].lower())

print()
if _failures:
    print(f"{len(_failures)} test(s) FAILED: {_failures}")
    sys.exit(1)
print("ALL CONTRACT TESTS PASSED")
