"""
ÉTAPE 0.2 — Filtrage : séparer plans des photos, exclure les inutiles
Classification en 3 passes :
  1. Règles déterministes (mots-clés dossier, seuil >100 images)
  2. Heuristiques (noms de fichiers, chemin)
  3. LLM Vision (Sonnet 4.6) sur échantillon 5% en cas de doute
Lance : python 01_filtrage.py
"""
import os
import shutil
import json
import base64
import random
import boto3
from pathlib import Path

# =====================================================
# CONFIGURATION
# =====================================================
ARCHIVES_ROOT = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\RUN ON 6 COPROS"  # ← MODIFIER ICI
OUTPUT_DIR = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\Archives_Filtrees"    # ← MODIFIER ICI
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_FILE = os.path.join(SCRIPT_DIR, "filtrage_rapport.json")

AWS_REGION = "eu-west-1"

# =====================================================
# Client Bedrock pour classification visuelle
# =====================================================
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
LLM_MODEL = "eu.anthropic.claude-sonnet-4-6"

# Cache des décisions LLM par dossier (1 seul appel par dossier)
folder_decisions_cache = {}

# =====================================================
# Règles de filtrage
# =====================================================

# Extensions à garder systématiquement (documents textuels)
KEEP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv",
    ".msg", ".eml", ".txt", ".rtf", ".ppt", ".pptx"
}

# Extensions d'images à trier (plan vs photo)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".heic"}

# Extensions de plans CAO à garder
PLAN_EXTENSIONS = {".dwg", ".dxf"}

# Extensions à exclure
EXCLUDE_EXTENSIONS = {".zip", ".rar", ".7z", ".exe", ".msi", ".dmg", ".mp4", ".avi", ".mov", ".mp3"}

# Fichiers système à ignorer
SYSTEM_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".dropbox"}

# Mots-clés dans le chemin/nom qui indiquent un PLAN
PLAN_KEYWORDS = [
    "plan", "plans", "pln", "niveau", "etage", "étage", "rdc", "rez-de-chaussée",
    "sous-sol", "ss1", "ss2", "coupe", "facade", "façade", "élévation", "elevation",
    "masse", "situation", "cadastr", "parcell", "géomètre", "geometre",
    "architecte", "archi", "lot", "tantième", "millième", "répartition",
    "carnet_entretien", "carnet entretien", "diagnostic",
    "mesurage", "loi_carrez", "carrez", "surface"
]

# Mots-clés qui indiquent une PHOTO (à exclure)
PHOTO_KEYWORDS = [
    "photo", "photos", "img_", "dsc_", "dcim", "screenshot", "capture",
    "whatsapp", "signal", "image_", "constat",
    "dégât", "degat", "sinistre_photo", "visite", "état_des_lieux_photo"
]

def is_system_file(filename):
    return filename.lower() in SYSTEM_FILES or filename.startswith("~$") or filename.startswith("._")

def path_contains_keywords(filepath, keywords):
    path_lower = filepath.lower().replace("_", " ").replace("-", " ")
    return any(kw in path_lower for kw in keywords)

# =====================================================
# Classification LLM Vision
# =====================================================

def count_images_in_folder(folder_path):
    """Compte le nombre d'images dans un dossier (non récursif)."""
    count = 0
    try:
        for f in os.listdir(folder_path):
            if Path(f).suffix.lower() in IMAGE_EXTENSIONS:
                count += 1
    except OSError:
        pass
    return count

