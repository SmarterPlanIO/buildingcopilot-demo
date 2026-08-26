"""
Extracteurs texte pour pages web enregistrees (.htm/.html) et OpenDocument (.odt).

CONTEXTE (audit NGE 22/08/2026, guide Section 22 pour la methode) : le dossier
COMPTA de 5490 Chevaleret contient des etats de depenses comptables sauvegardes
depuis le webmail Outlook (OWA) en "page complete" : un .htm porteur du contenu
+ un dossier "*_fichiers" de plomberie (.js/.css). Le pipeline gardait ces .htm
par precaution (extension inconnue dans 01) mais 02 n'avait aucun extracteur ->
ils finissaient TEXTE_VIDE au registre. Meme sort pour les .odt.

Ce module est PUR (stdlib uniquement, aucune dependance, aucun etat) : il est
importe par 02_extraction_optimized.py qui le branche dans DIRECT_EXTRACTORS.
Tests : tests/test_extractors_web.py (sans pytest, comme les autres suites).

Encodage : les sauvegardes OWA/IE sont souvent en windows-1252 avec un
<meta charset=...> declaratif. On tente dans l'ordre : charset declare,
utf-8 strict, cp1252 (qui ne peut pas echouer).
"""
from __future__ import annotations

import re
import zipfile
from html import unescape
from html.parser import HTMLParser

# Balises dont le contenu textuel est du code ou du bruit, jamais du document.
_BALISES_MUETTES = {"script", "style", "head", "noscript", "template"}
# Balises de bloc : leur fermeture vaut retour a la ligne (structure preservee
# pour le chunking, qui decoupe sur les sauts de ligne).
_BALISES_BLOC = {"p", "div", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6",
                 "li", "ul", "ol", "section", "article", "header", "footer", "blockquote"}

_RE_CHARSET = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.I)
_RE_XML_TAG = re.compile(r"<[^>]+>")
_RE_LIGNES_VIDES = re.compile(r"\n{3,}")
_RE_ESPACES = re.compile(r"[ \t\xa0]+")


class _ExtracteurTexte(HTMLParser):
    """Collecte le texte visible d'un document HTML, bruit exclu."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._morceaux = []
        self._muet = 0            # profondeur dans une balise muette

    def handle_starttag(self, tag, attrs):
        if tag in _BALISES_MUETTES:
            self._muet += 1
        elif tag == "br":
            self._morceaux.append("\n")
        elif tag in ("td", "th"):
            self._morceaux.append("\t")

    def handle_endtag(self, tag):
        if tag in _BALISES_MUETTES:
            self._muet = max(0, self._muet - 1)
        elif tag in _BALISES_BLOC:
            self._morceaux.append("\n")

    def handle_data(self, data):
        if not self._muet and data:
            self._morceaux.append(data)

    def texte(self) -> str:
        brut = "".join(self._morceaux)
        brut = _RE_ESPACES.sub(" ", brut)
        lignes = [l.strip() for l in brut.split("\n")]
        return _RE_LIGNES_VIDES.sub("\n\n", "\n".join(l for l in lignes if l)).strip()


def _decoder(octets: bytes) -> str:
    """Decode un fichier HTML : charset declare, sinon utf-8, sinon cp1252."""
    m = _RE_CHARSET.search(octets[:4096])
    if m:
        try:
            return octets.decode(m.group(1).decode("ascii"), errors="replace")
        except LookupError:
            pass
    try:
        return octets.decode("utf-8")
    except UnicodeDecodeError:
        return octets.decode("cp1252", errors="replace")


def extract_html(filepath: str) -> str:
    """Texte visible d'une page web enregistree. Chaine vide si illisible
    (le pipeline classe alors le fichier TEXTE_VIDE, comme avant)."""
    try:
        with open(filepath, "rb") as f:
            octets = f.read()
    except OSError:
        return ""
    p = _ExtracteurTexte()
    try:
        p.feed(_decoder(octets))
        p.close()
    except Exception:
        return ""
    return p.texte()


def extract_odt(filepath: str) -> str:
    """Texte d'un document OpenDocument (.odt) : content.xml debarrasse de ses
    balises. Les paragraphes <text:p> deviennent des sauts de ligne."""
    try:
        with zipfile.ZipFile(filepath) as z:
            xml = z.read("content.xml").decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    xml = xml.replace("</text:p>", "\n").replace("</text:h>", "\n")
    xml = xml.replace("<text:tab/>", "\t").replace("<text:line-break/>", "\n")
    texte = unescape(_RE_XML_TAG.sub("", xml))
    lignes = [_RE_ESPACES.sub(" ", l).strip() for l in texte.split("\n")]
    return _RE_LIGNES_VIDES.sub("\n\n", "\n".join(l for l in lignes if l)).strip()
