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
import time
import hashlib
import logging
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
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
CHUNK_MAX_SIZE = 5000       # Maximum souple (articles/résolutions) — aligné sur CHUNK_HARD_MAX
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

    # ── PASSE 0 : Cache pré-scan (corrections Haiku du pré-scan parallèle) ──
    # Priorité absolue : si le pré-scan a vérifié/corrigé ce fichier, utiliser son résultat
    if source_file and source_file in _doc_type_llm_cache:
        return _doc_type_llm_cache[source_file]

    # Séparer les composants du chemin pour matcher par dossier
    path_parts = [p.lower() for p in filepath.replace("\\", "/").split("/") if p]

    # ── PASSE 1 : Structure des dossiers (signal le plus fiable) ──
    _in_assemblee_folder = False
    for part in path_parts:
        # Dossier AG/ASSEMBLEE → marquer mais NE PAS retourner PV_AG directement.
        # Le dossier contient aussi convocations, ODJ, VPC, annexes, contrats syndic...
        # On laisse la Passe 2 (nom du fichier) qualifier le type exact.
        if part in ("assemblee", "assemblée", "assemblees", "assemblées",
                     "ag", "pv", "pv_ag", "proces_verbaux"):
            _in_assemblee_folder = True
            continue  # ne pas retourner tout de suite

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

    # --- Fichiers dans un dossier ASSEMBLEE : seuls les vrais PV sont identifiés par nom ---
    # Tout le reste (convocations, ODJ, VPC, annexes, AR, etc.) tombe en AUTRE → Haiku classifie
    # Cela évite une liste interminable de regex fragiles.

    # Entretien / carnet / maintenance
    if re.search(r'\bcarnet\b.*\bentretien\b|\bentretien\b|\bmaintenance\b', filename_lower):
        return "ENTRETIEN"

    # Sinistres, anomalies, constats, expertises, bilans d'anomalies
    if re.search(r'\bsinistres?\b|\banomalies?\b|\bconstat\b|\bexpertise\b|\bbilan\b.*\banomal', filename_lower):
        return "SINISTRE"

    # Comptabilité, annexes comptables, journaux, comptes, charges, balance
    if re.search(r'\bannexe\b|\bgrand[\-_\s]?livre\b|\bjournal\b|\bcompta\b|\bcomptes?\b|\bcharges?\s+de\s+copro|\bbalance\b', filename_lower):
        return "COMPTABILITE"

    # Appels de fonds exceptionnels → BUDGET
    if re.search(r'\bappel\b.*\bexcept', filename_lower):
        return "BUDGET"

    # PV d'AG — uniquement si le nom contient explicitement PV ou procès-verbal
    # Note : les ODJ/convocations dans un dossier AG sont aussi classés PV_AG ici,
    # mais la vérification Haiku (_verify_pvag) les reclassifiera en COURRIER
    # en analysant le contenu (présence/absence de résultats de vote).
    # Match : "PV AG", "PV 2018", "PVAG", "PV AGO", "PV signe", "PV D AG", "proces verbal"
    # Exclut : PV_AR (accusé réception), PV_SIMPLES (envoi), PV_DESTINATAIRES (déjà filtré plus haut)
    if re.search(r'\bpvag\b|\bpv\b|\bproc[eè]s[\-_\s]?verbal', filename_lower):
        return "PV_AG"

    # Contrat (dont contrat syndic)
    if re.search(r'\bcontrat\b|\bmandat\b|\bconvention\b', filename_lower):
        return "CONTRAT"

    # Devis
    if re.search(r'\bdevis\b', filename_lower):
        return "DEVIS"

    # RCP / Règlement de copropriété
    if re.search(r'\br[eè]glement\b|\brcp\b|\bregl[\-_]copro', filename_lower):
        return "RCP"

    if re.search(r'\bfacture\b|\bfact[\-_]', filename_lower):
        return "FACTURE"

    if re.search(r'\bbudget\b|\bappel[\-_\s]de[\-_\s]fond', filename_lower):
        return "BUDGET"

    if re.search(r'\bdiagnostic\b|\bdpe\b|\bamiante\b|\bplomb\b|\btermite\b', filename_lower):
        return "DIAGNOSTIC"

    if re.search(r'\bassurance\b|\bpolice\b', filename_lower):
        return "ASSURANCE"

    # PLAN en dernier (mot "plan" très courant, source de faux positifs)
    if re.search(r'\bplan\b|\bpln\b|\barchi\b', filename_lower):
        return "PLAN"

    # Si dans un dossier ASSEMBLEE mais nom non reconnu → AUTRE (sera classifié par Haiku)
    # NE PLUS retourner PV_AG par défaut
    # (tombe en Passe 3 ci-dessous ou retourne AUTRE)

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

