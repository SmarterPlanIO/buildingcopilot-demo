"""
PALIM_visites.py — Liens de visite 3D (jumeau numérique SmarterPlan).

Charge un registre statique `MOT_CLE : URL` depuis visites_3d.txt (embarqué dans
le package, à côté de ce module) et expose un matching substring insensible
casse/accents sur une requête libre. Porté depuis la fonction démo de
streamlit_app.py (DEMO_3D_LINKS) vers le serveur MCP.

Format du fichier (une paire par ligne, lignes vides et # ignorés) :
    LEMEAU : https://demo.smarterplan.io/visit/...
    EXTINCTEUR : https://demo.smarterplan.io/visit/...
Une ligne contenant uniquement une URL est rattachée à un libellé générique.
"""
import os
import unicodedata

VISITES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visites_3d.txt")

# Libellé générique pour une ligne ne contenant qu'une URL.
_GENERIC_LABEL = "Visualisez votre copropriété en 3D"


def _norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _load(path=VISITES_FILE):
    """Retourne un dict {MOT_CLE (upper) : url}. Vide si fichier absent/illisible."""
    links = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if " : " in line:
                    kw, url = line.split(" : ", 1)
                    kw, url = kw.strip().upper(), url.strip()
                    if url:
                        links[kw] = url
                elif line.startswith("http"):
                    links[_GENERIC_LABEL] = line
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return links


# Chargé une fois à l'import (registre statique, comme les autres modules MCP).
VISITES = _load()


def match_visites(query):
    """Liste les liens 3D dont le mot-clé apparaît dans `query`.

    Matching substring insensible casse/accents (comportement de la démo
    Streamlit). Retourne [{"label": MOT_CLE, "url": ...}] dans l'ordre du fichier.
    """
    q = _norm(query)
    if not q:
        return []
    out = []
    for kw, url in VISITES.items():
        if _norm(kw) in q:
            out.append({"label": kw, "url": url})
    return out
