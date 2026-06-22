# -*- coding: utf-8 -*-
"""Test isolé de linkify_sources (sans démarrer Streamlit).

Charge UNIQUEMENT les fonctions top-level de streamlit_app.py (décorateurs retirés,
aucune dépendance Streamlit/DB/Bedrock exécutée), puis vérifie les 5 critères du patch [N].
Lancer : PYTHONIOENCODING=utf-8 python test_linkify.py
"""
import ast
import io
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "streamlit_app.py"), encoding="utf-8").read()

ns = {"re": re}
tree = ast.parse(SRC)
for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        node.decorator_list = []  # éviter @st.cache_* à l'exec
        try:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "streamlit_app.py", "exec"), ns)
        except Exception:
            pass  # fonctions non pertinentes / deps absentes : ignorées

linkify_sources = ns["linkify_sources"]

LINK_RE = re.compile(r'<a href="#source-[^"]*"[^>]*>Source (\d+)</a>')


def to_text(out):
    """Aplatit la sortie de linkify_sources (str OU liste de segments) en une chaîne."""
    if isinstance(out, str):
        return out
    acc = []

    def walk(x):
        if isinstance(x, str):
            acc.append(x)
        elif isinstance(x, (list, tuple)):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            for y in x.values():
                walk(y)
    walk(out)
    return "\n".join(acc)


_dbg = linkify_sources("Sources : [45], [49], [57]", 100)
print("DEBUG type sortie:", type(_dbg).__name__,
      "| extrait:", repr(to_text(_dbg))[:200], "\n")


def check(label, got, expect_links=None, must_keep=None, must_have_no_links=False):
    ok = True
    out = to_text(linkify_sources(*got))
    found = [int(n) for n in LINK_RE.findall(out)]
    detail = f"liens={found}"
    if expect_links is not None and found != expect_links:
        ok = False
    if must_keep:
        for frag in must_keep:
            if frag not in out:
                ok = False
                detail += f" | MANQUE '{frag}'"
    if must_have_no_links and found:
        ok = False
    print(f"[{'OK ' if ok else 'FAIL'}] {label} -> {detail}")
    return ok


results = []
# 1. [N] séparés
results.append(check("[45], [49], [57] (mx=100)", ("Sources : [45], [49], [57]", 100), expect_links=[45, 49, 57]))
# 2. [N, M, P] groupés
results.append(check("[45, 49, 57] (mx=100)", ("[45, 49, 57]", 100), expect_links=[45, 49, 57]))
# 3. ancien format "Source N" (non-régression)
results.append(check("Source 12 et Source 15 (mx=100)", ("Voir Source 12 et Source 15", 100), expect_links=[12, 15]))
# 4. hors plage -> intact, aucun lien
results.append(check("[2024], [150] hors plage (mx=10)", ("En [2024], lot [150]", 10),
                     must_keep=["[2024]", "[150]"], must_have_no_links=True))
# 5. ancien format groupé "Sources 4, 8, 10"
results.append(check("Sources 4, 8, 10 (mx=20)", ("Sources 4, 8, 10", 20), expect_links=[4, 8, 10]))

print()
print("RESULTAT:", "TOUS OK" if all(results) else "ECHECS PRESENTS")