_haiku_pattern_stats = {"calls": 0, "success": 0, "fail": 0}

def _detect_resolution_format_haiku(text):
    """Appelle Haiku pour identifier le format de numérotation des résolutions d'un PV d'AG.
    Retourne une liste de chunks si réussi, [] sinon."""
    
    # Envoyer les premiers ~3000 chars — suffisant pour voir le format
    sample = text[:3000]
    
    prompt = f"""Voici le début d'un procès-verbal d'assemblée générale de copropriété.
Identifie le FORMAT DE NUMÉROTATION utilisé pour les résolutions ou points à l'ordre du jour.

Exemples de formats possibles :
- "5- Approbation des comptes" (chiffre + tiret)
- "Résolution N°5 : Approbation" (mot Résolution + numéro)
- "CINQUIEME RESOLUTION" (ordinal en toutes lettres)
- "Point 5 - Approbation" (mot Point + numéro)
- "Article 5 : Approbation" (mot Article + numéro)

Réponds UNIQUEMENT par un objet JSON valide :
{{"format": "description courte du format trouvé", "separator_regex": "un pattern regex Python qui matche le DÉBUT de chaque résolution, à utiliser avec re.split() en mode MULTILINE. Le pattern doit commencer par (?=(?:^|\\n)) pour découper sans perdre le texte.", "exemple": "un exemple exact trouvé dans le texte", "count": nombre_de_résolutions_détectées}}

Si tu ne trouves aucun format de numérotation structuré, réponds : {{"format": null}}

Texte :
{sample}"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}]
    })

    try:
        bedrock = _get_bedrock()
        response = bedrock.invoke_model(
            modelId=CLASSIFIER_MODEL, body=body,
            contentType="application/json", accept="application/json"
        )
        result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
        
        # Extraire le JSON (Haiku peut ajouter du texte autour)
        result_text = re.sub(r"^```json?\s*", "", result_text)
        result_text = re.sub(r"\s*```$", "", result_text)
        start = result_text.find("{")
        end = result_text.rfind("}") + 1
        if start >= 0 and end > start:
            result_text = result_text[start:end]
        
        result = json.loads(result_text)
        _haiku_pattern_stats["calls"] += 1
        
        if not result.get("format") or not result.get("separator_regex"):
            _haiku_pattern_stats["fail"] += 1
            return []
        
        # Tester le regex retourné par Haiku
        haiku_regex = result["separator_regex"]
        parts = re.split(haiku_regex, text, flags=re.IGNORECASE | re.MULTILINE)
        meaningful = [p.strip() for p in parts if p and len(p.strip()) >= 30]
        
        if len(meaningful) >= 3:
            _haiku_pattern_stats["success"] += 1
            return meaningful
        else:
            _haiku_pattern_stats["fail"] += 1
            return []
    
    except (json.JSONDecodeError, re.error) as e:
        _haiku_pattern_stats["calls"] += 1
        _haiku_pattern_stats["fail"] += 1
        return []
    except Exception as e:
        # Throttling, réseau — pas de retry ici (l'appelant fera le fallback)
        _haiku_pattern_stats["calls"] += 1
        _haiku_pattern_stats["fail"] += 1
        return []


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
        
        # Pattern 4 : "5- Approbation", "5 - Approbation" (format NCG typique des PV d'AG)
        r'(?=(?:^|\n)\s*\d{1,2}\s*[-–—]\s*[A-ZÉÈÀÊa-zéèàê])',
        
        # Pattern 5 : numérotation simple "1)", "2)", "3)"
        # (seulement si on trouve au moins 3 occurrences pour éviter les faux positifs)
        r'(?=(?:^|\n)\s*\d{1,2}\s*[)\.]\s+[A-ZÉÈÀÊ])',

        # Pattern 6 : "1 ELECTION DU PRESIDENT", "18 PROJET D'INSTALLATION"
        # (chiffre + espace + TITRE EN MAJUSCULES sans tiret — format NCG courant)
        # Placé en dernier car plus large que les autres → priorité basse
        r'(?=(?:^|\n)\s*\d{1,2}\s+[A-ZÉÈÀÊ]{3,})',
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
        # Aucun pattern regex n'a matché → demander à Haiku d'identifier le format
        best_chunks = _detect_resolution_format_haiku(cleaned)
    
    if not best_chunks:
        # Haiku n'a pas trouvé non plus → fallback par taille
        return chunk_by_size(cleaned)
    
    # Post-traitement : respecter les limites de taille
    # Quand une résolution est subdivisée, préserver le numéro et le verdict
    chunks = []
    for part in best_chunks:
        if len(part) <= CHUNK_MAX_SIZE:
            chunks.append(part)
        else:
            # Extraire le header (numéro + titre de la résolution)
            header_match = re.match(
                r'(\d{1,2}\s*[-–—]?\s*.{5,80}?)(?:\n|Type de vote)',
                part
            )
            header = header_match.group(1).strip() if header_match else ""
            # Extraire le verdict (dernière phrase "adoptée/rejetée")
            verdict_match = re.search(
                r'(En vertu de quoi.*?(?:adopt[ée]e|rejet[ée]e).*?\.)',
                part, re.IGNORECASE
            )
            verdict = verdict_match.group(1).strip() if verdict_match else ""

            sub_chunks = chunk_by_size(part)
            for j, sc in enumerate(sub_chunks):
                # Préfixer avec le header si pas déjà présent
                if header and not sc.strip().startswith(header[:20]):
                    sc = f"[Suite résolution {header}]\n{sc}"
                # Suffixer le dernier sous-chunk avec le verdict si manquant
                if j == len(sub_chunks) - 1 and verdict and verdict not in sc:
                    sc = f"{sc}\n{verdict}"
                chunks.append(sc)

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
    """Garde le document entier si < CHUNK_HARD_MAX (5000 chars), sinon découpe par taille."""
    if len(text) <= CHUNK_HARD_MAX:
        return enforce_max_size([text])
    return chunk_by_size(text)

# =====================================================
# Classification des résolutions PV_AG (Phase 1a)
# =====================================================
_PROCEDURE_PATTERNS = [
    # Résolutions de bureau (président, scrutateur, secrétaire de séance)
    re.compile(r"[Dd]ésignation\s+(?:du|des|de\s+la)\s+(?:Pr[ée]sident|Scrutateur|Secr[ée]taire)\s+de\s+s[ée]ance", re.IGNORECASE),
    re.compile(r"[ée]lection\s+(?:du|des)\s+(?:Pr[ée]sident|Scrutateur|Secr[ée]taire)", re.IGNORECASE),
    # Approbation des comptes
    re.compile(r"[Aa]pprobation\s+des\s+comptes", re.IGNORECASE),
    # Quitus au syndic
    re.compile(r"[Qq]uitus.*[Ss]yndic|[Dd]onner\s+quitus", re.IGNORECASE),
    # Désignation / renouvellement du syndic
    re.compile(r"[Dd]ésignation.*[Ss]yndic.*qualit[ée]|[Rr]enouvellement.*mandat.*[Ss]yndic|[Dd]ésignation\s+à\s+nouveau.*[Ss]yndic", re.IGNORECASE),
    re.compile(r"RENOUVELLEMENT\s+DU\s+MANDAT.*SYNDIC", re.IGNORECASE),
    # Autorisation Police / Gendarmerie
    re.compile(r"[Aa]utorisation.*[Pp]olice.*[Gg]endarmerie|p[ée]n[ée]trer\s+dans\s+les\s+parties\s+communes", re.IGNORECASE),
    # Budget prévisionnel (approbation/ajustement récurrent)
    re.compile(r"[Aa]pprobation\s+du\s+budget\s+pr[ée]visionnel|[Aa]justement\s+du\s+budget\s+pr[ée]visionnel", re.IGNORECASE),
    # Honoraires syndic / contrat de mandat
    re.compile(r"[Hh]onoraires.*[Ss]yndic|contrat\s+de\s+mandat", re.IGNORECASE),
    # Modalités de contrôle/vérification des comptes
    re.compile(r"modalit[ée]s.*contr[ôo]le\s+des\s+comptes|v[ée]rification\s+des\s+comptes", re.IGNORECASE),
    # Fonds de travaux ALUR (cotisation annuelle obligatoire)
    re.compile(r"cotisation\s+annuelle\s+obligatoire\s+du\s+fonds\s+de\s+travaux", re.IGNORECASE),
    # Seuils consultation CS / mise en concurrence (récurrent)
    re.compile(r"montant\s+des\s+march[ée]s.*consultation\s+du\s+conseil\s+syndical", re.IGNORECASE),
    re.compile(r"montant\s+des\s+march[ée]s.*mise\s+en\s+concurrence.*obligatoire", re.IGNORECASE),
]
_ELECTION_PATTERNS = [
    re.compile(r"(?:RENOUVELLEMENT|ELECTION|ÉLECTION).*MEMBRES?\s+(?:DU|DE)\s+CONSEIL\s+SYNDICAL", re.IGNORECASE),
    re.compile(r"MEMBRE\s+TITULAIRE", re.IGNORECASE),
    re.compile(r"MEMBRE\s+SUPPL[EÉ]ANT", re.IGNORECASE),
    re.compile(r"[Cc]andidature\s+de\s+(?:Monsieur|Madame|M\.|Mme|Mlle)\s+\w+", re.IGNORECASE),
]
_GOUVERNANCE_PATTERNS = [
    re.compile(r"[Rr]apport\s+d.activit[eé]\s+du\s+[Cc]onseil\s+[Ss]yndical", re.IGNORECASE),
    re.compile(r"[Dd]ispense\s+.*mise\s+en\s+concurrence", re.IGNORECASE),
]

def classify_resolution_category(chunk_text, doc_type, chunk_index):
    """
    Classifie un chunk PV_AG en catégorie de résolution.
    Retourne : PROCEDURE_AG, ELECTION_CS, GOUVERNANCE, FOND, ou None (non PV_AG).

    chunk_index=0 est le préambule → toujours PROCEDURE_AG (feuille de présence, quorum).
    """
    if doc_type != "PV_AG":
        return None

    if chunk_index == 0:
        return "PROCEDURE_AG"

    text_upper = chunk_text[:500]  # Les patterns sont en début de résolution

    for pat in _PROCEDURE_PATTERNS:
        if pat.search(text_upper):
            return "PROCEDURE_AG"

    for pat in _ELECTION_PATTERNS:
        if pat.search(text_upper):
            return "ELECTION_CS"

    for pat in _GOUVERNANCE_PATTERNS:
        if pat.search(text_upper):
            return "GOUVERNANCE"

    return "FOND"

_resolution_category_stats = {}

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

# =====================================================
# Pré-scan : classification Haiku en parallèle
# =====================================================
# Phase 1 : identifier les fichiers qui nécessitent un appel Haiku
# - Fichiers AUTRE après passes 1+2 (classification)
# - Fichiers PV_AG par dossier (vérification : est-ce vraiment un PV ou un OJ/convocation ?)
print("⏳ Pré-scan : identification des fichiers nécessitant classification LLM...")
files_needing_llm = []       # (source_file, text_excerpt, "classify")
files_needing_verify = []    # (source_file, text_excerpt, "verify_pvag")
_verify_stats = {"verified": 0, "reclassified": 0}

# Regex pour détecter la présence de résultats de vote (signature d'un vrai PV d'AG)
_VOTE_RESULT_RE = re.compile(
    r"adopt[ée]e?\s|rejet[ée]e?\s|tanti[eè]mes|"
    r"votent\s+pour|votent\s+contre|pour\s*:\s*\d+.*contre\s*:\s*\d+|"
    r"unanimit[ée]|abstentions?\s*:\s*\d+",
    re.IGNORECASE
)

def _build_smart_excerpt(text, max_chars=2000):
    """Construit un extrait multi-positions en ignorant le début (en-tête/bla bla juridique)
    et la fin (signature). Prend des échantillons au 1er quart, milieu et 3e quart."""
    n = len(text)
    if n <= max_chars:
        return text.strip()

    chunk_size = max_chars // 3  # ~666 chars par échantillon

    # Ignorer les 10% premiers (en-tête) et 10% derniers (signature)
    start = max(0, n // 10)
    end = min(n, n - n // 10)
    usable = end - start

    if usable <= max_chars:
        return text[start:end].strip()

    # 3 échantillons : 1er quart, milieu, 3e quart de la zone utile
    pos1 = start
    pos2 = start + usable // 2 - chunk_size // 2
    pos3 = end - chunk_size

    excerpt = (
        text[pos1:pos1 + chunk_size].strip()
        + "\n[...]\n"
        + text[pos2:pos2 + chunk_size].strip()
        + "\n[...]\n"
        + text[pos3:pos3 + chunk_size].strip()
    )
    return excerpt


def _build_pvag_excerpt(text, max_chars=2000):
    """Extrait optimisé pour distinguer PV d'AG vs ODJ/convocation.
    Prend le DÉBUT (en-tête, titre) + la FIN (verdicts adoptée/rejetée).
    C'est plus discriminant que l'extrait générique car :
    - Le début dit "PROCÈS VERBAL" ou "ORDRE DU JOUR"
    - La fin contient les verdicts (adoptée/rejetée) dans un PV, absents d'un ODJ
    """
    n = len(text)
    if n <= max_chars:
        return text.strip()

    half = max_chars // 2
    debut = text[:half].strip()
    fin = text[-(half):].strip()
    return debut + "\n[...]\n" + fin

for json_path in tqdm(json_files, desc="Pré-scan"):
    with open(json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    text = doc.get("texte", "")
    if len(text.strip()) < 30:
        continue

    source_file = doc.get("source_file", "")
    nom_fichier = doc.get("nom_fichier", "")

    # Passe 1+2 seulement (pas de LLM)
    doc_type = detect_doc_type(
        doc.get("dossier_parent", ""),
        nom_fichier,
        text="",  # Pas de texte → pas de passe 3
        source_file=source_file
    )

    if doc_type == "AUTRE" and len(text.strip()) >= LLM_MIN_TEXT_LENGTH:
        files_needing_llm.append((source_file, text[:1500].strip()))
    elif doc_type == "PV_AG" and len(text.strip()) >= LLM_MIN_TEXT_LENGTH:
        # Pré-filtre rapide : si aucun résultat de vote dans tout le texte, ce n'est PAS un PV
        if not _VOTE_RESULT_RE.search(text):
            _doc_type_llm_cache[source_file] = "COURRIER"  # Reclassifier directement
            _verify_stats["reclassified"] = _verify_stats.get("reclassified", 0) + 1
            continue
        # Tous les PV_AG passent par la vérification Haiku (plus de bypass "obvious")
        # On envoie le texte complet — _verify_pvag construit l'extrait optimisé (début+fin)
        files_needing_verify.append((source_file, text))

# Compter les reclassifications par pré-filtre regex (pas de vote = pas un PV)
regex_reclassified = sum(1 for sf in _doc_type_llm_cache.values() if sf == "COURRIER")
print(f"  {regex_reclassified} fichiers PV_AG reclassifiés par pré-filtre regex (aucun vote détecté)")
print(f"  {len(files_needing_llm)} fichiers AUTRE → classification Haiku")
print(f"  {len(files_needing_verify)} fichiers PV_AG → vérification Haiku")

# Phase 2 : appels Haiku en parallèle (classification + vérification PV_AG)
all_haiku_tasks = len(files_needing_llm) + len(files_needing_verify)

if all_haiku_tasks > 0:
    _classify_lock = threading.Lock()
    _thread_local_classify = threading.local()
    
    def _get_bedrock_thread():
        if not hasattr(_thread_local_classify, "client"):
            _thread_local_classify.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        return _thread_local_classify.client
    
    def _classify_one(source_file, excerpt):
        """Classifie un document AUTRE via Haiku. Thread-safe."""
        prompt = f"""Tu es un expert en gestion de copropriété. Analyse cet extrait et détermine le type de document.

