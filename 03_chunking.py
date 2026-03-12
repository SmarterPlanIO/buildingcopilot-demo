"""
ÉTAPE 3 — Chunking intelligent adapté aux documents de copropriété
Lance : python 03_chunking.py
Prérequis : content_filter.py dans le même dossier

Classification doc_type en 3 passes :
  1. Structure des dossiers (fiable, gratuit, instantané)
  2. Nom du fichier avec word boundaries (fiable, gratuit, instantané)
  3. Contenu via Claude Haiku (coûteux — uniquement si passes 1+2 retournent AUTRE
     ET texte > 200 caractères, avec cache par fichier source)

Filtre contenu binaire (v2) :
  - Avant chunking : analyze_file_quality → SKIP / PLACEHOLDER / OK
  - Après chunking : filter_chunks → élimine les chunks individuels non exploitables
  - Les fichiers images/Word avec données binaires → 1 chunk placeholder métadonnée
"""
import os
import re
import json
import hashlib
import logging
from pathlib import Path
from tqdm import tqdm

import boto3

# Filtre de contenu binaire / garbage (v2)
from content_filter import analyze_file_quality, filter_chunks, make_placeholder_chunk

# =====================================================
# CONFIGURATION
# =====================================================
EXTRACTED_DIR = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\Archives_Extraites"  # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"     # ← MODIFIER

# Taille cible des chunks (en caractères)
CHUNK_TARGET_SIZE = 1500    # ~375 tokens français
CHUNK_MAX_SIZE = 3000       # Maximum souple (articles/résolutions)
CHUNK_HARD_MAX = 5000       # Maximum ABSOLU — compatible Titan V2 (8192 tokens)
CHUNK_OVERLAP = 200         # Chevauchement entre chunks

# AWS / LLM — Passe 3 uniquement (fallback classification par contenu)
AWS_REGION = "eu-west-1"
CLASSIFIER_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"  # Haiku : rapide, ~20x moins cher que Sonnet
LLM_MIN_TEXT_LENGTH = 200   # Ne pas appeler le LLM pour des textes trop courts

# Client Bedrock (initialisé paresseusement à la première utilisation)
_bedrock_client = None
def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock_client

# Cache et stats pour la classification LLM
_doc_type_llm_cache = {}    # source_file → doc_type (évite appels redondants)
_llm_stats = {"calls": 0, "hits_cache": 0, "errors": 0, "skipped_short": 0}
_filter_stats = {"files_ok": 0, "files_placeholder": 0, "files_skipped": 0,
                 "chunks_kept": 0, "chunks_filtered": 0, "filter_reasons": {}}

DOC_TYPES_VALID = {
    "RCP", "PV_AG", "CONTRAT", "DEVIS", "FACTURE",
    "BUDGET", "DIAGNOSTIC", "COURRIER", "PLAN", "ASSURANCE",
    "ENTRETIEN", "SINISTRE", "COMPTABILITE"
}

log = logging.getLogger(__name__)


