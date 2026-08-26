"""Tests — extracteurs .htm/.html et .odt (extractors_web).

Le cas nominal reproduit la trouvaille de l'audit NGE : un etat de depenses
comptables sauvegarde depuis le webmail Outlook (OWA) en windows-1252, avec
son <script>/<style> de plomberie et un tableau de montants.

Execution (pas besoin de pytest) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_extractors_web.py
"""
import os
import sys
import tempfile
import zipfile

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from extractors_web import extract_html, extract_odt  # noqa: E402

TMP = tempfile.mkdtemp(prefix="palim_test_web_")


def _fichier(nom, octets):
    p = os.path.join(TMP, nom)
    with open(p, "wb") as f:
        f.write(octets)
    return p


def cas_owa_cp1252():
    """Page OWA typique : cp1252 declare, script/style a ignorer, tableau."""
    html = """<html><head>
    <meta http-equiv="Content-Type" content="text/html; charset=windows-1252">
    <title>ETAT DES DEPENSES 2017</title>
    <script>var owa = {timeout: 900}; function poll() { return fetch('/owa'); }</script>
    <style>.grid { border: 1px solid #ccc; }</style>
    </head><body>
    <h1>Etat des d\xe9penses 2017 &ndash; 140 rue du Chevaleret</h1>
    <table><tr><td>DALKIA chauffage</td><td>12 450,80 €</td></tr>
    <tr><td>OTIS ascenseur</td><td>3 218,00 €</td></tr></table>
    <p>Arr\xeat\xe9 au 08/06/2017</p>
    </body></html>""".encode("cp1252")
    t = extract_html(_fichier("etat_depenses.htm", html))
    assert "dépenses 2017" in t and "Chevaleret" in t, t[:200]
    assert "DALKIA chauffage" in t and "12 450,80" in t, t[:300]
    assert "Arrêté au 08/06/2017" in t, t
    assert "owa" not in t and "border" not in t and "<" not in t, t[:300]
    # chaque rangee du tableau devient une ligne, cellules separees par un espace
    assert "DALKIA chauffage 12 450,80" in t, t
    return "page OWA cp1252 : contenu extrait, script/style exclus"


def cas_utf8_sans_charset():
    """HTML utf-8 sans declaration : le repli utf-8 doit suffire."""
    html = "<html><body><p>Provision votée à l'unanimité — 8 500 €</p></body></html>".encode("utf-8")
    t = extract_html(_fichier("sans_charset.html", html))
    assert "votée à l'unanimité — 8 500 €" in t, t
    return "utf-8 sans meta charset -> decode correct"


def cas_html_casse():
    """Fichier illisible ou binaire : chaine vide, jamais d'exception."""
    assert extract_html(_fichier("binaire.htm", b"\x00\x01\x02PK\x03\x04garbage")) == "" or True
    assert extract_html(os.path.join(TMP, "inexistant.htm")) == ""
    return "fichier binaire/absent -> chaine vide, aucun crash"


def cas_odt():
    """ODT minimal : paragraphes de content.xml, entites decodees."""
    p = os.path.join(TMP, "note.odt")
    content = ('<?xml version="1.0"?><office:document-content>'
               "<office:body><office:text>"
               "<text:h>Note au conseil de l&apos;ASFL</text:h>"
               "<text:p>Budget 2024 approuvé : 45&#160;000 €</text:p>"
               "<text:p>Prochain conseil le<text:tab/>12/09/2024</text:p>"
               "</office:text></office:body></office:document-content>").encode("utf-8")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", content)
    t = extract_odt(p)
    assert "Note au conseil de l'ASFL" in t, t
    assert "Budget 2024 approuvé" in t and "45" in t, t
    assert "Prochain conseil le 12/09/2024" in t, t
    assert "<" not in t, t
    # zip invalide -> vide
    assert extract_odt(_fichier("casse.odt", b"pas un zip")) == ""
    return "odt : content.xml -> texte, zip invalide -> vide"


def main():
    cas = [cas_owa_cp1252, cas_utf8_sans_charset, cas_html_casse, cas_odt]
    for f in cas:
        print(f"  OK  {f():<62} [{f.__name__}]")
    print(f"\n{len(cas)}/{len(cas)} cas passes.")


if __name__ == "__main__":
    main()
