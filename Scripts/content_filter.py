"""
FILTRE DE CONTENU EXPLOITABLE — Module pour 03_chunking.py
=============================================================
Détecte et filtre les chunks contenant du contenu binaire, des données d'images
embarquées, du markup interne Word, ou tout texte non exploitable par un RAG.

Intégration dans 03_chunking.py :
  1. Importer : from content_filter import is_exploitable_text, filter_chunks, analyze_file_quality
  2. Avant chunking : quality = analyze_file_quality(full_text)
     → Si quality["verdict"] == "SKIP", ne pas chunker du tout
     → Si quality["verdict"] == "PLACEHOLDER", créer un chunk métadonnée unique
  3. Après chunking : filtered = filter_chunks(chunks)
     → Élimine les chunks individuels non exploitables dans les fichiers mixtes

Cas couverts :
  - Données pixel RGB (OPMOOQRRMOPMOON...)
  - Codes couleur hex (D69D58C26A25@57...)
  - Encodages binaires / base64 (\x84\xfe\x3a...)
  - Markup interne Word (.doc) (B*CJa Jph, $$If FF...)
  - Séquences de coordonnées (4Mn6Lj*Ky*Ky+Kx...)
  - Métadonnées d'images PNG/JPEG (IHDR, IDAT, JFIF...)
  - Texte avec <20% de caractères alphabétiques
"""
import re
import unicodedata
from typing import Dict, List, Tuple

# =====================================================
# CONFIGURATION DES SEUILS
# =====================================================
# Ratio minimum de caractères alphabétiques (lettres + espaces) pour qu'un texte soit "lisible"
MIN_ALPHA_RATIO = 0.35

# Ratio minimum de mots français reconnaissables (≥3 lettres) pour un chunk
MIN_WORD_RATIO = 0.15

# Longueur minimum d'un chunk exploitable (en caractères)
MIN_CHUNK_LENGTH = 30

# Nombre minimum de mots reconnaissables dans un chunk
MIN_WORD_COUNT = 5

# Ratio max de caractères non-imprimables / contrôle pour déclencher le filtre
MAX_NONPRINT_RATIO = 0.15

# Longueur minimum d'une séquence suspecte répétitive pour déclencher le filtre
REPETITIVE_SEQ_THRESHOLD = 50

# Pour analyze_file_quality : ratio de chunks exploitables pour garder le fichier
MIN_EXPLOITABLE_RATIO_FILE = 0.10  # Si <10% du texte est exploitable → SKIP ou PLACEHOLDER


# =====================================================
# PATTERNS DE CONTENU NON EXPLOITABLE
# =====================================================

# Séquences de codes couleur / coordonnées pixel (type RGB hex sans espaces)
RE_COLOR_CODES = re.compile(
    r'[A-Z][A-Z0-9]{2,5}[A-Z][A-Z0-9]{2,5}[A-Z][A-Z0-9]{2,5}[A-Z][A-Z0-9]{2,5}'
    r'[A-Z][A-Z0-9]{2,5}[A-Z][A-Z0-9]{2,5}',
    re.ASCII,
)

# Séquences type coordonnées d'image (*Ky+Kx*Jw...)
RE_COORD_SEQUENCES = re.compile(
    r'[\*\+\-][A-Z][a-z][\*\+\-][A-Z][a-z][\*\+\-][A-Z][a-z]',
)

# Markup interne Word (.doc binary) — CJa Jph, $$If FF, etc.
RE_WORD_MARKUP = re.compile(
    r'(?:CJa\s*Jph|B\*CJ|OJQJo|B\*ph|\$\$If\s+F|PJ\^J|gd[\*,]|OJQJ)',
)

# Headers/metadata d'images (PNG, JPEG, etc.)
RE_IMAGE_HEADERS = re.compile(
    r'(?:IHDR|IDAT|JFIF|Exif|PNG\s*\r?\n|RGB\s+sRGB|pHYs)',
)

# Séquences hex longues (>20 chars consécutifs de [0-9A-Fa-f] sans espaces)
RE_HEX_LONG = re.compile(r'[0-9A-Fa-f]{20,}')

# Caractères Unicode hors plans de base (emojis exotiques, symboles privés, etc.)
# typiques des blobs binaires mal décodés
RE_HIGH_UNICODE = re.compile(r'[\U00010000-\U0010FFFF]{3,}')

# Lignes de pure ponctuation/symboles (>80% non-alpha)
RE_SYMBOL_LINE = re.compile(r'^[^a-zA-ZÀ-ÿ\d\s]{10,}$', re.MULTILINE)