# =====================================================
# Post-traitement OCR léger (patterns non-ambigus)
# =====================================================
def clean_ocr_light(text):
    """
    Nettoyage léger post-OCR. Ne touche QUE les patterns 100% sûrs.
    Filet de sécurité après la reconstruction par WORD blocks.
    """
    if not text:
        return text

    # 1. Recoller les mots coupés par tiret+saut de ligne
    #    "copro-\npriété" → "copropriété"
    text = re.sub(r'(\w)-\s*\n\s*([a-zàâäéèêëïîôùûüÿçœæ])', r'\1\2', text)

    # 2. ALL-CAPS → Titlecase transition sans espace
    #    "REPARTITIONElles" → "REPARTITION Elles"
    text = re.sub(r'(?<=[A-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸ])(?=[A-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸ][a-zàâäéèêëïîôùûüÿçœæ]{2,})', ' ', text)

    # 3. minuscule→MAJUSCULE sans espace
    #    "fixantLe statut" → "fixant Le statut"
    text = re.sub(r'([a-zàâäéèêëïîôùûüÿçœæ])([A-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸÇŒÆ])', r'\1 \2', text)

    # 4. Apostrophe collée après préposition courte
    #    "àl'utilité" → "à l'utilité"  |  "DEL'ETAT" → "DE L'ETAT"
    text = re.sub(r"\b(à|de|du|que|qu|ne|se|ce|je|jusqu|lorsqu|puisqu)(l'|d'|s'|n'|m'|c'|j')", r"\1 \2", text, flags=re.IGNORECASE)

    # 5. Ponctuation collée au mot suivant
    #    "article.Les" → "article. Les"
    text = re.sub(r'([.!?])([A-ZÀÂÄÉÈÊËÏÎÔÙÛÜŸ])', r'\1 \2', text)

    # 6. Deux-points collé (typo FR)
    #    "textes:Article" → "textes : Article"
    text = re.sub(r'(\w)(:)([A-ZÀ-Ÿa-zà-ÿ])', r'\1 \2 \3', text)

    # 7. Doubles espaces
    text = re.sub(r' {2,}', ' ', text)

    return text.strip()

# =====================================================
# Détection du type de document — 3 passes
# =====================================================

def _classify_doc_type_with_llm(text, source_file):
    """
    Passe 3 : demande à Claude Haiku de classifier le document
    à partir des premiers caractères du texte extrait.
    
    Garde-fous :
      - Appelé UNIQUEMENT si passes 1+2 retournent AUTRE
      - Texte minimum 200 caractères (en-dessous, pas assez de signal)
      - Cache par source_file (un fichier = plusieurs chunks, même type)
      - Haiku (~20x moins cher que Sonnet, ~3x plus rapide)
    """
    # Cache : déjà classifié ?
    if source_file in _doc_type_llm_cache:
        _llm_stats["hits_cache"] += 1
        return _doc_type_llm_cache[source_file]

    # Texte trop court → pas la peine d'appeler le LLM
    if len(text.strip()) < LLM_MIN_TEXT_LENGTH:
        _llm_stats["skipped_short"] += 1
        _doc_type_llm_cache[source_file] = "AUTRE"
        return "AUTRE"

    # Envoyer les 1500 premiers caractères (suffisant pour identifier le type)
    excerpt = text[:1500].strip()

    prompt = f"""Tu es un expert en gestion de copropriété. Analyse cet extrait et détermine le type de document.

Types possibles :
- RCP : Règlement de copropriété (articles définissant lots, tantièmes, parties communes/privatives)
- PV_AG : Procès-verbal d'assemblée générale (résolutions votées, feuille de présence, ordre du jour)
- CONTRAT : Contrat ou mandat (mandat de syndic, contrat de maintenance, convention)
- DEVIS : Devis de travaux
- FACTURE : Facture
- BUDGET : Budget prévisionnel, appel de fonds, répartition des charges
- DIAGNOSTIC : Diagnostic technique (DPE, amiante, plomb, termites)
- COURRIER : Courrier, lettre, mise en demeure
- PLAN : Plan d'architecte, plan de masse, schéma technique
- ASSURANCE : Police d'assurance, déclaration de sinistre auprès de l'assureur
- ENTRETIEN : Carnet d'entretien, fiche de maintenance, suivi d'équipements, inventaire des installations
- SINISTRE : Constat de sinistre, bilan d'anomalies, rapport d'expertise, relevé de désordres
- COMPTABILITE : Annexe comptable, grand livre, journal, relevé de compte, état des dépenses
- AUTRE : Aucun des types ci-dessus

Réponds UNIQUEMENT par le code du type (ex: PV_AG). Rien d'autre.

Extrait :
{excerpt}"""

    try:
        bedrock = _get_bedrock()
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": prompt}]
        })
        response = bedrock.invoke_model(
            modelId=CLASSIFIER_MODEL, body=body,
            contentType="application/json", accept="application/json"
        )
        result = json.loads(response["body"].read())
        answer = result["content"][0]["text"].strip().upper()

        _llm_stats["calls"] += 1

        if answer in DOC_TYPES_VALID:
            _doc_type_llm_cache[source_file] = answer
            log.info(f"  🤖 LLM → {answer} pour {os.path.basename(source_file)}")
            return answer

        _doc_type_llm_cache[source_file] = "AUTRE"
        return "AUTRE"

    except Exception as e:
        _llm_stats["errors"] += 1
        log.warning(f"  ⚠️ Classification LLM échouée pour {source_file}: {e}")
        _doc_type_llm_cache[source_file] = "AUTRE"
        return "AUTRE"


