"""
Point d'entrée Streamlit Cloud — app CSG (Cabinet Saint Germain).

Identique au wrapper NCG (streamlit_app.py) : Streamlit Cloud n'autorise
qu'une app par triplet (repo, branche, fichier), ce fichier dédié permet une
2e app sur main. Le tenant est déterminé UNIQUEMENT par les secrets de l'app
([db] → RDS sp-rag-csg-copros).
"""
import os
import sys

_sc_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Scripts", "Streamlit Cloud")
if _sc_dir not in sys.path:
    sys.path.insert(0, _sc_dir)

_real_app = os.path.join(_sc_dir, "streamlit_app.py")
with open(_real_app, encoding="utf-8") as _f:
    exec(compile(_f.read(), _real_app, "exec"),  # noqa: S102
         {"__file__": _real_app, "__name__": "__main__"})