def sample_images_in_folder(folder_path, sample_pct=0.05, min_sample=2, max_sample=5):
    """Retourne un échantillon d'images du dossier."""
    all_images = [
        os.path.join(folder_path, f) for f in os.listdir(folder_path)
        if Path(f).suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not all_images:
        return []
    n = max(min_sample, min(max_sample, int(len(all_images) * sample_pct)))
    return random.sample(all_images, min(n, len(all_images)))

def classify_image_with_llm(image_paths):
    """
    Envoie un échantillon d'images à Sonnet 4.6 pour classification.
    Retourne 'PLAN' ou 'PHOTO' selon le verdict majoritaire.
    """
    content = []
    # Cap cumulatif pour rester sous la limite de tokens Bedrock (8 192 tokens)
    # ~3 Mo de base64 est une limite sûre pour une ou plusieurs images combinées
    MAX_TOTAL_B64_BYTES = 3_000_000
    cumulative_size = 0

    for img_path in image_paths:
        ext = Path(img_path).suffix.lower()
        media_type_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".bmp": "image/bmp", ".tiff": "image/tiff", ".tif": "image/tiff",
            ".webp": "image/webp"
        }
        media_type = media_type_map.get(ext, "image/jpeg")

        try:
            with open(img_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode("utf-8")

            img_size = len(img_data)
            # Ignorer les images individuellement trop lourdes
            if img_size > 10_000_000:  # ~10 Mo
                continue
            # Stopper si le cumul dépasserait le cap
            if cumulative_size + img_size > MAX_TOTAL_B64_BYTES:
                break

            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": img_data}
            })
            cumulative_size += img_size
        except Exception:
            continue

    if not content:
        return "PHOTO_PROBABLE"

    content.append({
        "type": "text",
        "text": """Analyse ces images. Pour chacune, détermine s'il s'agit de :
- PLAN : plan d'architecte, plan de masse, plan de coupe, plan d'étage, schéma technique, plan cadastral, plan de géomètre, dessin technique
- PHOTO : photographie d'un bâtiment, d'un appartement, d'un dégât, d'une façade, photo de constat, photo d'état des lieux

Réponds UNIQUEMENT par un seul mot : PLAN ou PHOTO
Donne le verdict MAJORITAIRE pour l'ensemble des images."""
    })
    
    def _call_bedrock(payload_content):
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": payload_content}]
        })
        response = bedrock.invoke_model(
            modelId=LLM_MODEL, body=body,
            contentType="application/json", accept="application/json"
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"].strip().upper()

    try:
        answer = _call_bedrock(content)
        return "PLAN" if "PLAN" in answer else "PHOTO"

    except Exception as e:
        err_str = str(e)
        # Si trop de tokens, réessayer avec une seule image
        if "Too many input" in err_str or "ValidationException" in err_str:
            print(f"  ⚠️ Payload trop lourd ({len(content)-1} images) — retry avec 1 seule image")
            try:
                fallback_content = [content[0], content[-1]]  # 1 image + le texte
                answer = _call_bedrock(fallback_content)
                return "PLAN" if "PLAN" in answer else "PHOTO"
            except Exception as e2:
                print(f"  ⚠️ Erreur LLM Vision (fallback) : {e2}")
                return "PHOTO_PROBABLE"
        print(f"  ⚠️ Erreur LLM Vision : {e}")
        return "PHOTO_PROBABLE"

# =====================================================
# Classification principale en 3 passes
# =====================================================

def classify_image(filepath, filename, filesize):
    """
    Classification d'image en 3 passes :
    1. Règles déterministes (mots-clés dossier, seuil >100 images)
    2. Heuristiques (noms, chemin)
    3. LLM Vision sur échantillon 5% en cas de doute
    """
    full_path = os.path.join(filepath, filename).lower()
    parent_folder = os.path.basename(filepath).lower().replace("_", " ").replace("-", " ")
    
    # ── PASSE 1 : Règles déterministes ──
    
    # 1a. Mot-clé "photo" dans le dossier parent → PHOTO
    if any(kw in parent_folder for kw in PHOTO_KEYWORDS):
        return "PHOTO"
    
    # 1b. Mot-clé "plan" dans le dossier parent → PLAN
    if any(kw in parent_folder for kw in PLAN_KEYWORDS):
        return "PLAN"
    
    # 1c. Plus de 100 images dans le même dossier → PHOTO
    #     (on ne peut pas avoir 100+ plans pour une seule copro dans 1 dossier)
    if filepath not in folder_decisions_cache:
        img_count = count_images_in_folder(filepath)
        if img_count > 100:
            folder_decisions_cache[filepath] = "PHOTO"
            print(f"  📁 {os.path.basename(filepath)} : {img_count} images → classé PHOTO (>100)")
    
    if folder_decisions_cache.get(filepath) == "PHOTO":
        return "PHOTO"
    if folder_decisions_cache.get(filepath) == "PLAN":
        return "PLAN"
    
    # ── PASSE 2 : Heuristiques par fichier ──
    
    # 2a. Mot-clé plan dans le chemin complet
    if path_contains_keywords(full_path, PLAN_KEYWORDS):
        return "PLAN"
    
    # 2b. Mot-clé photo dans le chemin complet
    if path_contains_keywords(full_path, PHOTO_KEYWORDS):
        return "PHOTO"
    
    # ── PASSE 3 : LLM Vision sur échantillon (1 seul appel par dossier) ──
    
    if filepath not in folder_decisions_cache:
        img_count = count_images_in_folder(filepath)
        
        if img_count == 0:
            folder_decisions_cache[filepath] = "PHOTO_PROBABLE"
        elif img_count <= 3:
            # Peu d'images → analyser toutes
            sample = sample_images_in_folder(filepath, sample_pct=1.0, min_sample=1, max_sample=3)
            verdict = classify_image_with_llm(sample)
            folder_decisions_cache[filepath] = verdict
            print(f"  🤖 {os.path.basename(filepath)} : {img_count} images, échantillon {len(sample)} → {verdict}")
        else:
            # Échantillon 5% (min 2, max 5)
            sample = sample_images_in_folder(filepath, sample_pct=0.05, min_sample=2, max_sample=5)
            verdict = classify_image_with_llm(sample)
            folder_decisions_cache[filepath] = verdict
            print(f"  🤖 {os.path.basename(filepath)} : {img_count} images, échantillon {len(sample)} → {verdict}")
    
    return folder_decisions_cache.get(filepath, "PHOTO_PROBABLE")

# =====================================================
# Exécution du filtrage
# =====================================================
# Nettoyage des anciennes sorties
if os.path.exists(OUTPUT_DIR):
    print(f"Nettoyage du dossier de sortie : {OUTPUT_DIR}")
    shutil.rmtree(OUTPUT_DIR)
if os.path.exists(REPORT_FILE):
    os.remove(REPORT_FILE)

print(f"Filtrage de : {ARCHIVES_ROOT}")
print(f"Sortie dans : {OUTPUT_DIR}\n")

stats = {
    "gardes_documents": 0,
    "gardes_plans": 0,
    "exclus_photos": 0,
    "exclus_photos_probables": 0,
    "exclus_autres": 0,
    "exclus_systeme": 0,
    "llm_calls": 0,
    "erreurs": 0
}

decisions_log = []

for root, dirs, filenames in os.walk(ARCHIVES_ROOT):
    for fname in filenames:
        src_path = os.path.join(root, fname)
        rel_path = os.path.relpath(src_path, ARCHIVES_ROOT)
        ext = Path(fname).suffix.lower()
        
        # Ignorer fichiers système
        if is_system_file(fname):
            stats["exclus_systeme"] += 1
            continue
        
        try:
            filesize = os.path.getsize(src_path)
        except OSError:
            stats["erreurs"] += 1
            continue
        
        # --- Décision ---
        decision = None
        
        if ext in KEEP_EXTENSIONS or ext in PLAN_EXTENSIONS:
            decision = "GARDER"
            stats["gardes_documents"] += 1
        
        elif ext in IMAGE_EXTENSIONS:
            classification = classify_image(root, fname, filesize)
            if classification == "PLAN":
                decision = "GARDER"
                stats["gardes_plans"] += 1
            elif classification == "PHOTO":
                decision = "EXCLURE"
                stats["exclus_photos"] += 1
            elif classification == "PHOTO_PROBABLE":
                decision = "EXCLURE"
                stats["exclus_photos_probables"] += 1
            else:
                decision = "GARDER"
                stats["gardes_plans"] += 1
        
        elif ext in EXCLUDE_EXTENSIONS:
            decision = "EXCLURE"
            stats["exclus_autres"] += 1
        
        else:
            # Extension inconnue → garder par sécurité
            decision = "GARDER"
            stats["gardes_documents"] += 1
        
        # --- Copie si gardé ---
        if decision == "GARDER":
            dest_path = os.path.join(OUTPUT_DIR, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_path, dest_path)
        
        decisions_log.append({
            "fichier": rel_path,
            "extension": ext,
            "taille_mo": round(filesize / (1024*1024), 2),
            "decision": decision
        })

# =====================================================
# Rapport
# =====================================================
print("=" * 50)
print("RAPPORT DE FILTRAGE")
print("=" * 50)
total_gardes = stats["gardes_documents"] + stats["gardes_plans"]
total_exclus = stats["exclus_photos"] + stats["exclus_photos_probables"] + stats["exclus_autres"] + stats["exclus_systeme"]

print(f"\n✅ GARDÉS : {total_gardes}")
print(f"   Documents textuels : {stats['gardes_documents']}")
print(f"   Plans identifiés   : {stats['gardes_plans']}")

print(f"\n❌ EXCLUS : {total_exclus}")
print(f"   Photos identifiées : {stats['exclus_photos']}")
print(f"   Photos probables   : {stats['exclus_photos_probables']}")
print(f"   Autres (zip, etc.) : {stats['exclus_autres']}")
print(f"   Fichiers système   : {stats['exclus_systeme']}")

print(f"\n🤖 Appels LLM Vision  : {len(folder_decisions_cache)} dossiers analysés")
print(f"⚠️  Erreurs : {stats['erreurs']}")

print(f"\n📁 Fichiers filtrés copiés dans : {OUTPUT_DIR}")

# Sauvegarder le log détaillé
with open(REPORT_FILE, "w", encoding="utf-8") as f:
    json.dump({"stats": stats, "llm_decisions": {k: v for k, v in folder_decisions_cache.items()}, "decisions": decisions_log}, f, ensure_ascii=False, indent=2)

print(f"📋 Log détaillé : {REPORT_FILE}")