def detect_doc_type(filepath, filename, text="", source_file=""):
    """
    Identifie le type de document de copropriété en 3 passes :
      1. Structure des dossiers (match exact par nom de dossier)
      2. Nom du fichier (regex avec word boundaries)
      3. Contenu via Claude Haiku (uniquement si AUTRE après passes 1+2)

    Taxonomie : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC,
                COURRIER, PLAN, ASSURANCE, ENTRETIEN, SINISTRE, COMPTABILITE, AUTRE

    Ordre de priorité dans chaque passe :
      - Les types spécifiques (ENTRETIEN, SINISTRE, COMPTABILITE) avant les types larges
      - PV_AG avant RCP (un PV citant le règlement reste un PV)
      - PLAN en dernier (mot trop courant, source de faux positifs)
    """
    filename_lower = filename.lower()

    # Séparer les composants du chemin pour matcher par dossier
    path_parts = [p.lower() for p in filepath.replace("\\", "/").split("/") if p]

    # ── PASSE 1 : Structure des dossiers (signal le plus fiable) ──
    for part in path_parts:
        # PV d'AG — testé en premier
        if part in ("assemblee", "assemblée", "assemblees", "assemblées",
                     "ag", "pv", "pv_ag", "proces_verbaux"):
            return "PV_AG"

        # Comptabilité — AVANT budget (un dossier COMPTA contient des annexes, pas des budgets)
        if part in ("compta", "comptabilité", "comptabilite", "comptable"):
            return "COMPTABILITE"

        # Entretien / maintenance
        if part in ("entretien", "maintenance"):
            return "ENTRETIEN"

        # Sinistres / expertises
        if part in ("sinistre", "sinistres", "expertise", "expertises", "anomalies"):
            return "SINISTRE"

        if part in ("reglement", "règlement", "rcp", "reglement_copro",
                     "règlement_copropriété", "regl_copro"):
            return "RCP"

        if part in ("contrat", "contrats", "mandat", "mandats", "convention", "conventions"):
            return "CONTRAT"

        if part in ("devis",):
            return "DEVIS"

        if part in ("facture", "factures"):
            return "FACTURE"

        if part in ("budget", "budgets", "appels_de_fonds", "repartition", "répartition"):
            return "BUDGET"

        if part in ("diagnostic", "diagnostics", "dpe", "amiante"):
            return "DIAGNOSTIC"

        if part in ("courrier", "courriers", "correspondance", "lrar"):
            return "COURRIER"

        # PLAN en dernier dans la Passe 1 (mot courant, risque de faux positifs)
        if part in ("plan", "plans", "architecte"):
            return "PLAN"

        if part in ("assurance", "assurances"):
            return "ASSURANCE"

    # ── PASSE 2 : Nom du fichier (word boundaries pour éviter faux positifs) ──
    # Ordre : types spécifiques d'abord, types larges ensuite

    # Entretien / carnet / maintenance
    if re.search(r'\bcarnet\b.*\bentretien\b|\bentretien\b|\bmaintenance\b', filename_lower):
        return "ENTRETIEN"

    # Sinistres, anomalies, constats, expertises, bilans d'anomalies
    if re.search(r'\bsinistres?\b|\banomalies?\b|\bconstat\b|\bexpertise\b|\bbilan\b.*\banomal', filename_lower):
        return "SINISTRE"

    # Comptabilité, annexes comptables, journaux
    if re.search(r'\bannexe\b|\bgrand[\-_\s]?livre\b|\bjournal\b|\bcompta\b', filename_lower):
        return "COMPTABILITE"

    # Appels de fonds exceptionnels → BUDGET
    if re.search(r'\bappel\b.*\bexcept', filename_lower):
        return "BUDGET"

    # PV avant RCP
    if re.search(r'\bpv\b|\bproc[eè]s[\-_\s]?verbal', filename_lower):
        return "PV_AG"
    if re.search(r'\bassembl[eé]e\b|\bag\b', filename_lower):
        return "PV_AG"

    if re.search(r'\br[eè]glement\b|\brcp\b|\bregl[\-_]copro', filename_lower):
        return "RCP"

    if re.search(r'\bcontrat\b|\bmandat\b|\bconvention\b', filename_lower):
        return "CONTRAT"

    if re.search(r'\bdevis\b', filename_lower):
        return "DEVIS"

    if re.search(r'\bfacture\b|\bfact[\-_]', filename_lower):
        return "FACTURE"

    if re.search(r'\bbudget\b|\bappel[\-_\s]de[\-_\s]fond', filename_lower):
        return "BUDGET"

    if re.search(r'\bdiagnostic\b|\bdpe\b|\bamiante\b|\bplomb\b|\btermite\b', filename_lower):
        return "DIAGNOSTIC"

    if re.search(r'\bcourrier\b|\blettre\b|\blrar\b|\bmise[\-_\s]en[\-_\s]demeure', filename_lower):
        return "COURRIER"

    if re.search(r'\bassurance\b|\bpolice\b', filename_lower):
        return "ASSURANCE"

    # PLAN en dernier (mot "plan" très courant, source de faux positifs)
    if re.search(r'\bplan\b|\bpln\b|\barchi\b', filename_lower):
        return "PLAN"

    # ── PASSE 3 : Classification par contenu via LLM (dernier recours) ──
    if text and source_file:
        return _classify_doc_type_with_llm(text, source_file)

    return "AUTRE"