# =====================================================
# DÉTECTION UNITAIRE
# =====================================================
def compute_text_metrics(text: str) -> Dict:
    """Calcule les métriques de qualité d'un texte."""
    if not text:
        return {"length": 0, "alpha_ratio": 0, "word_count": 0, "nonprint_ratio": 1,
                "has_binary_patterns": False, "has_word_markup": False,
                "has_image_headers": False, "exploitable": False}

    length = len(text)

    # Ratio alphabétique (lettres + espaces vs total)
    alpha_chars = sum(1 for c in text if c.isalpha() or c == ' ')
    alpha_ratio = alpha_chars / length if length > 0 else 0

    # Mots reconnaissables (≥3 lettres consécutives)
    words = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', text)
    word_count = len(words)

    # Caractères non-imprimables
    nonprint = sum(1 for c in text
                   if unicodedata.category(c).startswith('C')
                   and c not in '\n\r\t')
    nonprint_ratio = nonprint / length if length > 0 else 0

    # Patterns binaires
    has_binary = bool(RE_COLOR_CODES.search(text[:2000]) or
                      RE_COORD_SEQUENCES.search(text[:2000]) or
                      RE_HEX_LONG.search(text[:2000]) or
                      RE_HIGH_UNICODE.search(text[:2000]))

    has_word_markup = bool(RE_WORD_MARKUP.search(text[:2000]))
    has_image_headers = bool(RE_IMAGE_HEADERS.search(text[:500]))

    return {
        "length": length,
        "alpha_ratio": alpha_ratio,
        "word_count": word_count,
        "nonprint_ratio": nonprint_ratio,
        "has_binary_patterns": has_binary,
        "has_word_markup": has_word_markup,
        "has_image_headers": has_image_headers,
    }


def is_exploitable_text(text: str, strict: bool = False) -> Tuple[bool, str]:
    """
    Détermine si un texte est exploitable pour un RAG.

    Args:
        text: le texte à analyser
        strict: si True, applique des seuils plus stricts (pour chunks individuels)

    Returns:
        (is_exploitable, reason)
    """
    if not text or len(text.strip()) < MIN_CHUNK_LENGTH:
        return False, "trop_court"

    metrics = compute_text_metrics(text)

    # 1. Caractères non-imprimables excessifs
    if metrics["nonprint_ratio"] > MAX_NONPRINT_RATIO:
        return False, f"nonprint={metrics['nonprint_ratio']:.0%}"

    # 2. Ratio alphabétique trop bas
    threshold = MIN_ALPHA_RATIO + 0.10 if strict else MIN_ALPHA_RATIO
    if metrics["alpha_ratio"] < threshold:
        return False, f"alpha={metrics['alpha_ratio']:.0%}"

    # 3. Patterns binaires détectés
    if metrics["has_binary_patterns"]:
        # Vérifier que ce n'est pas un faux positif (texte réel avec quelques codes)
        # Texte réel = alpha ratio élevé + beaucoup de mots + diversité lexicale
        if metrics["alpha_ratio"] > 0.55 and metrics["word_count"] > 20:
            words_check = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', text[:3000])
            unique_check = set(w.lower() for w in words_check)
            word_diversity = len(unique_check) / max(len(words_check), 1)
            if word_diversity < 0.30:
                return False, "binary_patterns_repetitive"
            # Else: genuinely diverse text with some embedded codes → keep
        else:
            return False, "binary_patterns"

    # 4. Markup Word
    if metrics["has_word_markup"]:
        # Word markup often has high alpha ratio (letters like CJa, Jph) but no real words
        # Check for low unique word diversity (same 3-letter combos repeating)
        words_in_text = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', text)
        unique_words = set(w.lower() for w in words_in_text)
        diversity = len(unique_words) / max(len(words_in_text), 1)
        if diversity < 0.40 or metrics["alpha_ratio"] < 0.60:
            return False, "word_markup"

    # 5. Headers d'image
    if metrics["has_image_headers"] and metrics["alpha_ratio"] < 0.40:
        return False, "image_headers"

    # 6. Pas assez de mots reconnaissables
    min_words = MIN_WORD_COUNT * 2 if strict else MIN_WORD_COUNT
    if metrics["word_count"] < min_words:
        return False, f"mots={metrics['word_count']}"

    # 7. Détection de séquences répétitives (données pixel tabulées)
    # Compter les lignes qui ne sont que des codes (pas de vrais mots)
    lines = text.split('\n')
    garbage_lines = 0
    for line in lines[:50]:  # Échantillonner les 50 premières lignes
        line = line.strip()
        if not line:
            continue
        line_words = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', line)
        if len(line) > 20 and len(line_words) < 2:
            garbage_lines += 1
    total_nonblank = sum(1 for l in lines[:50] if l.strip())
    if total_nonblank > 5 and garbage_lines / total_nonblank > 0.70:
        return False, f"repetitive_garbage={garbage_lines}/{total_nonblank}"

    return True, "ok"


