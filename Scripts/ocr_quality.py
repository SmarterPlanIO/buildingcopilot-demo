"""ocr_quality.py — gate de qualite de la couche texte des PDF (fix du cas 320).

Contexte : extract_pdf_native (02) decidait "PDF natif -> sauter Textract" sur un
critere de VOLUME seul (>300 chars/page + 80% couverture). Une couche OCR embarquee
volumineuse mais pourrie (scanner du syndic, export Lobby) passait ce test : le
charabia entrait en base tel quel (cas prouve : 320-PV AG OCT 2024, AA8054405,
resolution 22 illisible). Ce module ajoute le critere de QUALITE.

Triage (cout maitrise) :
  score heuristique < SEUIL_PROPRE  -> PROPRE  (aucun appel LLM ; ~85 % du parc)
  score >= SEUIL_PROPRE             -> arbitrage Haiku (~0,0005 $/doc)
  (sans arbitre disponible : repli heuristique, DEGRADE au-dessus de SEUIL_DEGRADE)

MESURE DU 27/08 (echantillon 40 docs flagues, arbitrage Haiku de reference) : le
score seul produit **57 % de faux positifs**, concentres sur les documents
TABULAIRES ET NUMERIQUES sains (balances comptables, rapprochements bancaires,
releves de compteurs, listes de tantiemes) : peu de mots-outils francais, tokens
alphanumeriques colles -> score eleve alors que le texte est parfait. Un score
haut ne vaut donc PAS condamnation : au-dessus de SEUIL_PROPRE on arbitre toujours
avec Haiku quand il est disponible. Cout de l'arbitrage (0,0005 $) tres inferieur
au cout d'un OCR inutile (~0,014 $/doc).

Fail-open : si Bedrock est indisponible en zone grise, on garde le comportement
historique (PROPRE + warning) — jamais pire qu'avant le fix.

Verdicts memoises par appelant (extract_pdf_native est appele 2x par fichier).
Calibrage du score sur le balayage du 26/08 : mediane du parc sain 0,12-0,15 ;
cas prouve 320 a 0,36 ; charabia franc > 0,6.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

SEUIL_PROPRE = 0.22    # en-dessous : couche consideree saine sans arbitrage
SEUIL_DEGRADE = 0.55   # au-dessus : charabia evident sans arbitrage
_SAMPLE_CHARS = 2500

HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

_STOP = re.compile(r"\b(le|la|les|de|des|du|et|un|une|que|qui|dans|pour|par|sur|est|sont|aux?|ce|cette)\b", re.I)
_WEIRD = re.compile(r"\b(?=\w*\d)(?=\w*[A-Za-z])[A-Za-z0-9]{2,}\b")  # tokens chiffres+lettres
_IP = re.compile(r"\bI'")   # « l' » OCRise en « I' » — signature tres discriminante
_LP = re.compile(r"\bl'")

_verdict_cache: dict[str, tuple[str, float, str]] = {}


def score_texte(text: str) -> float:
    """Score heuristique de charabia OCR (0 = propre). Pur, deterministe, sans I/O."""
    t = text[:4000]
    tokens = re.findall(r"\S+", t)
    n = max(len(tokens), 1)
    stop = len(_STOP.findall(t)) / n
    weird = len(_WEIRD.findall(t)) / n
    ip, lp = len(_IP.findall(t)), len(_LP.findall(t))
    ip_ratio = ip / max(ip + lp, 1)
    punct = sum(1 for c in t if not c.isalnum() and not c.isspace()) / max(len(t), 1)
    return round((max(0.0, 0.18 - stop) * 3.0) + weird * 2.0 + ip_ratio * 0.5
                 + max(0.0, punct - 0.08) * 2.0, 4)


def _arbitrage_haiku(sample: str, bedrock) -> str:
    prompt = (
        "Voici un extrait de la couche texte d'un PDF de gestion de copropriete "
        "(francais). Dis si ce texte est exploitable tel quel ou s'il provient d'un "
        "OCR degrade (lettres substituees, mots fusionnes, chiffres alteres, ordre "
        "de lecture casse). Juge UNIQUEMENT la qualite des caracteres et des mots : "
        "un texte repetitif, tabulaire ou comptable mais bien orthographie est PROPRE. "
        "Reponds UNIQUEMENT par un mot : PROPRE ou DEGRADE.\n\n"
        f"<extrait>\n{sample[:_SAMPLE_CHARS]}\n</extrait>"
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 5,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = bedrock.invoke_model(modelId=HAIKU_MODEL, body=body,
                                contentType="application/json", accept="application/json")
    answer = json.loads(resp["body"].read())["content"][0]["text"].strip().upper()
    return "DEGRADE" if "DEGRADE" in answer else "PROPRE"


def verdict_couche(text: str, cache_key: str | None = None, bedrock_factory=None):
    """-> (verdict "PROPRE"|"DEGRADE", score, methode "heuristique"|"haiku"|"fail-open").

    bedrock_factory : callable sans argument retournant un client bedrock-runtime
    (injecte par l'appelant ; None = pas d'arbitrage possible -> heuristique seule).
    """
    if cache_key and cache_key in _verdict_cache:
        return _verdict_cache[cache_key]

    score = score_texte(text)
    if score < SEUIL_PROPRE:
        result = ("PROPRE", score, "heuristique")
    elif bedrock_factory is None:
        # Pas d'arbitre : on privilegie la QUALITE. Un OCR inutile coute ~0,014 $ ;
        # une couche pourrie en base coute une reponse fausse au gestionnaire
        # (cas 320). Faux positifs assumes dans ce mode degrade.
        result = ("DEGRADE", score, "heuristique")
    else:
        try:
            v = _arbitrage_haiku(text, bedrock_factory())
            result = (v, score, "haiku")
        except Exception as e:
            log.warning(f"ocr_quality : arbitrage Haiku indisponible ({e}) — fail-open PROPRE")
            result = ("PROPRE", score, "fail-open")

    if cache_key:
        _verdict_cache[cache_key] = result
    return result