# =====================================================
# Fonctions de chunking par type
# =====================================================

def split_long_paragraph(text, max_size):
    """Découpe un long paragraphe en respectant les frontières de phrases."""
    # Chercher les points de coupure naturels (phrases)
    sentence_ends = list(re.finditer(r'[.!?]\s+', text))
    
    if not sentence_ends:
        # Pas de phrases détectées -> découpe brute avec overlap
        pieces = []
        for i in range(0, len(text), max_size - CHUNK_OVERLAP):
            pieces.append(text[i:i + max_size])
        return pieces
    
    pieces = []
    start = 0
    current_end = 0
    
    for match in sentence_ends:
        candidate_end = match.end()
        if candidate_end - start <= max_size:
            current_end = candidate_end
        else:
            # On a dépassé la taille max -> sauver le chunk jusqu'au dernier point valide
            if current_end > start:
                pieces.append(text[start:current_end].strip())
                start = current_end
                current_end = candidate_end
            else:
                # Phrase unique trop longue -> découpe brute
                pieces.append(text[start:start + max_size].strip())
                start = start + max_size - CHUNK_OVERLAP
                current_end = candidate_end
    
    # Dernier morceau
    if start < len(text):
        remaining = text[start:].strip()
        if remaining:
            pieces.append(remaining)
    
    return pieces