Types possibles :
- RCP : Règlement de copropriété
- PV_AG : Procès-verbal d'assemblée générale
- CONTRAT : Contrat ou mandat
- DEVIS : Devis de travaux
- FACTURE : Facture
- BUDGET : Budget prévisionnel
- DIAGNOSTIC : Diagnostic technique
- COURRIER : Courrier, lettre
- PLAN : Plan d'architecte
- ASSURANCE : Police d'assurance
- ENTRETIEN : Carnet d'entretien, fiche de maintenance
- SINISTRE : Constat de sinistre, rapport d'expertise
- COMPTABILITE : Annexe comptable, relevé de compte
- AUTRE : Aucun des types ci-dessus

Réponds UNIQUEMENT par le code du type (ex: PV_AG). Rien d'autre.

Extrait :
{excerpt}"""
        
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": prompt}]
        })
        
        for attempt in range(3):
            try:
                bedrock = _get_bedrock_thread()
                response = bedrock.invoke_model(
                    modelId=CLASSIFIER_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                answer = json.loads(response["body"].read())["content"][0]["text"].strip().upper()
                result = answer if answer in DOC_TYPES_VALID else "AUTRE"
                return source_file, result, "classify"
            except Exception as e:
                if "ThrottlingException" in str(e) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return source_file, "AUTRE", "classify"
        return source_file, "AUTRE", "classify"
    
    def _verify_pvag(source_file, text_full):
        """Vérifie qu'un doc classé PV_AG est bien un PV et pas un OJ/convocation. Thread-safe."""
        excerpt = _build_pvag_excerpt(text_full)
        prompt = f"""Ce document a été trouvé dans un dossier d'assemblée générale et classé PV_AG.
Analyse le CONTENU pour déterminer son type réel.

CRITÈRE DÉCISIF — PV_AG vs COURRIER :

Un VRAI PV_AG (procès-verbal) contient OBLIGATOIREMENT :
- Des RÉSULTATS de votes avec décomptes PRÉCIS : "Votent pour : 35 copropriétaires totalisant 5082 tantièmes"
- Des VERDICTS FORMELS : "En vertu de quoi, cette résolution est adoptée/rejetée"
- La mention "résolution adoptée" ou "résolution rejetée" au moins une fois

Un ORDRE DU JOUR ou CONVOCATION (= COURRIER) contient :
- Des PROJETS de résolution sans résultats : "Il est proposé à l'AG de..."
- "L'Assemblée Générale est invitée à délibérer..."
- Des articles de majorité SANS décompte de votes
- AUCUNE mention "adoptée" ou "rejetée"
- Souvent : "Madame, Monsieur", formule de convocation, date/lieu de l'AG

ATTENTION : un document qui contient le mot "tantièmes" ou "résolution" n'est PAS forcément un PV.
Un ODJ contient aussi ces mots dans les projets de résolution. Le critère clé est la PRÉSENCE
ou l'ABSENCE de résultats de vote effectifs et de verdicts formels.

Autres types possibles dans un dossier AG :
- Contrat de syndic, mandat → CONTRAT
- Devis de travaux → DEVIS
- Annexes comptables, comptes, charges, budget → COMPTABILITE
- Diagnostic, tableau d'anomalies → DIAGNOSTIC
- Police ou attestation d'assurance → ASSURANCE
- Feuille de présence, pouvoir, VPC, LRE, accusé réception → COURRIER

Réponds UNIQUEMENT par le code du type : PV_AG, COURRIER, CONTRAT, DEVIS, COMPTABILITE, DIAGNOSTIC, ASSURANCE, AUTRE.

Extrait (début et fin du document) :
{excerpt}"""
        
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 15,
            "messages": [{"role": "user", "content": prompt}]
        })
        
        for attempt in range(3):
            try:
                bedrock = _get_bedrock_thread()
                response = bedrock.invoke_model(
                    modelId=CLASSIFIER_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                answer = json.loads(response["body"].read())["content"][0]["text"].strip().upper()
                result = answer if answer in DOC_TYPES_VALID else "PV_AG"
                return source_file, result, "verify"
            except Exception as e:
                if "ThrottlingException" in str(e) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return source_file, "PV_AG", "verify"  # En cas d'erreur, garder PV_AG
        return source_file, "PV_AG", "verify"
    
    MAX_CLASSIFY_WORKERS = 10
    print(f"⏳ Haiku parallèle ({MAX_CLASSIFY_WORKERS} workers) : {len(files_needing_llm)} classifications + {len(files_needing_verify)} vérifications PV_AG...")
    
    with ThreadPoolExecutor(max_workers=MAX_CLASSIFY_WORKERS) as executor:
        futures = {}
        for sf, excerpt in files_needing_llm:
            futures[executor.submit(_classify_one, sf, excerpt)] = sf
        for sf, excerpt in files_needing_verify:
            futures[executor.submit(_verify_pvag, sf, excerpt)] = sf
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Haiku pré-scan"):
            sf, doc_type, task_type = future.result()
            with _classify_lock:
                _doc_type_llm_cache[sf] = doc_type
                if task_type == "classify":
                    _llm_stats["calls"] += 1
                else:
                    _verify_stats["verified"] += 1
                    if doc_type != "PV_AG":
                        _verify_stats["reclassified"] += 1
    
    print(f"  ✅ {len(files_needing_llm)} classifications + {_verify_stats['verified']} vérifications terminées")
    if _verify_stats["reclassified"] > 0:
        reclassed = [(sf, _doc_type_llm_cache[sf]) for sf, _ in files_needing_verify if _doc_type_llm_cache.get(sf) != "PV_AG"]
        print(f"  🔄 {_verify_stats['reclassified']} PV_AG reclassifiés :")
        for sf, new_dt in reclassed:
            print(f"    PV_AG → {new_dt} : {os.path.basename(sf)}")

# =====================================================
# Dédup par similarité de contenu (.docx/.pdf du même document)
# =====================================================
print("\n⏳ Dédup par similarité de contenu...")
from difflib import SequenceMatcher
from collections import defaultdict

_dedup_excluded = set()  # source_file à exclure du chunking
_dedup_stats = {"groups_checked": 0, "duplicates_found": 0}

# Charger texte + métadonnées de chaque fichier JSON (lecture rapide)
_dedup_index = []  # (json_path, source_file, nom_fichier, dossier_parent, text_start)
for json_path in json_files:
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        text = doc.get("texte", "")
        if len(text.strip()) < 100:
            continue
        source_file = doc.get("source_file", "")
        nom_fichier = doc.get("nom_fichier", "")
        dossier_parent = doc.get("dossier_parent", "")
        # Normaliser les 500 premiers chars pour la comparaison
        text_norm = re.sub(r'\s+', ' ', text[:500].lower().strip())
        _dedup_index.append((json_path, source_file, nom_fichier, dossier_parent, text_norm, len(text)))
    except Exception:
        continue

# Grouper par dossier parent
_by_folder = defaultdict(list)
for item in _dedup_index:
    _by_folder[item[3]].append(item)

for folder, items in _by_folder.items():
    if len(items) < 2:
        continue
    _dedup_stats["groups_checked"] += 1
    # Comparer toutes les paires au sein du même dossier
    for i in range(len(items)):
        if items[i][1] in _dedup_excluded:
            continue
        for j in range(i + 1, len(items)):
            if items[j][1] in _dedup_excluded:
                continue
            # Similarité sur les 500 premiers chars normalisés
            ratio = SequenceMatcher(None, items[i][4], items[j][4]).ratio()
            if ratio > 0.85:
                # Choisir lequel garder — règles de priorité :
                # a) Document signé → prioritaire (même si PDF)
                # b) .docx/.doc → texte natif (meilleure qualité)
                # c) Le plus long (plus de texte = extraction plus complète)
                def _priority(item):
                    sf, nf, text_len = item[1], item[2], item[5]
                    nf_lower = nf.lower()
                    is_signed = any(k in nf_lower for k in ("signé", "signe", "signed"))
                    is_word = nf_lower.endswith((".docx", ".doc"))
                    return (is_signed, is_word, text_len)

                keep, drop = (items[i], items[j]) if _priority(items[i]) >= _priority(items[j]) else (items[j], items[i])
                _dedup_excluded.add(drop[1])
                _dedup_stats["duplicates_found"] += 1
                print(f"  🔗 Doublon détecté (sim={ratio:.0%}) :")
                print(f"     GARDÉ  : {keep[2]}")
                print(f"     EXCLU  : {drop[2]}")

print(f"  ✅ {_dedup_stats['duplicates_found']} doublons éliminés sur {_dedup_stats['groups_checked']} groupes vérifiés")

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

        # ── DÉDUP : exclure les doublons détectés ──
        if source_file in _dedup_excluded:
            continue
        
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
            
            # Classification résolution PV_AG (Phase 1a)
            res_cat = classify_resolution_category(chunk_text, doc_type, i)
            if res_cat:
                _resolution_category_stats[res_cat] = _resolution_category_stats.get(res_cat, 0) + 1

            chunk_record = {
                "chunk_id": chunk_id,
                "copropriete": doc.get("copropriete", ""),
                "source_file": doc.get("source_file", ""),
                "nom_fichier": doc.get("nom_fichier", ""),
                "doc_type": doc_type,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "text": chunk_text,
                "nb_caracteres": len(chunk_text),
                "resolution_category": res_cat
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

print(f"\n--- Vérification PV_AG (pré-scan) ---")
print(f"  Vérifiés             : {_verify_stats['verified']}")
print(f"  Reclassifiés         : {_verify_stats['reclassified']}")

print(f"\n--- Détection format résolutions (Haiku) ---")
print(f"  Appels               : {_haiku_pattern_stats['calls']}")
print(f"  Patterns trouvés     : {_haiku_pattern_stats['success']}")
print(f"  Échecs (fallback)    : {_haiku_pattern_stats['fail']}")

if _resolution_category_stats:
    print(f"\n--- Classification résolutions PV_AG (Phase 1a) ---")
    for cat, cnt in sorted(_resolution_category_stats.items(), key=lambda x: -x[1]):
        print(f"  {cat:20s} : {cnt:5d} chunks")

print(f"\n-> Chunks : {OUTPUT_FILE}")