# =====================================================
# ANALYSE AU NIVEAU FICHIER
# =====================================================
def analyze_file_quality(full_text: str, filename: str = "") -> Dict:
    """
    Analyse la qualité globale d'un fichier extrait.

    Returns dict avec :
      - verdict: "OK" (chunker normalement), "PLACEHOLDER" (1 chunk métadonnée),
                 "SKIP" (ignorer complètement)
      - reason: explication
      - exploitable_ratio: % du texte considéré exploitable
      - stats: métriques détaillées
    """
    if not full_text or len(full_text.strip()) < 50:
        return {
            "verdict": "SKIP",
            "reason": "Fichier vide ou trop court",
            "exploitable_ratio": 0,
            "stats": {},
        }

    metrics = compute_text_metrics(full_text)

    # Test rapide sur le texte entier
    is_ok, reason = is_exploitable_text(full_text)

    if is_ok:
        return {
            "verdict": "OK",
            "reason": "Texte exploitable",
            "exploitable_ratio": 1.0,
            "stats": metrics,
        }

    # Si le texte entier n'est pas exploitable, tester par blocs de 1000 chars
    # pour détecter les fichiers mixtes (intro textuelle + images binaires)
    block_size = 1000
    n_blocks = max(1, len(full_text) // block_size)
    exploitable_blocks = 0

    for i in range(min(n_blocks, 100)):  # Échantillonner max 100 blocs
        start = i * block_size
        block = full_text[start:start + block_size]
        block_ok, _ = is_exploitable_text(block)
        if block_ok:
            exploitable_blocks += 1

    sampled = min(n_blocks, 100)
    ratio = exploitable_blocks / sampled if sampled > 0 else 0

    if ratio >= MIN_EXPLOITABLE_RATIO_FILE:
        # Le fichier a du contenu mixte — on le laisse passer,
        # les chunks individuels seront filtrés après
        return {
            "verdict": "OK",
            "reason": f"Fichier mixte ({ratio:.0%} exploitable), filtrage par chunk",
            "exploitable_ratio": ratio,
            "stats": metrics,
        }

    # Fichier quasi entièrement non exploitable
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ""
    is_image_source = ext in ("jpg", "jpeg", "png", "tif", "tiff", "bmp", "gif")
    is_doc_with_images = ext in ("doc", "docx") and metrics.get("has_binary_patterns", False)

    if is_image_source or is_doc_with_images:
        return {
            "verdict": "PLACEHOLDER",
            "reason": f"Contenu binaire/image ({reason}), placeholder créé",
            "exploitable_ratio": ratio,
            "stats": metrics,
        }

    return {
        "verdict": "SKIP",
        "reason": f"Contenu non exploitable ({reason})",
        "exploitable_ratio": ratio,
        "stats": metrics,
    }


def make_placeholder_chunk(source_file: str, filename: str, copropriete: str,
                           doc_type: str, file_size_chars: int) -> Dict:
    """
    Crée un chunk métadonnée unique pour un fichier non exploitable par OCR.
    Le texte est une description du fichier, pas son contenu.
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else "inconnu"

    type_descriptions = {
        "doc": "Document Word avec images embarquées",
        "docx": "Document Word avec images embarquées",
        "jpg": "Photo ou image scannée",
        "jpeg": "Photo ou image scannée",
        "png": "Image PNG",
        "tif": "Image TIFF scannée",
        "tiff": "Image TIFF scannée",
        "bmp": "Image bitmap",
    }
    file_desc = type_descriptions.get(ext, f"Fichier {ext}")

    text = (
        f"[FICHIER NON EXPLOITABLE PAR OCR]\n"
        f"Type : {file_desc}\n"
        f"Nom : {filename}\n"
        f"Chemin : {source_file}\n"
        f"Copropriété : {copropriete}\n"
        f"Classification : {doc_type}\n"
        f"Taille texte brut : {file_size_chars} caractères (non lisibles)\n"
        f"Note : Ce fichier contient principalement des données binaires ou des images "
        f"dont le contenu textuel n'a pas pu être extrait de manière exploitable. "
        f"Le fichier existe dans les archives et peut être consulté directement."
    )

    return {
        "source_file": source_file,
        "nom_fichier": filename,
        "copropriete": copropriete,
        "doc_type": doc_type,
        "chunk_index": 0,
        "total_chunks": 1,
        "text": text,
        "nb_caracteres": len(text),
        "is_placeholder": True,
    }


# =====================================================
# FILTRAGE POST-CHUNKING
# =====================================================
def filter_chunks(chunks: List[Dict], verbose: bool = False) -> Tuple[List[Dict], Dict]:
    """
    Filtre les chunks non exploitables après le chunking.
    Utilisé pour les fichiers mixtes (texte + binaire).

    Args:
        chunks: liste de dicts avec au minimum un champ "text"
        verbose: si True, affiche les chunks filtrés

    Returns:
        (chunks_filtrés, stats)
        stats = {"total": N, "kept": N, "filtered": N, "reasons": Counter}
    """
    from collections import Counter

    kept = []
    reasons = Counter()
    filtered_count = 0

    for chunk in chunks:
        text = chunk.get("text", "")
        is_ok, reason = is_exploitable_text(text, strict=True)

        if is_ok:
            kept.append(chunk)
        else:
            filtered_count += 1
            reasons[reason] += 1
            if verbose:
                preview = text[:80].replace('\n', ' ')
                print(f"  ⛔ Chunk filtré ({reason}) : {preview}...")

    stats = {
        "total": len(chunks),
        "kept": len(kept),
        "filtered": filtered_count,
        "reasons": dict(reasons),
    }

    return kept, stats


# =====================================================
# POINT D'ENTRÉE POUR TESTS
# =====================================================
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python content_filter.py <fichier_extrait.json>")
        print("       python content_filter.py --test-chunk \"texte à tester\"")
        sys.exit(1)

    if sys.argv[1] == "--test-chunk":
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        is_ok, reason = is_exploitable_text(text)
        metrics = compute_text_metrics(text)
        print(f"Exploitable : {is_ok} ({reason})")
        print(f"Métriques   : alpha={metrics['alpha_ratio']:.0%}, "
              f"mots={metrics['word_count']}, "
              f"nonprint={metrics['nonprint_ratio']:.0%}, "
              f"binary={metrics['has_binary_patterns']}")
        sys.exit(0)

    # Analyser un fichier JSON extrait
    filepath = sys.argv[1]
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    text = data.get("texte", "")
    filename = data.get("nom_fichier", filepath)

    print(f"\n{'='*60}")
    print(f"ANALYSE : {filename}")
    print(f"{'='*60}")
    print(f"Taille : {len(text)} caractères")

    quality = analyze_file_quality(text, filename)
    print(f"\nVerdict  : {quality['verdict']}")
    print(f"Raison   : {quality['reason']}")
    print(f"Ratio OK : {quality['exploitable_ratio']:.0%}")

    if quality['stats']:
        m = quality['stats']
        print(f"\nMétriques :")
        print(f"  Alpha ratio    : {m['alpha_ratio']:.0%}")
        print(f"  Word count     : {m['word_count']}")
        print(f"  Nonprint ratio : {m['nonprint_ratio']:.0%}")
        print(f"  Binary patterns: {m['has_binary_patterns']}")
        print(f"  Word markup    : {m['has_word_markup']}")
        print(f"  Image headers  : {m['has_image_headers']}")

    # Simuler le chunking simple et filtrage
    if quality["verdict"] == "OK" and len(text) > 1000:
        # Découper en blocs de 2000 chars pour simuler
        fake_chunks = []
        for i in range(0, len(text), 2000):
            fake_chunks.append({"text": text[i:i+2000], "chunk_index": i // 2000})

        filtered, stats = filter_chunks(fake_chunks, verbose=True)
        print(f"\nFiltrage post-chunk : {stats['kept']}/{stats['total']} gardés")
        if stats['reasons']:
            print(f"Raisons de filtrage : {stats['reasons']}")

    elif quality["verdict"] == "PLACEHOLDER":
        placeholder = make_placeholder_chunk(
            data.get("source_file", ""), filename,
            data.get("copropriete", ""), "SINISTRE", len(text)
        )
        print(f"\nPlaceholder créé :")
        print(f"  {placeholder['text']}")