def enforce_max_size(chunks):
    """Garde-fou final : garantit qu'AUCUN chunk ne dépasse CHUNK_HARD_MAX."""
    safe_chunks = []
    for chunk in chunks:
        if len(chunk) <= CHUNK_HARD_MAX:
            safe_chunks.append(chunk)
        else:
            # Tenter un découpage intelligent par phrases
            pieces = split_long_paragraph(chunk, CHUNK_HARD_MAX)
            for piece in pieces:
                if len(piece) <= CHUNK_HARD_MAX:
                    safe_chunks.append(piece)
                else:
                    # Dernier recours : découpe brute absolue
                    for i in range(0, len(piece), CHUNK_HARD_MAX):
                        safe_chunks.append(piece[i:i + CHUNK_HARD_MAX])
    return safe_chunks


def chunk_by_articles(text):
    """
    Découpe un RCP ou contrat par articles — version robuste OCR.
    
    Gère les artefacts Textract : "Artic1e", "ARTIC LE", "Art. 24",
    retours à la ligne parasites, accents perdus.
    """
    # Pré-nettoyage OCR
    cleaned = re.sub(r'(\w)\n(\w)', r'\1\2', text)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    
    patterns = [
        # Pattern 1 : "Article 24", "ARTICLE 24", "Art. 24" (tolérant OCR)
        r'(?=(?:^|\n)\s*(?:[Aa][Rr][Tt][Ii1][Cc][Ll1][Ee]|[Aa][Rr][Tt]\.?)\s*\d+)',
        
        # Pattern 2 : "CHAPITRE I", "Chapitre 3", "TITRE II"
        r'(?=(?:^|\n)\s*(?:CHAPITRE|Chapitre|TITRE|Titre)\s*(?:[IVXLC]+|\d+))',
        
        # Pattern 3 : numérotation juridique "1°", "2°", "1 -", "2 -"
        r'(?=(?:^|\n)\s*\d{1,3}\s*[°\-]\s+[A-ZÉÈÀÊ])',
    ]
    
    best_chunks = []
    
    for pattern in patterns:
        try:
            parts = re.split(pattern, cleaned, flags=re.MULTILINE)
        except re.error:
            continue
        
        meaningful = [p.strip() for p in parts if p and len(p.strip()) >= 30]
        
        if len(meaningful) >= 3 and len(meaningful) > len(best_chunks):
            best_chunks = meaningful
    
    if not best_chunks:
        return chunk_by_size(cleaned)
    
    chunks = []
    for part in best_chunks:
        if len(part) <= CHUNK_MAX_SIZE:
            chunks.append(part)
        else:
            chunks.extend(chunk_by_size(part))
    
    return enforce_max_size(chunks)

def chunk_by_resolutions(text):
    """
    Découpe un PV d'AG par résolutions — version robuste OCR.
    
    Gère les artefacts Textract courants sur les PV scannés :
      - Mots cassés/espacés : "Résol ution", "R ésolution"
      - Accents perdus ou altérés : "Resolution", "Reésolution"  
      - Symbole N° dégradé : "N'", "N °", "No", "N*"
      - Formats alternatifs : "1ère résolution", "PREMIERE RESOLUTION",
        "Point 1", "Point n°2", numérotation simple "1)", "I."
      - Retours à la ligne parasites dans les mots
    """
    # Pré-nettoyage : recoller les mots cassés par un \n parasite au milieu
    # Ex: "Résolu\ntion" → "Résolution", "assem\nblée" → "assemblée"
    cleaned = re.sub(r'(\w)\n(\w)', r'\1\2', text)
    
    # Normaliser les espaces multiples (OCR produit souvent "Résolution    N°  3")
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    
    # Patterns de détection de résolutions, du plus spécifique au plus générique
    # On essaie chaque pattern et on prend celui qui produit le meilleur découpage
    patterns = [
        # Pattern 1 : "Résolution N° 3" (forme standard, tolérant OCR)
        r'(?=(?:^|\n)\s*[RrÉéEe][ée]?s[oö0]l[ue]t[il1][oö0][nm]\s*(?:N[°\'*oO ]?|n[°\'*oO ]?|#)?\s*\d+)',
        
        # Pattern 2 : ordinal "PREMIERE RESOLUTION", "1ère résolution", "2ème résolution"
        r'(?=(?:^|\n)\s*(?:\d+\s*[èeé][mr]e\s+r[ée]solution|(?:PREMI[ÈE]RE|DEUXI[ÈE]ME|TROISI[ÈE]ME|QUATRI[ÈE]ME|CINQUI[ÈE]ME|SIXI[ÈE]ME|SEPTI[ÈE]ME|HUITI[ÈE]ME|NEUVI[ÈE]ME|DIXI[ÈE]ME)\s+R[ÉE]SOLUTION))',
        
        # Pattern 3 : "Point 1", "Point n°2"
        r'(?=(?:^|\n)\s*[Pp]oint\s*(?:N[°\'*oO ]?|n[°\'*oO ]?|#)?\s*\d+)',
        
        # Pattern 4 : numérotation simple en début de ligne "1)", "2)", "3)" 
        # (seulement si on trouve au moins 3 occurrences pour éviter les faux positifs)
        r'(?=(?:^|\n)\s*\d{1,2}\s*[)\.]\s+[A-ZÉÈÀÊ])',
    ]
    
    best_chunks = []
    
    for pattern in patterns:
        try:
            parts = re.split(pattern, cleaned, flags=re.IGNORECASE | re.MULTILINE)
        except re.error:
            continue
        
        # Filtrer les parties vides ou trop courtes
        meaningful = [p.strip() for p in parts if p and len(p.strip()) >= 30]
        
        # Un bon découpage produit au moins 3 parties (intro + 2 résolutions minimum)
        if len(meaningful) >= 3 and len(meaningful) > len(best_chunks):
            best_chunks = meaningful
    
    if not best_chunks:
        # Aucun pattern n'a matché → fallback par taille
        return chunk_by_size(cleaned)
    
    # Post-traitement : respecter les limites de taille
    chunks = []
    for part in best_chunks:
        if len(part) <= CHUNK_MAX_SIZE:
            chunks.append(part)
        else:
            chunks.extend(chunk_by_size(part))
    
    return enforce_max_size(chunks)

def chunk_by_size(text):
    """Découpe générique par taille avec overlap — pour documents courts ou sans structure."""
    if len(text) <= CHUNK_TARGET_SIZE:
        return enforce_max_size([text])
    
    chunks = []
    # Découper par paragraphes d'abord (\n\n), sinon par lignes (\n)
    paragraphs = text.split("\n\n")
    
    # Fallback : si \n\n ne produit pas de découpage utile (cas fréquent avec
    # Textract OCR qui sépare par \n simple), on découpe par lignes
    if len(paragraphs) <= 1 or max(len(p) for p in paragraphs) > CHUNK_HARD_MAX:
        paragraphs = text.split("\n")
    
    current_chunk = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        
        # Si le paragraphe seul dépasse CHUNK_HARD_MAX, le découper d'abord
        if len(para) > CHUNK_HARD_MAX:
            # Flusher le chunk en cours avant
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            chunks.extend(split_long_paragraph(para, CHUNK_TARGET_SIZE))
            continue
        
        if len(current_chunk) + len(para) + 2 <= CHUNK_TARGET_SIZE:
            current_chunk += "\n\n" + para if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
                # Overlap : garder les derniers N caractères
                overlap = current_chunk[-CHUNK_OVERLAP:] if len(current_chunk) > CHUNK_OVERLAP else ""
                current_chunk = overlap + "\n\n" + para
            else:
                # Paragraphe seul trop long -> découpe par phrases
                chunks.extend(split_long_paragraph(para, CHUNK_TARGET_SIZE))
                current_chunk = ""
        
        # Sécurité : si le chunk en cours dépasse CHUNK_HARD_MAX, le flusher
        if len(current_chunk) > CHUNK_HARD_MAX:
            chunks.extend(split_long_paragraph(current_chunk, CHUNK_TARGET_SIZE))
            current_chunk = ""
    
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    # Garde-fou final — TOUJOURS appelé
    return enforce_max_size(chunks)

def chunk_whole_document(text):
    """Garde le document entier si assez court, sinon découpe par taille."""
    if len(text) <= CHUNK_TARGET_SIZE:
        return enforce_max_size([text])
    return chunk_by_size(text)

# Mapping type -> stratégie
CHUNKING_STRATEGY = {
    "RCP": chunk_by_articles,
    "PV_AG": chunk_by_resolutions,
    "CONTRAT": chunk_by_articles,
    "DEVIS": chunk_whole_document,
    "FACTURE": chunk_whole_document,
    "BUDGET": chunk_whole_document,
    "DIAGNOSTIC": chunk_by_size,
    "COURRIER": chunk_whole_document,
    "PLAN": chunk_whole_document,
    "ASSURANCE": chunk_by_articles,
    "ENTRETIEN": chunk_by_size,           # structure variable (tableaux, listes d'équipements)
    "SINISTRE": chunk_by_size,            # constats, rapports d'expertise
    "COMPTABILITE": chunk_whole_document,  # tableaux chiffrés, garder le contexte
    "AUTRE": chunk_by_size,
}

# =====================================================
# Exécution
# =====================================================
# Nettoyage de l'ancien fichier de chunks
if os.path.exists(OUTPUT_FILE):
    print(f"Nettoyage de l'ancien fichier : {OUTPUT_FILE}")
    os.remove(OUTPUT_FILE)

print("=" * 50)
print("CHUNKING INTELLIGENT DES DOCUMENTS")
print("=" * 50)

# Charger tous les fichiers JSON extraits
json_files = []
if os.path.exists(EXTRACTED_DIR):
    for root, dirs, filenames in os.walk(EXTRACTED_DIR):
        for fname in filenames:
            if fname.endswith(".json"):
                json_files.append(os.path.join(root, fname))
else:
    print(f"Le dossier {EXTRACTED_DIR} n'existe pas. Lance d'abord l'etape 02.")
    import sys
    sys.exit(1)

print(f"\n{len(json_files)} fichiers a chunker\n")

total_chunks = 0
doc_type_stats = {}

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    for json_path in tqdm(json_files, desc="Chunking"):
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        
        text = doc.get("texte", "")
        if len(text.strip()) < 30:
            continue
        
        # Post-traitement OCR léger (patterns non-ambigus)
        text = clean_ocr_light(text)
        
        # ── FILTRE CONTENU : vérifier que le texte est exploitable ──
        source_file = doc.get("source_file", "")
        nom_fichier = doc.get("nom_fichier", "")
        copropriete = doc.get("copropriete", "")
        
        quality = analyze_file_quality(text, nom_fichier)
        
        if quality["verdict"] == "SKIP":
            _filter_stats["files_skipped"] += 1
            continue
        
        # Détecter le type de document (3 passes : dossier → nom → LLM si nécessaire)
        doc_type = detect_doc_type(
            doc.get("dossier_parent", ""),
            nom_fichier,
            text=text,
            source_file=source_file
        )
        doc_type_stats[doc_type] = doc_type_stats.get(doc_type, 0) + 1
        
        if quality["verdict"] == "PLACEHOLDER":
            _filter_stats["files_placeholder"] += 1
            placeholder = make_placeholder_chunk(
                source_file, nom_fichier, copropriete, doc_type, len(text)
            )
            chunk_id = hashlib.md5(f"{source_file}_placeholder".encode()).hexdigest()[:12]
            placeholder["chunk_id"] = chunk_id
            out.write(json.dumps(placeholder, ensure_ascii=False) + "\n")
            total_chunks += 1
            continue
        
        _filter_stats["files_ok"] += 1
        
        # Appliquer la stratégie de chunking
        chunker = CHUNKING_STRATEGY[doc_type]
        chunks = chunker(text)
        
        # ── FILTRE POST-CHUNKING : éliminer les chunks individuels non exploitables ──
        chunks_before = len(chunks)
        chunk_dicts = [{"text": c} for c in chunks]
        filtered_dicts, fstats = filter_chunks(chunk_dicts)
        chunks = [d["text"] for d in filtered_dicts]
        
        _filter_stats["chunks_kept"] += fstats["kept"]
        _filter_stats["chunks_filtered"] += fstats["filtered"]
        for reason, count in fstats["reasons"].items():
            _filter_stats["filter_reasons"][reason] = _filter_stats["filter_reasons"].get(reason, 0) + count
        
        if fstats["filtered"] > 0:
            tqdm.write(f"  ⛔ {nom_fichier}: {fstats['filtered']}/{chunks_before} chunks filtrés")
        
        # Si tous les chunks ont été filtrés, créer un placeholder
        if not chunks:
            placeholder = make_placeholder_chunk(
                source_file, nom_fichier, copropriete, doc_type, len(text)
            )
            chunk_id = hashlib.md5(f"{source_file}_placeholder".encode()).hexdigest()[:12]
            placeholder["chunk_id"] = chunk_id
            out.write(json.dumps(placeholder, ensure_ascii=False) + "\n")
            total_chunks += 1
            _filter_stats["files_placeholder"] += 1
            continue
        
        # Écrire chaque chunk avec ses métadonnées
        for i, chunk_text in enumerate(chunks):
            chunk_id = hashlib.md5(f"{doc['source_file']}_{i}".encode()).hexdigest()[:12]
            
            chunk_record = {
                "chunk_id": chunk_id,
                "copropriete": doc.get("copropriete", ""),
                "source_file": doc.get("source_file", ""),
                "nom_fichier": doc.get("nom_fichier", ""),
                "doc_type": doc_type,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "text": chunk_text,
                "nb_caracteres": len(chunk_text)
            }
            
            out.write(json.dumps(chunk_record, ensure_ascii=False) + "\n")
            total_chunks += 1

# =====================================================
# Rapport
# =====================================================
print("\n" + "=" * 50)
print("RAPPORT DE CHUNKING")
print("=" * 50)
print(f"\nTotal chunks generes : {total_chunks}")
print(f"\nPar type de document :")
for doc_type, count in sorted(doc_type_stats.items(), key=lambda x: -x[1]):
    print(f"  {doc_type:20s} : {count:5d} documents")

print(f"\n--- Filtre contenu binaire ---")
print(f"  Fichiers OK (chunkés)    : {_filter_stats['files_ok']}")
print(f"  Fichiers → placeholder   : {_filter_stats['files_placeholder']}")
print(f"  Fichiers ignorés (SKIP)  : {_filter_stats['files_skipped']}")
print(f"  Chunks gardés            : {_filter_stats['chunks_kept']}")
print(f"  Chunks filtrés           : {_filter_stats['chunks_filtered']}")
if _filter_stats['filter_reasons']:
    print(f"  Raisons de filtrage :")
    for reason, count in sorted(_filter_stats['filter_reasons'].items(), key=lambda x: -x[1]):
        print(f"    {reason:30s} : {count:5d}")

print(f"\n--- Classification LLM (Passe 3) ---")
print(f"  Appels Bedrock Haiku : {_llm_stats['calls']}")
print(f"  Cache hits           : {_llm_stats['hits_cache']}")
print(f"  Skippés (texte court): {_llm_stats['skipped_short']}")
print(f"  Erreurs              : {_llm_stats['errors']}")
if _llm_stats['calls'] > 0:
    print(f"  Coût estimé          : ~${_llm_stats['calls'] * 1500 * 0.0000008:.4f}")

print(f"\n-> Chunks : {OUTPUT_FILE}")
