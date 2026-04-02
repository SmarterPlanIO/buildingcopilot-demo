# Guide complet — Prototype RAG pour archives de copropriété sur AWS

**Projet :** PALIM — Building Copilot — RAG multi-copropriétés
**Dernière mise à jour :** 2 avril 2026
**Version :** v0.5.0
**Volume :** 9 GO, 1 430 dossiers, 11 000 fichiers, 24 482 chunks
**Profil :** Non-développeur, copier/coller dans VS Code ou Antigravity
**Stack :** Full AWS (Textract, Bedrock, RDS pgvector) + Streamlit Cloud + Langfuse

---

## Vue d'ensemble du pipeline

```
Archives locales (9 GO)
  → Étape 0 : Inventaire & nettoyage
  → Étape 1 : Upload S3
  → Étape 2 : OCR via Textract
  → Étape 3 : Parsing structurel + classification doc_type (3 passes dont LLM) + filtre contenu binaire + BORDEREAU_AR
  → Étape 4 : Extraction métadonnées document-level (date, sous-type, statut) via Haiku + protection RCP
  → Étape 5 : Embedding via Bedrock Titan (15 workers parallèles)
  → Étape 5b : Questions synthétiques Haiku (PV_AG, RCP, CONTRAT éligibles)
  → Étape 6 : Stockage pgvector + tsvector BM25 (setweight A=texte, D=questions) + table documents (RDS Postgres)
  → Étape 7 : Requête hybride (pré-filtrage document → RRF + source diversity + Claude) + UI Streamlit multi-turn
            + Auth gate (login pilotes) + Langfuse tracing (tokens, coût, metadata) + Feedback 👍👎💬
            + Mode juriste (rigueur absolue sur PV_AG, RCP, CONTRAT, ASSURANCE)
            + Filtrage prompts hors-sujet (classification Haiku)
  → Étape 8 : Synchronisation Airtable Assynco → table dossiers PostgreSQL (UPSERT par copropriété)
              ⚠️ OBLIGATOIRE après chaque Étape 6 (le TRUNCATE efface les chunks virtuels Airtable)
```

**Coût estimé pour l'essai complet :** ~$50–$80 (dont ~$12 RDS, ~$15–$30 Textract, ~$5–$10 Bedrock)

---

## Prérequis

### Outils à installer sur ton PC Windows

1. **Python 3.11+** — Vérifie avec `python --version` dans le Terminal (PowerShell ou cmd)
   - Si absent : télécharge depuis https://www.python.org/downloads/ (coche "Add to PATH" lors de l'installation)

2. **AWS CLI v2** — Pour interagir avec AWS depuis le Terminal
   - Télécharge le MSI depuis https://aws.amazon.com/cli/
   - Configurer : `aws configure` (saisir Access Key, Secret Key, région `eu-west-1`)

3. **VS Code** (ou Antigravity) — Pour éditer et lancer les scripts

4. **pip packages** — À installer une seule fois :
   ```bash
   pip install boto3 psycopg2-binary pgvector tiktoken tqdm pandas openpyxl python-docx extract-msg streamlit flashrank requests
   ```

### Services AWS à activer

1. **Amazon S3** — Stockage des fichiers (déjà actif si tu as un compte AWS)
2. **Amazon Textract** — OCR. Activer dans la console AWS > Textract (région `eu-west-1`)
3. **Amazon Bedrock** — LLM et Embeddings. Dans la console :
   - Aller dans Bedrock > Model access
   - Demander l'accès à : **Titan Embeddings V2**, **Claude Sonnet 4.6** et **Claude Haiku 4.5** (classification de documents à l'étape 3 + mode démo à l'étape 7)
   - L'activation prend quelques minutes
   - **Important :** Haiku 4.5 nécessite un inference profile cross-region (préfixe `eu.`), pas un appel on-demand direct
4. **Amazon RDS** — Base Postgres. On la crée à l'étape 6.

---

## Étape 0 — Inventaire, nettoyage et filtrage

### 0.1 Comprendre ce qu'on a

Avant tout, on fait un inventaire complet pour savoir exactement ce qu'on a dans les 9 GO.

Crée un fichier `00_inventaire.py` et copie-colle :

```python
"""
ÉTAPE 0.1 — Inventaire des fichiers d'archives
Lance : python 00_inventaire.py
"""
import os
import csv
from collections import Counter
from pathlib import Path

# =====================================================
# CONFIGURATION — Modifie ce chemin vers tes archives
# =====================================================
ARCHIVES_ROOT = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\RUN ON 6 COPROS"  # ← MODIFIER ICI
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_CSV = os.path.join(SCRIPT_DIR, "inventaire_fichiers.csv")
OUTPUT_STATS = os.path.join(SCRIPT_DIR, "inventaire_stats.txt")

# =====================================================
# Catégorisation des fichiers
# =====================================================
CATEGORIES = {
    "pdf": "DOCUMENT_PDF",
    "doc": "DOCUMENT_WORD",
    "docx": "DOCUMENT_WORD",
    "xls": "TABLEUR",
    "xlsx": "TABLEUR",
    "csv": "TABLEUR",
    "msg": "EMAIL",
    "eml": "EMAIL",
    "txt": "TEXTE",
    "rtf": "TEXTE",
    "jpg": "IMAGE",
    "jpeg": "IMAGE",
    "png": "IMAGE",
    "gif": "IMAGE",
    "bmp": "IMAGE",
    "tiff": "IMAGE",
    "tif": "IMAGE",
    "heic": "IMAGE",
    "dwg": "PLAN_CAO",
    "dxf": "PLAN_CAO",
    "ppt": "PRESENTATION",
    "pptx": "PRESENTATION",
    "zip": "ARCHIVE_COMPRESSE",
    "rar": "ARCHIVE_COMPRESSE",
    "7z": "ARCHIVE_COMPRESSE",
}

SYSTEM_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".gitkeep", ".dropbox"}

def categorize(filename):
    ext = Path(filename).suffix.lower().lstrip(".")
    return CATEGORIES.get(ext, "AUTRE")

def is_system_file(filename):
    return filename.lower() in SYSTEM_FILES or filename.startswith("~$")

# =====================================================
# Scan complet
# =====================================================
# Nettoyage des anciens exports
for f in [OUTPUT_CSV, OUTPUT_STATS]:
    if os.path.exists(f):
        os.remove(f)

print(f"Scan de : {ARCHIVES_ROOT}")
print("Patiente, 11 000 fichiers ça prend 1-2 minutes...\n")

files = []
categories_count = Counter()
categories_size = Counter()
copro_stats = Counter()
skipped = 0

for root, dirs, filenames in os.walk(ARCHIVES_ROOT):
    for fname in filenames:
        filepath = os.path.join(root, fname)
        
        if is_system_file(fname):
            skipped += 1
            continue
        
        try:
            size = os.path.getsize(filepath)
        except OSError:
            skipped += 1
            continue
        
        cat = categorize(fname)
        rel_path = os.path.relpath(filepath, ARCHIVES_ROOT)
        
        # Identifier la copro (premier niveau de dossier)
        copro = rel_path.split(os.sep)[0] if os.sep in rel_path else "RACINE"
        
        files.append({
            "copro": copro,
            "categorie": cat,
            "extension": Path(fname).suffix.lower(),
            "taille_mo": round(size / (1024 * 1024), 2),
            "chemin_relatif": rel_path,
            "nom_fichier": fname
        })
        
        categories_count[cat] += 1
        categories_size[cat] += size
        copro_stats[copro] += 1

# =====================================================
# Export CSV détaillé
# =====================================================
with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["copro", "categorie", "extension", "taille_mo", "chemin_relatif", "nom_fichier"])
    writer.writeheader()
    writer.writerows(files)

# =====================================================
# Rapport statistiques
# =====================================================
report = []
report.append("=" * 60)
report.append("INVENTAIRE DES ARCHIVES DE COPROPRIÉTÉ")
report.append("=" * 60)
report.append(f"\nTotal fichiers utiles : {len(files)}")
report.append(f"Fichiers système ignorés : {skipped}")
report.append(f"Taille totale : {sum(f['taille_mo'] for f in files):.1f} Mo")

report.append("\n--- PAR CATÉGORIE ---")
for cat, count in categories_count.most_common():
    size_mo = categories_size[cat] / (1024 * 1024)
    report.append(f"  {cat:25s} : {count:6d} fichiers  ({size_mo:8.1f} Mo)")

report.append("\n--- PAR COPROPRIÉTÉ ---")
for copro, count in copro_stats.most_common():
    report.append(f"  {copro:40s} : {count:5d} fichiers")

report.append("\n--- DÉCISION DE FILTRAGE SUGGÉRÉE ---")
report.append("  ✅ GARDER  : DOCUMENT_PDF, DOCUMENT_WORD, TABLEUR, EMAIL, TEXTE, PRESENTATION")
report.append("  ✅ GARDER  : PLAN_CAO (plans .dwg/.dxf)")
report.append("  ⚠️  TRIER  : IMAGE (garder les plans scannés, exclure les photos)")
report.append("  ❌ EXCLURE : ARCHIVE_COMPRESSE, AUTRE, fichiers système")

report_text = "\n".join(report)
print(report_text)

with open(OUTPUT_STATS, "w", encoding="utf-8") as f:
    f.write(report_text)

print(f"\n✅ Fichiers générés :")
print(f"   - {OUTPUT_CSV} (détail de chaque fichier)")
print(f"   - {OUTPUT_STATS} (statistiques)")
```

**Lance :** `python 00_inventaire.py`

Ça te donne un CSV complet et un rapport. Regarde le rapport pour voir la répartition avant de continuer.


### 0.2 Filtrage intelligent — Séparer plans et photos

Le point délicat : dans les images, il faut garder les **plans** (qui contiennent du texte utile : numéros de lots, surfaces, annotations) et exclure les **photos** (constats, état des lieux visuels).

Stratégie de filtrage en 3 passes :

1. **Par nom de fichier et dossier** — Les plans sont souvent dans des dossiers nommés "Plans", "Carnet d'entretien", "Diagnostics" etc., ou nommés "plan_", "PLN_", "niveau_", "RDC", "etage"
2. **Par taille et dimensions** — Les plans scannés sont généralement en haute résolution (>2 Mo, format paysage très allongé type A1/A0). Les photos sont plus petites ou en format portrait/carré.
3. **En cas de doute** — On garde. Mieux vaut un faux positif qu'un plan perdu.

Crée `01_filtrage.py` :

```python
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
```

**Lance :** `python 01_filtrage.py`

> **Important :** Après exécution, ouvre `filtrage_rapport.json` et vérifie rapidement les "plans probables". Ce sont les images qu'on a gardées par défaut mais dont on n'est pas sûr. En 5-10 minutes de vérification manuelle, tu seras tranquille.

---

## Étape 1 — Upload vers S3

### 1.1 Créer le bucket S3

Dans le Terminal :

```bash
aws s3 mb s3://smarterplan-rag-prototype --region eu-west-1
```

### 1.2 Upload des fichiers filtrés

```bash
aws s3 sync "G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\Archives_Filtrees" s3://smarterplan-rag-prototype/archives/ --region eu-west-1
```

Avec 9 GO (moins les photos exclues, probablement 5-7 GO), ça prend 15-30 minutes selon ta connexion.

Vérifie :
```bash
aws s3 ls s3://smarterplan-rag-prototype/archives/ --recursive --summarize
```

> **Note Windows :** Si la commande `aws s3 sync` échoue à cause des accents dans le chemin, copie d'abord le dossier `Archives_Filtrees` à la racine d'un disque (ex: `C:\Archives_Filtrees`) et lance le sync depuis là.

---

## Étape 2 — OCR via Amazon Textract

### 2.1 Comprendre la stratégie OCR

Tous les fichiers ne nécessitent pas un OCR :

| Type de fichier | Traitement |
|---|---|
| PDF scanné (image) | Textract OCR complet avec LAYOUT |
| PDF natif (texte sélectionnable) | Extraction texte directe (PyPDF2, pas besoin de Textract) |
| Word (.doc/.docx) | Extraction texte directe (python-docx) |
| Excel (.xls/.xlsx) | Extraction texte directe (openpyxl/pandas) |
| Email (.msg/.eml) | Extraction texte directe (extract-msg) |
| Texte (.txt/.rtf) | Lecture directe |
| Image (plans gardés) | Textract OCR |
| PowerPoint (.ppt/.pptx) | Extraction texte directe (python-pptx) |

**Seuls les PDF scannés et les images passent par Textract**, ce qui réduit massivement le coût.

### 2.2 Script d'extraction universelle

Crée `02_extraction.py` :

```python
"""
ÉTAPE 2 — Extraction OPTIMISÉE de texte de tous les fichiers
============================================================
Architecture "fire-all-then-collect" pour Textract :
  Phase 1 : Extraction directe (Word, Excel, Email, Texte, PDF natifs) — séquentiel rapide
  Phase 2 : Batch upload S3 des fichiers nécessitant OCR — parallèle (20 threads)
  Phase 3 : Lancement en rafale de tous les jobs Textract async — rate-limited
  Phase 4 : Polling parallèle et collecte des résultats
  Phase 5 : Nettoyage S3 + checkpoint

Gain attendu : 5-10x plus rapide que la version séquentielle
Lance : python 02_extraction_optimized.py
"""
import os
import sys
import json
import time
import shutil
import boto3
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from botocore.config import Config
from tqdm import tqdm

# ── Dépendances (installation auto si manquantes) ──
try:
    import fitz  # PyMuPDF
except ImportError:
    os.system("pip install PyMuPDF")
    import fitz
try:
    from docx import Document as DocxDocument
except ImportError:
    os.system("pip install python-docx")
    from docx import Document as DocxDocument
try:
    import openpyxl
except ImportError:
    os.system("pip install openpyxl")
    import openpyxl
try:
    import extract_msg
except ImportError:
    os.system("pip install extract-msg")
    import extract_msg
try:
    from pptx import Presentation
except ImportError:
    os.system("pip install python-pptx")
    from pptx import Presentation


# =====================================================
# CONFIGURATION
# =====================================================
FILTERED_DIR = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\Archives_Filtrees"
OUTPUT_DIR = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\Archives_Extraites"
S3_BUCKET = "smarterplan-rag-prototype"
S3_TEXTRACT_PREFIX = "textract_temp/"
AWS_REGION = "eu-west-1"

# ── Paramètres de performance ──
MAX_CONCURRENT_UPLOADS = 20       # Threads pour upload S3
JOB_LAUNCH_RATE = 15              # Jobs lancés par seconde (quota augmenté)
POLL_INTERVAL = 3                 # Secondes entre chaque cycle de polling
POLL_BATCH_SIZE = 100             # Jobs vérifiés par cycle (quota augmenté)
MAX_RETRIES = 3                   # Retries sur throttling

# ── Checkpoint / reprise ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "extraction_checkpoint.json")

# ── AWS clients avec retry adaptatif ──
boto_config = Config(
    retries={"max_attempts": 10, "mode": "adaptive"},
    max_pool_connections=50
)
textract = boto3.client("textract", region_name=AWS_REGION, config=boto_config)
s3 = boto3.client("s3", region_name=AWS_REGION, config=boto_config)

# ── Logging ──
log = logging.getLogger(__name__)

def setup_logging():
    # Nettoie les anciens handlers si on relance dans le même interpréteur
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("extraction.log", mode='w', encoding="utf-8")
        ]
    )

# ── Stats ──
stats = {
    "pdf_natif": 0, "pdf_ocr": 0, "word": 0, "excel": 0,
    "email": 0, "texte": 0, "pptx": 0, "image_ocr": 0,
    "erreurs": 0, "vides": 0, "skipped_checkpoint": 0
}


# =====================================================
# CHECKPOINT : reprise après interruption
# =====================================================
def load_checkpoint():
    completed = set()
    checkpoint_exists_and_valid = False
    
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                data = json.load(f).get("completed", [])
                if data:
                    completed.update(data)
                    checkpoint_exists_and_valid = True
        except:
            pass
            
    # Ne scanner le dossier QUE SI on n'a pas de checkpoint valide
    # Cela évite de scanner 10 000 fichiers à chaque lancement
    if not checkpoint_exists_and_valid and os.path.exists(OUTPUT_DIR):
        print("Recherche des fichiers déjà traités dans le dossier de sortie (scan initial)...")
        for root, _, files in os.walk(OUTPUT_DIR):
            for fname in files:
                if fname.endswith(".json"):
                    # Retrouver le chemin relatif original (sans .json)
                    rel_json = os.path.relpath(os.path.join(root, fname), OUTPUT_DIR)
                    completed.add(rel_json[:-5])
                    
    return completed

def save_checkpoint(completed_set):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"completed": list(completed_set)}, f)

completed_files = load_checkpoint()
if completed_files:
    log.info(f"♻️  Reprise : {len(completed_files)} fichiers déjà traités")


# =====================================================
# EXTRACTEURS DIRECTS (non-Textract)
# =====================================================
def extract_pdf_native(filepath):
    """
    Retourne (texte, is_native). is_native=False → nécessite Textract.
    
    Détection robuste en 3 critères :
      1. Moyenne globale : >300 chars/page (un vrai PDF natif FR fait 1500-3000)
      2. Couverture : >80% des pages doivent avoir du texte substantiel (>100 chars)
         → détecte les PDFs mixtes (pages natives + pages scannées)
      3. Fallback : si le PDF a ≤2 pages, on utilise uniquement la moyenne
    
    Seuil 300 chars/page choisi car :
      - Un OCR embarqué de mauvaise qualité produit ~50-150 chars/page
      - Un PDF natif français avec du contenu juridique fait >1500 chars/page
      - Marge de sécurité pour les pages de garde, sommaires courts, etc.
    """
    NATIVE_THRESHOLD_CHARS_PER_PAGE = 300
    MIN_PAGE_CHARS = 100            # Seuil pour qu'une page soit "substantielle"
    MIN_COVERAGE_RATIO = 0.80       # 80% des pages doivent avoir du texte

    try:
        doc = fitz.open(filepath)
        page_count = doc.page_count

        if page_count == 0:
            doc.close()
            return "", False

        page_texts = []
        full_text = ""
        for page in doc:
            pt = page.get_text()
            page_texts.append(pt)
            full_text += pt
        doc.close()

        total_chars = len(full_text.strip())
        avg_chars = total_chars / page_count

        # Critère 1 : moyenne globale
        if avg_chars < NATIVE_THRESHOLD_CHARS_PER_PAGE:
            log.debug(f"PDF scanné (avg {avg_chars:.0f} chars/page < {NATIVE_THRESHOLD_CHARS_PER_PAGE}): {filepath}")
            return "", False

        # Critère 2 : couverture page par page (sauf docs très courts)
        if page_count > 2:
            pages_with_text = sum(1 for pt in page_texts if len(pt.strip()) >= MIN_PAGE_CHARS)
            coverage = pages_with_text / page_count

            if coverage < MIN_COVERAGE_RATIO:
                log.info(f"PDF mixte détecté ({coverage:.0%} couverture, {pages_with_text}/{page_count} pages) → OCR: {os.path.basename(filepath)}")
                return "", False

        return full_text.strip(), True

    except Exception as e:
        log.debug(f"Erreur lecture PDF {filepath}: {e}")
        return "", False

def extract_docx(filepath):
    try:
        doc = DocxDocument(filepath)
        return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    except Exception:
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
            text = raw.decode("utf-8", errors="ignore")
            return "".join(c for c in text if c.isprintable() or c in "\n\r\t")
        except Exception:
            return ""

def extract_excel(filepath):
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        texts = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            texts.append(f"--- Feuille: {sheet_name} ---")
            for row in ws.iter_rows(values_only=True):
                row_text = " | ".join([str(c) for c in row if c is not None])
                if row_text.strip():
                    texts.append(row_text)
        return "\n".join(texts)
    except Exception:
        return ""

def extract_email_msg(filepath):
    try:
        msg = extract_msg.Message(filepath)
        parts = []
        if msg.subject: parts.append(f"Objet: {msg.subject}")
        if msg.sender: parts.append(f"De: {msg.sender}")
        if msg.date: parts.append(f"Date: {msg.date}")
        if msg.body: parts.append(f"\n{msg.body}")
        return "\n".join(parts)
    except Exception:
        return ""

def extract_pptx(filepath):
    try:
        prs = Presentation(filepath)
        texts = []
        for i, slide in enumerate(prs.slides):
            texts.append(f"--- Slide {i+1} ---")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    texts.append(shape.text)
        return "\n".join(texts)
    except Exception:
        return ""

def extract_text_file(filepath):
    for enc in ["utf-8", "latin-1", "cp1252"]:
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    return ""


DIRECT_EXTRACTORS = {
    "word": extract_docx,
    "excel": extract_excel,
    "email": extract_email_msg,
    "texte": extract_text_file,
    "pptx": extract_pptx,
}

DIRECT_EXT_MAP = {
    ".doc": "word", ".docx": "word",
    ".xls": "excel", ".xlsx": "excel",
    ".msg": "email", ".eml": "email",
    ".txt": "texte", ".rtf": "texte", ".csv": "texte",
    ".ppt": "pptx", ".pptx": "pptx",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


# =====================================================
# HELPERS
# =====================================================
def save_extracted(rel_path, ext, text, output_dir):
    """Sauvegarde un résultat d'extraction en JSON."""
    if not text or len(text.strip()) < 20:
        stats["vides"] += 1
        return False

    output_data = {
        "source_file": rel_path,
        "source_extension": ext,
        "copropriete": rel_path.split(os.sep)[0],
        "dossier_parent": os.path.dirname(rel_path),
        "nom_fichier": os.path.basename(rel_path),
        "texte": text,
        "nb_caracteres": len(text)
    }
    output_path = os.path.join(output_dir, rel_path + ".json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    return True


# =====================================================
# PHASE 0 : TRIAGE
# =====================================================
def triage_files():
    """Sépare fichiers en extraction directe vs OCR Textract."""
    direct_files = []   # (filepath, rel_path, ext, extractor_name)
    ocr_files = []      # (filepath, rel_path, ext)

    log.info("Phase 0: Triage des fichiers...")
    for root, dirs, filenames in os.walk(FILTERED_DIR):
        for fname in filenames:
            filepath = os.path.join(root, fname)
            rel_path = os.path.relpath(filepath, FILTERED_DIR)
            ext = Path(fname).suffix.lower()

            if rel_path in completed_files:
                stats["skipped_checkpoint"] += 1
                continue

            if ext in DIRECT_EXT_MAP:
                direct_files.append((filepath, rel_path, ext, DIRECT_EXT_MAP[ext]))
            elif ext == ".pdf":
                text, is_native = extract_pdf_native(filepath)
                if is_native:
                    # Stocker le texte déjà extrait pour éviter double lecture
                    direct_files.append((filepath, rel_path, ext, "pdf_natif"))
                else:
                    ocr_files.append((filepath, rel_path, ext))
            elif ext in IMAGE_EXTS:
                ocr_files.append((filepath, rel_path, ext))

    return direct_files, ocr_files


# =====================================================
# PHASE 1 : EXTRACTION DIRECTE
# =====================================================
def run_direct_extraction(direct_files):
    log.info(f"\nPhase 1: Extraction directe de {len(direct_files)} fichiers...")
    for filepath, rel_path, ext, etype in tqdm(direct_files, desc="Extraction directe"):
        if etype == "pdf_natif":
            text, _ = extract_pdf_native(filepath)
        else:
            text = DIRECT_EXTRACTORS[etype](filepath)

        if save_extracted(rel_path, ext, text, OUTPUT_DIR):
            stats[etype] += 1
            completed_files.add(rel_path)
        else:
            if not text or len(text.strip()) < 20:
                pass  # déjà compté dans save_extracted
            else:
                stats["erreurs"] += 1

    save_checkpoint(completed_files)
    log.info(f"✅ Phase 1 terminée")


# =====================================================
# PHASE 2 : UPLOAD S3 PARALLÈLE
# =====================================================
def upload_one(filepath, s3_key):
    try:
        s3.upload_file(filepath, S3_BUCKET, s3_key)
        return s3_key
    except Exception as e:
        log.error(f"Upload échoué {os.path.basename(filepath)}: {e}")
        return None

def batch_upload_s3(ocr_files):
    log.info(f"\nPhase 2: Upload S3 de {len(ocr_files)} fichiers ({MAX_CONCURRENT_UPLOADS} threads)...")
    s3_map = {}  # rel_path → (s3_key, ext)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_UPLOADS) as executor:
        futures = {}
        for filepath, rel_path, ext in ocr_files:
            s3_key = S3_TEXTRACT_PREFIX + rel_path.replace(os.sep, "/")
            fut = executor.submit(upload_one, filepath, s3_key)
            futures[fut] = (rel_path, s3_key, ext)

        for fut in tqdm(as_completed(futures), total=len(futures), desc="Upload S3"):
            rel_path, s3_key, ext = futures[fut]
            if fut.result():
                s3_map[rel_path] = (s3_key, ext)

    log.info(f"✅ {len(s3_map)}/{len(ocr_files)} fichiers sur S3")
    return s3_map


# =====================================================
# PHASE 3 : LANCEMENT RAFALE TEXTRACT
# =====================================================
def start_textract_job(s3_key, ext):
    """Lance un job. Retourne ('ASYNC', job_id) ou ('SYNC', text) ou None."""
    for attempt in range(MAX_RETRIES):
        try:
            if ext == ".pdf":
                resp = textract.start_document_text_detection(
                    DocumentLocation={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}}
                )
                return ("ASYNC", resp["JobId"])
            else:
                # Images ≤10MB : API synchrone (plus rapide, pas de polling)
                obj = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
                img_bytes = obj["Body"].read()

                if len(img_bytes) > 10_000_000:
                    resp = textract.start_document_text_detection(
                        DocumentLocation={"S3Object": {"Bucket": S3_BUCKET, "Name": s3_key}}
                    )
                    return ("ASYNC", resp["JobId"])

                resp = textract.detect_document_text(Document={"Bytes": img_bytes})
                lines = [b["Text"] for b in resp.get("Blocks", []) if b["BlockType"] == "LINE"]
                return ("SYNC", "\n".join(lines))

        except textract.exceptions.ProvisionedThroughputExceededException:
            time.sleep(2 ** attempt)
        except Exception as e:
            log.error(f"Textract start error {s3_key}: {e}")
            return None
    return None

def launch_all_jobs(s3_map):
    log.info(f"\nPhase 3: Lancement de {len(s3_map)} jobs Textract (rate: {JOB_LAUNCH_RATE}/s)...")
    async_jobs = {}      # job_id → (rel_path, s3_key, ext)
    sync_results = {}    # rel_path → (text, ext)
    failed = []

    for i, (rel_path, (s3_key, ext)) in enumerate(
        tqdm(s3_map.items(), desc="Lancement Textract")
    ):
        result = start_textract_job(s3_key, ext)

        if result is None:
            failed.append(rel_path)
        elif result[0] == "ASYNC":
            async_jobs[result[1]] = (rel_path, s3_key, ext)
        elif result[0] == "SYNC":
            sync_results[rel_path] = (result[1], ext)

        # Rate limiting
        if (i + 1) % JOB_LAUNCH_RATE == 0:
            time.sleep(1.0)

    log.info(f"✅ {len(async_jobs)} async + {len(sync_results)} sync + {len(failed)} échecs")
    return async_jobs, sync_results, failed


# =====================================================
# PHASE 4 : POLLING DES RÉSULTATS ASYNC
# =====================================================
def collect_result(job_id):
    """Récupère toutes les pages de résultat d'un job terminé."""
    text_blocks = []
    kwargs = {"JobId": job_id}
    while True:
        result = textract.get_document_text_detection(**kwargs)
        for block in result.get("Blocks", []):
            if block["BlockType"] == "LINE":
                text_blocks.append(block["Text"])
        next_token = result.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token
    return "\n".join(text_blocks)

def poll_all_jobs(async_jobs):
    log.info(f"\nPhase 4: Polling de {len(async_jobs)} jobs...")
    pending = dict(async_jobs)
    results = {}
    failed = []
    cycle = 0

    while pending:
        cycle += 1
        done_this_round = []
        job_ids = list(pending.keys())[:POLL_BATCH_SIZE]

        for job_id in job_ids:
            try:
                resp = textract.get_document_text_detection(JobId=job_id, MaxResults=1)
                status = resp["JobStatus"]

                if status == "SUCCEEDED":
                    rel_path, s3_key, ext = pending[job_id]
                    text = collect_result(job_id)
                    results[rel_path] = (text, ext)
                    done_this_round.append(job_id)
                elif status == "FAILED":
                    rel_path, _, _ = pending[job_id]
                    log.warning(f"FAILED: {rel_path}")
                    failed.append(rel_path)
                    done_this_round.append(job_id)

            except Exception as e:
                log.warning(f"Poll error: {e}")

        for jid in done_this_round:
            del pending[jid]

        if pending:
            done_n = len(results) + len(failed)
            total_n = done_n + len(pending)
            log.info(f"  Cycle {cycle}: {done_n}/{total_n} ({done_n/total_n*100:.0f}%) — {len(pending)} restants")
            time.sleep(POLL_INTERVAL)

    log.info(f"✅ Polling terminé: {len(results)} OK, {len(failed)} KO")
    return results, failed


# =====================================================
# PHASE 5 : NETTOYAGE S3
# =====================================================
def cleanup_s3(s3_map):
    log.info("\nPhase 5: Nettoyage S3...")
    keys = [s3_key for s3_key, _ in s3_map.values()]
    for i in range(0, len(keys), 1000):
        batch = keys[i:i+1000]
        try:
            s3.delete_objects(
                Bucket=S3_BUCKET,
                Delete={"Objects": [{"Key": k} for k in batch]}
            )
        except Exception as e:
            log.warning(f"Nettoyage S3 partiel: {e}")
    log.info(f"✅ {len(keys)} fichiers temp supprimés")


# =====================================================
# MAIN
# =====================================================
def main():
    # Initialisation du log (le mode 'w' s'occupe de vider le fichier proprement)
    setup_logging()

    print("=" * 60)
    print("  EXTRACTION OPTIMISÉE — PIPELINE PARALLÈLE")
    print("=" * 60)

    # ── Triage ──
    direct_files, ocr_files = triage_files()
    log.info(f"  {len(direct_files)} directs | {len(ocr_files)} OCR | {stats['skipped_checkpoint']} checkpoint")

    # ── Phase 1 : Extraction directe ──
    run_direct_extraction(direct_files)

    if not ocr_files:
        log.info("Aucun fichier OCR. Terminé !")
        print_report()
        return

    # ── Phase 2 : Upload S3 ──
    s3_map = batch_upload_s3(ocr_files)

    # ── Phase 3 : Lancement Textract ──
    async_jobs, sync_results, launch_failed = launch_all_jobs(s3_map)

    # Sauvegarder résultats synchrones immédiatement
    for rel_path, (text, ext) in sync_results.items():
        if save_extracted(rel_path, ext, text, OUTPUT_DIR):
            stats["image_ocr"] += 1
            completed_files.add(rel_path)
    save_checkpoint(completed_files)

    # ── Phase 4 : Polling async ──
    if async_jobs:
        ocr_results, poll_failed = poll_all_jobs(async_jobs)
        for rel_path, (text, ext) in ocr_results.items():
            if save_extracted(rel_path, ext, text, OUTPUT_DIR):
                if ext == ".pdf":
                    stats["pdf_ocr"] += 1
                else:
                    stats["image_ocr"] += 1
                completed_files.add(rel_path)
        stats["erreurs"] += len(poll_failed) + len(launch_failed)
        save_checkpoint(completed_files)

    # ── Phase 5 : Nettoyage ──
    cleanup_s3(s3_map)

    # ── Rapport ──
    print_report()

    if stats["erreurs"] == 0 and os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
    elif stats["erreurs"] > 0:
        ERROR_LOG = os.path.join(SCRIPT_DIR, "erreurs_extraction.txt")
        with open(ERROR_LOG, "w", encoding="utf-8") as f:
            f.write("LISTE DES FICHIERS EN ERREUR\n")
            f.write("="*30 + "\n")
            for rel_id in (launch_failed + (poll_failed if 'poll_failed' in locals() else [])):
                f.write(f"- {rel_id}\n")
        print(f"\n  ⚠️  {stats['erreurs']} erreurs. Liste détaillée dans : {ERROR_LOG}")
        print(f"  Relance le script pour retenter uniquement les fichiers échoués.")


def print_report():
    print("\n" + "=" * 60)
    print("  RAPPORT D'EXTRACTION")
    print("=" * 60)
    print(f"  PDF natifs (texte direct) : {stats['pdf_natif']}")
    print(f"  PDF scannés (Textract)    : {stats['pdf_ocr']}")
    print(f"  Word                      : {stats['word']}")
    print(f"  Excel                     : {stats['excel']}")
    print(f"  Emails                    : {stats['email']}")
    print(f"  Texte/CSV                 : {stats['texte']}")
    print(f"  PowerPoint                : {stats['pptx']}")
    print(f"  Images OCR (plans)        : {stats['image_ocr']}")
    print(f"  Fichiers vides/courts     : {stats['vides']}")
    print(f"  Erreurs                   : {stats['erreurs']}")
    t = stats['pdf_ocr'] + stats['image_ocr']
    print(f"\n  TOTAL Textract            : {t} fichiers")
    print(f"  Coût Textract estimé      : ~${t * 0.0015:.2f}")
    print(f"\n  📁 Résultats : {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
```

**Lance :** `python 02_extraction_optimized.py`

> **Note :** Ce script optimisé utilise le traitement en parallèle avec votre compte AWS. Il permet de diviser le temps par 5 à 10 par rapport à un script séquentiel standard. Le traitement OCR asynchrone pour ~10 000 fichiers prendra environ 30min - 1 heure.

> **Reprise après erreur :** Le script possède un système de sauvegarde automatique. Si vous l'arrêtez, il reprendra à l'endroit exact où il s'est arrêté. À la fin, tous les fichiers posant souci (PDF corrompus, etc.) seront loggués dans un fichier `erreurs_extraction.txt`.


---

## Étape 3 — Chunking et parsing structurel

### 3.1 Stratégie de chunking

Chaque fichier extrait doit être découpé en chunks exploitables par le RAG. La stratégie dépend du type de document :

| Type document | Stratégie de chunking |
|---|---|
| RCP / Règlement | Par article (détection "Article XX") |
| PV d'AG | Par résolution (6 patterns regex + fallback Haiku) |
| Contrats | Par clause/article |
| Devis / Factures | Document entier (généralement court) |
| Courriers | Document entier |
| Emails | Document entier |
| Budgets (Excel) | Par feuille |
| Plans | Document entier (texte OCR limité) |

Le script `03_chunking.py` est maintenu séparément du guide (voir fichier `.py` dédié). Copie la dernière version du fichier `03_chunking.py` **et** `content_filter.py` dans ton dossier de scripts (les deux fichiers doivent être dans le même dossier).

**Résumé des fonctionnalités :**
- **Taxonomie enrichie — 14 types de documents** : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, PLAN, ASSURANCE, **ENTRETIEN** (carnets d'entretien, fiches de maintenance, suivi d'équipements), **SINISTRE** (constats, bilans d'anomalies, rapports d'expertise), **COMPTABILITE** (annexes comptables, journaux, relevés), AUTRE.
- **Filtre de contenu binaire (v2 — `content_filter.py`)** :
  - **Avant chunking** : `analyze_file_quality()` analyse le texte extrait complet et détermine si le contenu est exploitable. Verdict : `OK` (chunker normalement), `PLACEHOLDER` (créer un chunk métadonnée unique), `SKIP` (ignorer le fichier).
  - **Après chunking** : `filter_chunks()` élimine les chunks individuels non exploitables dans les fichiers mixtes (texte + images binaires).
  - **Détecte et bloque** : données pixel RGB, coordonnées d'images, hex brut, markup interne Word (.doc), métadonnées PNG/JPEG, texte avec <35% de caractères alphabétiques.
  - **Laisse passer** : texte OCR dégradé mais lisible, PV d'AG, constats de sinistre, rapports techniques avec quelques codes intégrés.
  - **Placeholder** : pour les fichiers images/Word entièrement binaires, un chunk métadonnée est créé avec le nom du fichier, son chemin, et la mention « contenu non exploitable par OCR ». Le fichier reste référençable sans polluer le retrieval.
  - **Impact mesuré** : sur l'exemple `5390 - FICHE VISITE 2-4-6BIS TARIEL - 28-09-2017.doc` (2422 chunks de garbage), le verdict est PLACEHOLDER → 1 seul chunk au lieu de 2422 chunks poubelle.
- **Classification doc_type en 3 passes** :
  - **Passe 1 — Structure des dossiers** (gratuit, instantané) : match exact sur chaque composant du chemin (`ASSEMBLEE/`, `PV/`, `COMPTA/`, `SINISTRE/`, `ENTRETIEN/`...). Types spécifiques testés avant les types larges. PLAN en dernier (mot trop courant, source de faux positifs).
  - **Passe 2 — Nom du fichier** (gratuit, instantané) : regex avec word boundaries (`\b`). Ordre : `\bcarnet\b.*\bentretien\b` → ENTRETIEN, `\bsinistres?\b|\banomalies?\b|\bconstat\b` → SINISTRE, `\bannexe\b|\bcompta\b` → COMPTABILITE, puis PV_AG avant RCP, PLAN en dernier. **BORDEREAU_AR (v0.5.0)** : détecte les bordereaux d'accusé de réception (`bordereau`, `accus[eé].*réception`, `avis.*réception`, `_deposit.pdf`, `recipients.csv`).
  - **Passe 3 — Contenu via Claude Haiku** (uniquement si passes 1+2 retournent `AUTRE`) : envoie les 1500 premiers caractères à `eu.anthropic.claude-haiku-4-5-20251001-v1:0` pour classification. Prompt enrichi avec les 14 types et descriptions métier. Cache par fichier source. Texte minimum 200 chars, sinon classé `AUTRE` directement.
- Chunking par articles pour les RCP et contrats — **robuste OCR** : tolère les artefacts Textract (mots cassés, accents perdus, retours à la ligne parasites)
- Chunking par résolutions pour les PV d'AG — **robuste OCR** : gère les formats alternatifs (ordinal, "Point N°", numérotation simple, **"1 ELECTION DU PRESIDENT" sans tiret** — Pattern 6 ajouté v0.4.0)
- **Résolutions subdivisées** : quand une résolution dépasse 5000 chars, les sous-chunks sont préfixés `[Suite résolution N — TITRE]` et le dernier reçoit le verdict (adoptée/rejetée)
- **Vérification Haiku renforcée (v0.4.0)** : prompt PV_AG vs ODJ/convocation avec critères de distinction explicites (résultats de vote + verdicts = PV ; projets de résolution sans résultats = COURRIER). Extrait début+fin du document (en-tête + verdicts).
- **Dédup par similarité de contenu (v0.4.0)** : détecte les .docx/.pdf du même document (SequenceMatcher > 85%). Priorité : document signé > .docx > plus long.
- **Classification résolutions PV_AG enrichie (v0.4.0)** : 12 patterns PROCEDURE_AG (vs 1 avant) couvrant scrutateurs, approbation comptes, quitus, syndic, Police/Gendarmerie, budget, honoraires, contrôle comptes, fonds travaux ALUR, seuils CS.
- **BORDEREAU_AR doc_type (v0.5.0)** : les bordereaux d'accusé de réception sont détectés en passe 2 (regex) et chunked via `chunk_whole_document`. Exclus par défaut du retrieval SQL (`c.doc_type != 'BORDEREAU_AR'`) sauf quand Haiku détecte une requête de traçabilité juridique (`include_bordereau_ar=True`).
- Pré-nettoyage systématique du texte OCR (recollage des mots coupés par `\n`, normalisation des espaces multiples)
- Garde-fou `CHUNK_HARD_MAX = 5000` caractères — aucun chunk ne dépasse cette limite (compatible Titan V2)
- **`chunk_whole_document` (v4)** : les documents courts (FACTURE, DEVIS, BUDGET, COURRIER, COMPTABILITE, PLAN) sont gardés en un seul chunk jusqu'à `CHUNK_HARD_MAX` (5000 chars) au lieu d'être découpés à `CHUNK_TARGET_SIZE` (1500 chars). Évite que le content_filter supprime des chunks intermédiaires contenant des montants/mesures, et préserve l'intégrité du contexte.
- Fallback intelligent : si aucun pattern structurel n'est détecté, découpage par taille avec respect des frontières de phrases
- **Rapport de fin** : affiche les stats de classification LLM + les stats du filtre binaire (fichiers OK/placeholder/skip, chunks gardés/filtrés, raisons)

> **Prérequis :** Claude Haiku 4.5 doit être activé dans Bedrock (voir Prérequis). Le model ID est `eu.anthropic.claude-haiku-4-5-20251001-v1:0` (inference profile EU, pas d'appel on-demand direct).
> `content_filter.py` doit être dans le même dossier que `03_chunking.py`.

```

**Lance :** `python 03_chunking.py`

---

## Étape 4 — Extraction métadonnées document-level

L'étape 3 produit `chunks_copro.jsonl` avec `doc_type`, `source_file`, `copropriete`. Il manque des métadonnées structurées au niveau du **document** (pas du chunk) : date de signature, sous-type précis, statut actif/expiré, montant principal, parties concernées, rattachement à un dossier transversal. Ces métadonnées permettront un pré-filtrage SQL en amont du retrieval hybride (étape 7), éliminant le bruit sur les requêtes factuelles et temporelles.

Le script tourne en **3 passes** :
1. **Extraction parallèle** (10 workers) — Haiku extrait les métadonnées de chaque document (doc_type_corrige, date, sous_type, dossier_lie, statut, montant, parties, résumé). **Protection RCP (v0.5.0)** : les documents classés RCP par la structure des dossiers (passe 1) ne peuvent pas être reclassifiés par Haiku (`_TRUSTED_FOLDER_TYPES`). Empêche la perte de RCP contenant des actes notariés que Haiku reclassait en MUTATION.
2. **Consolidation des sous_types** — Un second appel Haiku normalise les variantes synonymes (`DDE_CHAUDIERE` → `DDE`, `CONTRAT_SYNDIC` → `SYNDIC`, etc.) pour homogénéiser le vocabulaire sur l'ensemble du corpus.
3. **Déduplication** — Un troisième appel Haiku identifie, au sein de chaque groupe `(copro, doc_type, année)`, les vrais doublons (PDF + DOCX, brouillon + final) des documents distincts. Chaque document reçoit `groupe_doc` (identifiant du groupe logique) et `est_reference` (true si c'est la version de référence).

### Principe

On agrège les chunks par `source_file`, on construit une fenêtre de texte adaptée au `doc_type`, et on demande à Haiku d'extraire un JSON structuré de métadonnées.

### Fenêtre de lecture adaptative

Tous les `doc_type` n'ont pas leurs métadonnées au même endroit dans le document :
- **En-tête seul** — COURRIER, DEVIS, FACTURE, DIAGNOSTIC, PV_AG, RCP, ENTRETIEN, PLAN, AUTRE : la date, l'objet et les parties sont dans les premiers paragraphes. **→ 2000 premiers caractères.**
- **Tête + queue** — CONTRAT, BUDGET, COMPTABILITE, SINISTRE, ASSURANCE : la date et les parties sont en haut, mais le statut (actif/résilié), le montant total et les clauses d'échéance sont souvent en fin de document. **→ 1500 premiers + 1500 derniers caractères.**

Construction de la fenêtre à partir des chunks (déjà triés par `chunk_index`) :

```python
TETE_QUEUE_TYPES = {"CONTRAT", "BUDGET", "COMPTABILITE", "SINISTRE", "ASSURANCE"}

def build_extraction_window(chunks_du_document, doc_type):
    """Construit la fenêtre de texte pour l'extraction metadata Haiku."""
    all_text = [c["text"] for c in sorted(chunks_du_document, key=lambda x: x["chunk_index"])]
    
    if doc_type in TETE_QUEUE_TYPES:
        # Tête : concat depuis le début jusqu'à ~1500 chars
        head = ""
        for t in all_text:
            if len(head) + len(t) > 1500:
                head += t[:1500 - len(head)]
                break
            head += t + "\n"
        
        # Queue : concat depuis la fin jusqu'à ~1500 chars
        tail = ""
        for t in reversed(all_text):
            if len(tail) + len(t) > 1500:
                tail = t[-(1500 - len(tail)):] + "\n" + tail
                break
            tail = t + "\n" + tail
        
        return head.strip() + "\n\n[...]\n\n" + tail.strip()
    else:
        # En-tête seul : ~2000 premiers chars
        window = ""
        for t in all_text:
            if len(window) + len(t) > 2000:
                window += t[:2000 - len(window)]
                break
            window += t + "\n"
        return window.strip()
```

### Prompt Haiku

```python
METADATA_PROMPT = """Tu es un assistant spécialisé en gestion de copropriété.
Extrais les métadonnées de ce document. Le type de document ACTUEL est : {doc_type}.
Ce type a été déterminé automatiquement par le dossier parent, il peut être INCORRECT.

Réponds UNIQUEMENT par un objet JSON valide, sans commentaire ni markdown :
{{
  "doc_type_corrige": "Le vrai type basé sur le CONTENU parmi : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, MUTATION, PLAN, BORDEREAU_AR, AUTRE",
  "date_document": "YYYY-MM-DD. Si seuls année+mois trouvés → YYYY-MM-01. Si seule année → YYYY-01-01. Si année introuvable → null. Ne JAMAIS inventer.",
  "annee": 2024 ou null,
  "sous_type": "Catégorie précise en UN MOT-CLÉ canonique, ou null — voir règles ci-dessous",
  "parties_concernees": ["nom entreprise", "assureur", "expert"] ou [],
  "statut": "actif|expire|resilie|cloture|en_cours|null",
  "montant_principal": 12500.00 ou null,
  "dossier_lie": "SINISTRE|TRAVAUX|CONTENTIEUX|null — dossier transversal même si le doc_type est différent (ex: FACTURE liée à un SINISTRE, DEVIS lié à des TRAVAUX)",
  "resume_une_ligne": "Description courte du document"
}}

Règles pour doc_type_corrige — UNIQUEMENT une de ces valeurs exactes :
  RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, MUTATION, PLAN, BORDEREAU_AR, AUTRE
- Un procès-verbal d'AG (compte-rendu de votes, résolutions) → PV_AG
- Une convocation à l'AG, un ordre du jour, une feuille de présence → COURRIER
- Un contrat (syndic, maintenance, assurance) → CONTRAT
- Un règlement intérieur / de copropriété → RCP
- Un état daté, pré-état daté, questionnaire acquisition → MUTATION
- Un plan d'architecte, plan technique, DOE → PLAN
- Un bordereau d'accusé de réception, liste de destinataires → BORDEREAU_AR
- Un guide pratique, liste, annexe diverse → AUTRE

Règles pour sous_type — CONVENTIONS DE NOMMAGE :
- Format : MAJUSCULE, un seul terme court, underscores si besoin, PAS d'accents, PAS de pluriel
- UN SEUL sous_type par document (le principal). Jamais de liste séparée par virgules.
- Utiliser le terme MÉTIER LE PLUS COURT et courant. Exemples :
  ✅ DDE (pas DEGAT_DES_EAUX), ✅ MRI (pas MULTIRISQUE_IMMEUBLE), ✅ SYNDIC (pas CONTRAT_DE_SYNDIC)
  ✅ MUTATION (pas PRE_ETAT_DATE), ✅ CHAUFFAGE (pas CHAUDIERE), ✅ DIGICODE (pas CONTROLE_ACCES)
  ✅ DERATISATION (pas RONGEURS), ✅ VMC (pas EXTRACTION), ✅ PARKING (pas STATIONNEMENT)
- Si impossible à déterminer → null

Règles pour le statut :
- Date d'échéance future → "actif"
- Date d'échéance passée → "expire"
- Mentions "résilié", "résiliation", "annulé" → "resilie"
- Mentions "clos", "clôturé", "indemnisé" → "cloture"
- Impossible à déterminer → null

Texte du document :
{texte}"""
```

> **Note :** Le champ `doc_type_corrige` permet à Haiku de corriger la classification automatique (passe 1 du chunking basée sur les dossiers). Exemple : un fichier `CONTRAT SYNDIC 2013.doc` dans un dossier `AG/` est classé PV_AG par la passe 1, mais Haiku le reclassifie CONTRAT. Deux nouveaux types sont désormais reconnus : `MUTATION` (états datés, actes de vente, questionnaires acquisition) et `PLAN` (plans d'architecte, DOE, schémas d'implantation). Le pré-filtrage en étape 7 utilise `COALESCE(doc_type_corrige, doc_type)` pour bénéficier de cette correction.
>
> Le champ `dossier_lie` permet de retrouver des documents liés à un même dossier transversal même si leur `doc_type` est différent : par exemple une `FACTURE` rattachée au dossier `SINISTRE`, ou un `COURRIER` rattaché à `CONTENTIEUX`. La passe de consolidation des `sous_type` fusionne automatiquement les variantes synonymes produites par Haiku (`DDE_CHAUDIERE` → `DDE`, etc.).

### Script `04_metadata_documents.py`

Crée `04_metadata_documents.py` :

```python
"""
ÉTAPE 4 — Extraction métadonnées document-level via Haiku
Lit chunks_copro.jsonl (sortie de l'étape 3), agrège par source_file, extrait metadata via LLM.
Fenêtre de lecture adaptative : en-tête seul ou tête+queue selon le doc_type.
Sortie : documents_metadata.jsonl (1 ligne JSON par document source)
Lance : python 04_metadata_documents.py
"""
import json
import os
import re
import time
import unicodedata
import boto3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"     # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\documents_metadata.jsonl"  # ← MODIFIER
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRIPT_DIR, "metadata_cache.json")
AWS_REGION = "eu-west-1"
LLM_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

# Parallélisme
MAX_WORKERS = 10        # Workers parallèles — baisser à 5 si beaucoup de ThrottlingException
MAX_RETRIES = 3         # Retries par document avant abandon

# Types nécessitant une lecture tête+queue (date/statut/montant souvent en fin de document)
TETE_QUEUE_TYPES = {"CONTRAT", "BUDGET", "COMPTABILITE", "SINISTRE", "ASSURANCE"}


# =====================================================
# Agrégation par source_file
# =====================================================
print("=" * 60)
print("  EXTRACTION MÉTADONNÉES DOCUMENT-LEVEL VIA HAIKU")
print("=" * 60)

print("\nChargement et agrégation des chunks par document source...")
docs = {}  # source_file → {copropriete, doc_type, nom_fichier, chunks: [...]}

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        chunk = json.loads(line)
        sf = chunk["source_file"]
        if sf not in docs:
            docs[sf] = {
                "copropriete": chunk["copropriete"],
                "doc_type": chunk["doc_type"],
                "nom_fichier": chunk["nom_fichier"],
                "total_chunks": chunk.get("total_chunks", 1),
                "chunks": []
            }
        docs[sf]["chunks"].append({
            "text": chunk["text"],
            "chunk_index": chunk["chunk_index"]
        })

print(f"  → {len(docs)} documents sources identifiés")

# Répartition par doc_type
type_counts = {}
for d in docs.values():
    dt = d["doc_type"]
    type_counts[dt] = type_counts.get(dt, 0) + 1
print(f"  Répartition : {dict(sorted(type_counts.items(), key=lambda x: -x[1]))}")
tq_count = sum(1 for d in docs.values() if d["doc_type"] in TETE_QUEUE_TYPES)
print(f"  → {tq_count} documents en mode tête+queue, {len(docs) - tq_count} en mode en-tête seul")


# =====================================================
# Fenêtre de lecture adaptative
# =====================================================
def build_extraction_window(chunks, doc_type):
    """Construit la fenêtre de texte pour l'extraction metadata Haiku.
    - En-tête seul (2000 chars) pour la majorité des doc_types
    - Tête+queue (1500+1500 chars) pour CONTRAT, BUDGET, COMPTABILITE, SINISTRE, ASSURANCE
    """
    all_text = [c["text"] for c in sorted(chunks, key=lambda x: x["chunk_index"])]

    if doc_type in TETE_QUEUE_TYPES:
        # Tête : concat depuis le début jusqu'à ~1500 chars
        head = ""
        for t in all_text:
            if len(head) + len(t) > 1500:
                head += t[:1500 - len(head)]
                break
            head += t + "\n"

        # Queue : concat depuis la fin jusqu'à ~1500 chars
        tail = ""
        for t in reversed(all_text):
            if len(tail) + len(t) > 1500:
                tail = t[-(1500 - len(tail)):] + "\n" + tail
                break
            tail = t + "\n" + tail

        return head.strip() + "\n\n[...]\n\n" + tail.strip()
    else:
        # En-tête seul : ~2000 premiers chars
        window = ""
        for t in all_text:
            if len(window) + len(t) > 2000:
                window += t[:2000 - len(window)]
                break
            window += t + "\n"
        return window.strip()


# =====================================================
# Extraction Haiku
# =====================================================
METADATA_PROMPT = """Tu es un assistant spécialisé en gestion de copropriété.
Extrais les métadonnées de ce document. Le type de document ACTUEL est : {doc_type}.
Ce type a été déterminé automatiquement par le dossier parent, il peut être INCORRECT.

Réponds UNIQUEMENT par un objet JSON valide, sans commentaire ni markdown :
{{
  "doc_type_corrige": "Le vrai type basé sur le CONTENU du document, parmi : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, MUTATION, PLAN, BORDEREAU_AR, AUTRE. Exemples : une convocation → COURRIER, un contrat syndic → CONTRAT, un règlement intérieur → RCP, un ordre du jour seul → COURRIER, une liste de copropriétaires → AUTRE, un guide résidence → AUTRE, un état daté → MUTATION, un plan technique → PLAN",
  "date_document": "YYYY-MM-DD. Si seuls l'année et le mois sont trouvés → YYYY-MM-01. Si seule l'année est trouvée → YYYY-01-01. Si l'année est introuvable ou incertaine → null. Ne JAMAIS inventer une date ou un jour absent du document.",
  "annee": 2024,
  "sous_type": "Catégorie précise en UN MOT-CLÉ canonique, ou null — voir règles ci-dessous",
  "parties_concernees": ["nom entreprise", "assureur", "expert"] ou [],
  "statut": "actif|expire|resilie|cloture|en_cours|null",
  "montant_principal": 12500.00,
  "dossier_lie": "SINISTRE|TRAVAUX|CONTENTIEUX|null — le dossier transversal auquel ce document est rattaché, même si le doc_type est différent (ex: une FACTURE liée à un SINISTRE, un DEVIS lié à des TRAVAUX, un COURRIER lié à un CONTENTIEUX)",
  "resume_une_ligne": "Description courte du document"
}}

Règles pour dossier_lie :
- Un document peut avoir un doc_type (FACTURE, DEVIS, COURRIER, COMPTABILITE...) tout en étant lié à un dossier transversal
- SINISTRE : le texte mentionne un sinistre, dégât des eaux, constat, expertise, indemnisation, dégorgement, fuite
- TRAVAUX : le texte concerne des travaux votés en AG, un chantier, une réfection, un ravalement
- CONTENTIEUX : le texte concerne un impayé, une mise en demeure, une procédure judiciaire, un recouvrement
- Si le doc_type_corrige est déjà SINISTRE, ENTRETIEN ou DIAGNOSTIC → dossier_lie = null (redondant)
- Si aucun dossier transversal identifiable → null

Règles pour sous_type — CONVENTIONS DE NOMMAGE :
- Format : MAJUSCULE, un seul terme, underscores si besoin, PAS d'accents
- TOUJOURS AU SINGULIER : BALCON (pas BALCONS), ASCENSEUR (pas ASCENSEURS), EXTINCTEUR (pas EXTINCTEURS)
- UN SEUL sous_type par document (le principal, celui du titre ou de l'objet). Jamais de liste séparée par virgules.
- Utiliser le terme MÉTIER LE PLUS COURT et courant du domaine syndic/copro.
- REGROUPER sous le terme générique le plus simple. Exemples :
  ✅ DDE (pas DEGAT_DES_EAUX, DÉGÂT_DES_EAUX, DOMMAGES_DES_EAUX, FUITE, INFILTRATIONS)
  ✅ MRI (pas MULTIRISQUE_IMMEUBLE)
  ✅ SYNDIC (pas CONTRAT_DE_SYNDIC)
  ✅ MUTATION (pas PRE_ETAT_DATE, QUESTIONNAIRE_ACQUISITION, AVIS_DE_MUTATION, VENTE_IMMOBILIERE, ACTE_NOTARIE)
  ✅ CHAUFFAGE (pas CHAUDIERE, CALORIFIQUE, CVC, CLIMATISATION)
  ✅ DIGICODE (pas CONTROLE_ACCES, INTERPHONE, SERRURERIE, VIGIK, BADGE_VIGIK, CYLINDRE)
  ✅ DERATISATION (pas RONGEURS, NUISIBLES)
  ✅ VMC (pas EXTRACTION, DESENFUMAGE, EXTRACTEUR, VENTILATION)
  ✅ PARKING (pas STATIONNEMENT, VELOS, BORNES_RECHARGE, BORNE_RECHARGE, IRVE)
  ✅ COMPTE_GESTION (pas COMPTE_DE_GESTION, COMPTES_ANNUELS)
  ✅ FONDS_TRAVAUX (pas FONDS_ALUR, FDS_ALUR, FONDS_DE_TRAVAUX)
  ✅ CHARGES (pas DECOMPTE_CHARGES, SOLDES_COPROPRIÉTAIRES, CHARGES_COMMUNES, APPEL_FONDS)
  ✅ ESPACES_VERTS (pas ELAGAGE, ABATTAGE, ENGAZONNEMENT, ARBORICULTURE, PAYSAGER)
  ✅ FERME_PORTE (pas FERMEPORTE, GROOM)
  ✅ PORTE_AUTOMATIQUE (pas PORTES_AUTOMATIQUES, PORTES, FERMETURE, FERMETURES)
  ✅ RELEVAGE (pas POMPES_RELEVAGE, RELEVAGE_EAUX, POMPE_RELEVAGE)
  ✅ COMPTEUR (pas COMPTEURS, COMPTAGE, COMPTAGE_EAU, RELEVE_COMPTEURS)
  ✅ SECURITE_INCENDIE (pas BAES, SSI, COLONNE_SECHE, DESENFUMAGE, EXTINCTEUR)
  ✅ ETANCHEITE (pas INFILTRATIONS, COUVERTURE, TOITURE quand c'est un problème d'étanchéité)
- Si le document porte sur un sujet non couvert par les exemples ci-dessus, crée un terme court suivant les mêmes conventions
- Si impossible à déterminer → null

Règles pour doc_type_corrige — UNIQUEMENT une de ces valeurs exactes :
  RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, MUTATION, PLAN, BORDEREAU_AR, AUTRE
- PV_AG = procès-verbal d'assemblée générale UNIQUEMENT. Un PV_AG contient obligatoirement les RÉSULTATS de votes (tantièmes pour/contre/abstention, "résolution adoptée/rejetée"). Sans résultats de votes → ce n'est PAS un PV_AG.
- COURRIER = convocation à l'AG, ordre du jour, feuille de présence, procuration, projet de résolutions. ATTENTION : un ORDRE DU JOUR n'est JAMAIS un PV_AG même s'il liste des projets de résolutions — un OJ contient "il sera proposé de voter..." ou liste les résolutions SANS résultats de vote.
- Un brouillon ou projet de PV ("projet PV", "baze PV") reste PV_AG s'il contient des résultats de votes, sinon → COURRIER.
- Un rapport du conseil syndical → AUTRE (pas PV_AG)
- Un compte-rendu rédigé avec les résultats des votes → PV_AG
- Un contrat (syndic, maintenance, assurance, prestation) → CONTRAT
- Un règlement intérieur, règlement de copropriété → RCP
- Un état daté, pré-état daté, questionnaire acquisition, notification de vente → MUTATION
- Un plan d'architecte, plan technique, DOE, schéma d'implantation → PLAN
- Un guide pratique, liste de copropriétaires, annexe diverse → AUTRE
- Un devis, une facture, un budget → DEVIS, FACTURE, BUDGET respectivement
- Une annexe au contrat → CONTRAT

Règles pour le statut :
- Date d'échéance future → "actif"
- Date d'échéance passée → "expire"
- Mentions "résilié", "résiliation", "annulé" → "resilie"
- Mentions "clos", "clôturé", "indemnisé" → "cloture"
- Dossier en cours de traitement → "en_cours"
- Impossible à déterminer → null

Texte du document :
{texte}"""



# Client Bedrock thread-safe (un par thread)
_thread_local = threading.local()

def _get_bedrock():
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


def extract_json(text):
    """Extrait le premier objet JSON valide d'un texte (ignore le bavardage Haiku après le JSON)."""
    text = text.strip()
    text = re.sub(r"^```json?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Trouver le premier '{' et son '}' fermant correspondant
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i+1])
    raise json.JSONDecodeError("Unterminated JSON object", text, start)

# Cache pour reprises (thread-safe)
cache = {}
_cache_lock = threading.Lock()
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    print(f"  → {len(cache)} documents en cache (reprise)")

stats = {"llm_calls": 0, "cache_hits": 0, "errors": 0, "too_short": 0}
_stats_lock = threading.Lock()

FALLBACK_RESULT = {
    "date_document": None, "annee": None, "sous_type": None,
    "parties_concernees": [], "statut": None,
    "montant_principal": None, "resume_une_ligne": None
}


def normalize_sous_type(st):
    """Normalisation mécanique du sous_type — règles de forme, pas de sémantique.
    Corrige les problèmes récurrents que le prompt ne suffit pas à éviter."""
    if not st or st == "null":
        return None
    # Majuscule + strip
    st = st.strip().upper()
    # Accents → ASCII
    st = unicodedata.normalize("NFD", st)
    st = "".join(c for c in st if unicodedata.category(c) != "Mn")
    # Espaces et tirets → underscores
    st = st.replace(" ", "_").replace("-", "_")
    # Supprimer underscores multiples
    st = re.sub(r"_+", "_", st).strip("_")
    # Composite "X, Y, Z" → premier seulement
    if "," in st:
        st = st.split(",")[0].strip().strip("_")
    return st or None


def extract_metadata(source_file, doc_type, texte):
    """Appelle Haiku pour extraire les métadonnées. Thread-safe via locks."""
    with _cache_lock:
        if source_file in cache:
            with _stats_lock:
                stats["cache_hits"] += 1
            return cache[source_file]

    if len(texte.strip()) < 100:
        with _stats_lock:
            stats["too_short"] += 1
        with _cache_lock:
            cache[source_file] = FALLBACK_RESULT
        return FALLBACK_RESULT

    prompt = METADATA_PROMPT.format(doc_type=doc_type, texte=texte[:3500])
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    })

    bedrock_client = _get_bedrock()

    for attempt in range(MAX_RETRIES):
        try:
            response = bedrock_client.invoke_model(
                modelId=LLM_MODEL, body=body,
                contentType="application/json", accept="application/json"
            )
            result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
            metadata = extract_json(result_text)
            # Normalisation mécanique du sous_type
            if metadata.get("sous_type"):
                metadata["sous_type"] = normalize_sous_type(metadata["sous_type"])
            with _stats_lock:
                stats["llm_calls"] += 1
            with _cache_lock:
                cache[source_file] = metadata
            return metadata

        except json.JSONDecodeError as e:
            # JSON invalide — pas de retry, le modèle redonnerait la même chose
            tqdm.write(f"  ⚠️ JSON invalide {os.path.basename(source_file)}: {e}")
            break

        except Exception as e:
            err_str = str(e)
            if "ThrottlingException" in err_str:
                wait = min(2 ** attempt, 15)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue
            tqdm.write(f"  ⚠️ Erreur {os.path.basename(source_file)}: {e}")
            break

    with _stats_lock:
        stats["errors"] += 1
    with _cache_lock:
        cache[source_file] = FALLBACK_RESULT
    return FALLBACK_RESULT


# =====================================================
# Exécution parallèle
# =====================================================
print(f"\nExtraction des métadonnées pour {len(docs)} documents ({MAX_WORKERS} workers)...\n")

start_time = time.time()
results = {}  # source_file → record

def process_one(source_file, doc_info):
    texte = build_extraction_window(doc_info["chunks"], doc_info["doc_type"])
    metadata = extract_metadata(source_file, doc_info["doc_type"], texte)
    return source_file, {
        "source_file": source_file,
        "copropriete": doc_info["copropriete"],
        "nom_fichier": doc_info["nom_fichier"],
        "doc_type": doc_info["doc_type"],
        "total_chunks": doc_info["total_chunks"],
        "premier_texte": doc_info["chunks"][0]["text"][:500] if doc_info["chunks"] else "",
        **metadata
    }

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {
        executor.submit(process_one, sf, di): sf
        for sf, di in docs.items()
    }

    pbar = tqdm(total=len(futures), desc="Metadata")
    cache_save_counter = 0

    for future in as_completed(futures):
        sf, record = future.result()
        results[sf] = record
        pbar.update(1)

        # Sauvegarder le cache tous les 50 documents
        cache_save_counter += 1
        if cache_save_counter % 50 == 0:
            with _cache_lock:
                with open(CACHE_FILE, "w", encoding="utf-8") as fc:
                    json.dump(cache, fc, ensure_ascii=False)

    pbar.close()

# =====================================================
# Consolidation des sous_types par Haiku
# =====================================================
# Collecter tous les sous_types uniques avec leur fréquence
sous_type_counts = {}
for r in results.values():
    st = r.get("sous_type")
    if st:
        sous_type_counts[st] = sous_type_counts.get(st, 0) + 1

if sous_type_counts:
    print(f"\n⏳ Consolidation des {len(sous_type_counts)} sous_types uniques via Haiku...")

    consolidation_prompt = f"""Tu es un expert en gestion de copropriété.
Voici une liste de sous-types de documents extraits automatiquement, avec leur fréquence d'apparition.
Certains sont des variantes, synonymes ou formes singulier/pluriel du même concept.

Produis un mapping JSON qui regroupe les variantes sous la forme canonique la plus courte et standard.
Règles :
- Garder la forme la PLUS COURTE et la plus standard du domaine syndic/copro
- TOUJOURS au singulier sauf si le pluriel est la forme standard (CHARGES, ESPACES_VERTS, FONDS_TRAVAUX)
- Ne mapper que les vrais synonymes/variantes. Ne PAS fusionner des concepts différents.
- Les termes déjà canoniques → se mapper vers eux-mêmes
- Format : un objet JSON plat {{"variante": "canonique", ...}}

Sous-types à consolider :
{json.dumps(sous_type_counts, indent=2, ensure_ascii=False)}"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": consolidation_prompt}]
    })

    try:
        bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        # Retry pour throttling et erreurs réseau
        for attempt in range(3):
            try:
                response = bedrock_client.invoke_model(
                    modelId=LLM_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
                mapping = extract_json(result_text)
                break
            except json.JSONDecodeError:
                raise
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

        # Appliquer le mapping
        remapped = 0
        for sf, record in results.items():
            st = record.get("sous_type")
            if st and st in mapping and mapping[st] != st:
                record["sous_type"] = mapping[st]
                remapped += 1

        # Rapport
        merges = {}
        for old, new in mapping.items():
            if old != new:
                merges.setdefault(new, []).append(old)
        if merges:
            print(f"  ✅ {remapped} documents remappés, {len(merges)} fusions :")
            for canonical, variants in sorted(merges.items()):
                print(f"    {canonical:25s} ← {', '.join(variants)}")
        else:
            print(f"  ✅ Aucune fusion nécessaire")

    except Exception as e:
        print(f"  ⚠️ Consolidation échouée ({e}) — sous_types non fusionnés")

# =====================================================
# Déduplication : groupement par document logique
# =====================================================
print(f"\n⏳ Déduplication des documents par groupe logique...")

# Grouper par (copropriete, doc_type_corrige, annee)
groups = {}
for sf, record in results.items():
    dt = record.get("doc_type_corrige") or record.get("doc_type", "AUTRE")
    annee = record.get("annee")
    copro = record.get("copropriete", "")
    if annee:
        key = (copro, dt, annee)
        groups.setdefault(key, []).append(sf)

# Groupes avec doublons potentiels (>1 fichier)
multi_groups = {k: v for k, v in groups.items() if len(v) > 1}
print(f"  {len(multi_groups)} groupes avec >1 fichier (total {sum(len(v) for v in multi_groups.values())} fichiers)")

# Initialiser tous les documents comme référence unique (groupe = eux-mêmes)
for sf, record in results.items():
    dt = record.get("doc_type_corrige") or record.get("doc_type", "AUTRE")
    annee = record.get("annee")
    copro = record.get("copropriete", "")
    if annee:
        record["groupe_doc"] = f"{dt}_{annee}_{copro}"
    else:
        record["groupe_doc"] = f"{dt}_NODATE_{copro}"
    record["est_reference"] = True

# Pour chaque groupe multi-fichiers, demander à Haiku de trier
DEDUP_PROMPT = """Tu es un expert en gestion documentaire de copropriété.
Voici un groupe de fichiers du même type, même année, même copropriété.
Identifie les VRAIS doublons (copies du même document) et les documents DISTINCTS (sujets différents).

ATTENTION : dans un même type/année, il y a souvent PLUSIEURS documents DIFFÉRENTS.
Par exemple :
- 3 ventes à 3 copropriétaires différents (VENTE JURADO ≠ VENTE KNEITZ) → DISTINCTS
- 3 contrats pour 3 équipements différents (ascenseur ≠ portes ≠ VMC) → DISTINCTS
- 3 états datés pour 3 ventes différentes (Hoche ≠ Suzan ≠ Foucher) → DISTINCTS
- 3 factures de fournisseurs différents → DISTINCTS
- 3 plans d'étages différents (N02, N04, N05) → DISTINCTS
- 2 constats de sinistres différents (ADICEBM ≠ CRUET) → DISTINCTS
- 6 devis numérotés différemment (WY752, WY753...) pour des emplacements différents → DISTINCTS

Sont des COPIES/DOUBLONS uniquement quand :
- Même document en PDF + DOCX (ex: "Contrat syndic 2023.pdf" et "Contrat syndic 2023.docx")
- Version brouillon + version finale (ex: "BAZ PV AG.docx" et "PV AG 2014.pdf")
- Versions numérotées du même document (ex: "V1.docx" et "V2.docx")

Groupe : {doc_type} {annee} — {n} fichiers :
{file_list}

Réponds UNIQUEMENT par un objet JSON valide :
{{
  "reference": "nom_du_fichier_reference (parmi les copies/brouillons, celui qui est le plus complet)",
  "copies": ["copie1", "copie2"],
  "brouillons": ["projet_ou_baze"],
  "distincts": ["fichier_sur_un_sujet_different"]
}}

Critères pour la référence (uniquement parmi les copies du MÊME document) :
- PDF signé ("SIGNE", "FINAL") prime toujours
- Sinon le fichier le plus long (plus de chunks)
- PDF prime sur DOCX
- Si TOUS les fichiers sont distincts, choisis le premier comme reference et mets tous les autres dans distincts"""

bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
dedup_ok = 0
dedup_err = 0

for (copro, dt, annee), source_files in multi_groups.items():
    # Construire la liste pour Haiku — nom + chunks + résumé pour distinguer les sujets
    file_lines = []
    for sf in source_files:
        rec = results[sf]
        resume = rec.get("resume_une_ligne") or ""
        if resume:
            file_lines.append(f"  - {rec['nom_fichier']} ({rec.get('total_chunks', '?')} chunks) → {resume[:100]}")
        else:
            file_lines.append(f"  - {rec['nom_fichier']} ({rec.get('total_chunks', '?')} chunks)")

    prompt = DEDUP_PROMPT.format(
        doc_type=dt, annee=annee, n=len(source_files),
        file_list="\n".join(file_lines)
    )

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    })

    try:
        # Retry pour throttling et erreurs réseau
        for attempt in range(3):
            try:
                response = bedrock_client.invoke_model(
                    modelId=LLM_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
                dedup_result = extract_json(result_text)
                break
            except json.JSONDecodeError:
                raise  # pas de retry sur JSON invalide
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

        ref_name = dedup_result.get("reference", "")
        copies = set(dedup_result.get("copies", []))
        brouillons = set(dedup_result.get("brouillons", []))
        distincts = set(dedup_result.get("distincts", []))
        groupe_id = f"{dt}_{annee}_{copro}"

        for sf in source_files:
            rec = results[sf]
            nom = rec["nom_fichier"]
            rec["groupe_doc"] = groupe_id

            if nom in distincts:
                # Document distinct → son propre groupe
                rec["groupe_doc"] = f"{dt}_{annee}_{copro}_{nom[:20]}"
                rec["est_reference"] = True
            elif nom == ref_name:
                rec["est_reference"] = True
            else:
                rec["est_reference"] = False

        dedup_ok += 1

    except Exception as e:
        dedup_err += 1
        tqdm.write(f"  ⚠️ Dédup échoué {dt} {annee}: {e}")

non_ref = sum(1 for r in results.values() if not r.get("est_reference", True))
print(f"  ✅ {dedup_ok} groupes traités, {dedup_err} erreurs")
print(f"  📊 {non_ref} documents marqués comme copies/brouillons")

# Écriture finale (ordre stable par source_file)
with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for sf in docs:
        if sf in results:
            fout.write(json.dumps(results[sf], ensure_ascii=False) + "\n")

# Sauvegarde finale du cache
with _cache_lock:
    with open(CACHE_FILE, "w", encoding="utf-8") as fc:
        json.dump(cache, fc, ensure_ascii=False)

elapsed = time.time() - start_time


# =====================================================
# Rapport
# =====================================================
all_records = []
with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        all_records.append(json.loads(line))

with_date = sum(1 for r in all_records if r.get("date_document"))
with_sous_type = sum(1 for r in all_records if r.get("sous_type"))
with_statut = sum(1 for r in all_records if r.get("statut"))
with_montant = sum(1 for r in all_records if r.get("montant_principal"))
reclassified = sum(1 for r in all_records if r.get("doc_type_corrige") and r["doc_type_corrige"] != r["doc_type"])

print("\n" + "=" * 60)
print("RAPPORT D'EXTRACTION METADATA")
print("=" * 60)
print(f"\nTerminé en {elapsed:.0f}s ({elapsed/60:.1f} min) — {MAX_WORKERS} workers")
print(f"Documents traités : {len(all_records)}")
print(f"  Appels Haiku : {stats['llm_calls']}")
print(f"  Cache hits   : {stats['cache_hits']}")
print(f"  Trop courts  : {stats['too_short']}")
print(f"  Erreurs      : {stats['errors']}")
print(f"\nCouverture des champs :")
print(f"  date_document    : {with_date}/{len(all_records)} ({100*with_date/len(all_records):.0f}%)")
print(f"  sous_type        : {with_sous_type}/{len(all_records)} ({100*with_sous_type/len(all_records):.0f}%)")
print(f"  statut           : {with_statut}/{len(all_records)} ({100*with_statut/len(all_records):.0f}%)")
print(f"  montant_principal: {with_montant}/{len(all_records)} ({100*with_montant/len(all_records):.0f}%)")

# Reclassification doc_type
print(f"\n📊 Reclassification doc_type par Haiku :")
print(f"  {reclassified}/{len(all_records)} documents reclassifiés ({100*reclassified/len(all_records):.0f}%)")
reclass_details = {}
for r in all_records:
    orig = r["doc_type"]
    corr = r.get("doc_type_corrige") or orig
    if orig != corr:
        key = f"{orig} → {corr}"
        reclass_details[key] = reclass_details.get(key, 0) + 1
if reclass_details:
    for key, count in sorted(reclass_details.items(), key=lambda x: -x[1]):
        print(f"    {key:35s} : {count}")

# Répartition doc_type_corrige
corr_types = {}
for r in all_records:
    ct = r.get("doc_type_corrige") or r["doc_type"]
    corr_types[ct] = corr_types.get(ct, 0) + 1
print(f"\nRépartition doc_type_corrige (post-Haiku) :")
for ct, count in sorted(corr_types.items(), key=lambda x: -x[1]):
    print(f"  {ct:25s} : {count}")

# Répartition sous-types
sous_types = {}
for r in all_records:
    st_val = r.get("sous_type") or "null"
    sous_types[st_val] = sous_types.get(st_val, 0) + 1
print(f"\nRépartition sous-types :")
for st_val, count in sorted(sous_types.items(), key=lambda x: -x[1]):
    print(f"  {st_val:25s} : {count}")

# Répartition statuts
statuts = {}
for r in all_records:
    s = r.get("statut") or "null"
    statuts[s] = statuts.get(s, 0) + 1
print(f"\nRépartition statuts :")
for s, count in sorted(statuts.items(), key=lambda x: -x[1]):
    print(f"  {s:25s} : {count}")

# Déduplication
refs = sum(1 for r in all_records if r.get("est_reference", True))
copies = sum(1 for r in all_records if not r.get("est_reference", True))
groupes = len(set(r.get("groupe_doc", r["source_file"]) for r in all_records))
print(f"\n📋 Déduplication :")
print(f"  Documents référence    : {refs}")
print(f"  Copies/brouillons      : {copies}")
print(f"  Groupes logiques       : {groupes}")

# Détail des groupes avec copies
groups_with_copies = {}
for r in all_records:
    g = r.get("groupe_doc", "")
    groups_with_copies.setdefault(g, []).append((r["nom_fichier"], r.get("est_reference", True)))
for g, members in sorted(groups_with_copies.items()):
    if len(members) > 1 and any(not ref for _, ref in members):
        print(f"\n  Groupe: {g}")
        for nom, is_ref in members:
            tag = "✅ REF" if is_ref else "  copie"
            print(f"    {tag}  {nom}")

print(f"\n📁 Métadonnées : {OUTPUT_FILE}")

# Coût estimé
cost = stats["llm_calls"] * 1000 * 0.80 / 1_000_000  # ~1000 tokens input, $0.80/MTok
print(f"💰 Coût estimé Haiku : ${cost:.2f}")

```

**Lance :** `python 04_metadata_documents.py`

**Sortie :** `documents_metadata.jsonl` — un JSON par ligne, un par document source. Exemple :
```json
{"source_file": "RESIDENCE_LILAS/Contrats/MRI_Generali_2023.pdf", "copropriete": "RESIDENCE_LILAS", "doc_type": "CONTRAT", "doc_type_corrige": "CONTRAT", "date_document": "2023-03-15", "annee": 2023, "sous_type": "MRI", "parties_concernees": ["Generali", "Syndic NCG"], "statut": "actif", "montant_principal": 8500.00, "dossier_lie": null, "groupe_doc": "CONTRAT_2023_RESIDENCE_LILAS", "est_reference": true, "resume_une_ligne": "Contrat assurance multirisque immeuble Generali - Résidence des Lilas"}
```

**Coût estimé :** ~1500 docs × ~1000 tokens input (passe 1) + ~$0.05 (consolidation + dédup) × $0.80/MTok ≈ **~$1.25**

> **⚠️ Limites connues du champ `statut` :** un contrat résilié par courrier séparé ne sera pas détecté comme "résilié" dans le texte du contrat lui-même. Le statut est inféré par heuristique (date d'échéance, mots-clés). Couverture attendue : ~40-60%. Le fallback au pipeline complet (sans pré-filtrage) couvre les cas non déterminés.

---

## Étape 5 — Embedding via Amazon Bedrock (version parallèle)

La version parallèle utilise 15 workers `ThreadPoolExecutor` pour un gain de **5-8x** vs la version séquentielle.

**Caractéristiques de la version parallèle :**
- **15 workers** parallèles (baisser à 10 si beaucoup de `ThrottlingException`)
- **Client Bedrock thread-safe** : un client `boto3` par thread (via `threading.local`)
- **Retry exponentiel** sur `ThrottlingException` (backoff 1s, 2s, 4s… max 30s) — pas de `sleep` fixe
- **Troncature progressive** sur token overflow (`ValidationException`) : réduction de 30% et retry
- **Écriture batch** : flush sur disque tous les 100 chunks (pas à chaque ligne)
- **Résumable** : reprend automatiquement là où il s'est arrêté

Crée `05_embedding.py` :

```python
"""
ÉTAPE 5 — Génération des embeddings via Amazon Bedrock Titan (VERSION PARALLÈLE)
Lance : python 05_embedding.py

Optimisations vs version séquentielle :
  - 15 workers parallèles (ThreadPoolExecutor) : 5-8x plus rapide
  - Retry exponentiel sur ThrottlingException (pas de sleep fixe)
  - Écriture batch (100 chunks) au lieu de flush à chaque ligne
  - Résumable : reprend où il s'est arrêté
"""
import os
import json
import boto3
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\...\chunks_copro.jsonl"    # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\...\chunks_avec_embeddings.jsonl"  # ← MODIFIER
AWS_REGION = "eu-west-1"

EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024

# Parallélisme
MAX_WORKERS = 15        # Workers parallèles — baisser à 10 si beaucoup de ThrottlingException
WRITE_BATCH_SIZE = 100  # Écrire sur disque tous les N chunks
MAX_RETRIES = 5         # Retries par chunk avant abandon

# =====================================================
# Client Bedrock thread-safe (un par thread)
# =====================================================
_thread_local = threading.local()

def get_bedrock_client():
    """Un client boto3 par thread (boto3 clients ne sont pas thread-safe)."""
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


def get_embedding_with_retry(text, chunk_id):
    """Appelle Bedrock Titan avec retry exponentiel. Retourne (chunk_id, embedding) ou (chunk_id, None)."""
    MAX_CHARS = 5000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    body = json.dumps({
        "inputText": text,
        "dimensions": EMBEDDING_DIMENSION,
        "normalize": True
    })

    bedrock = get_bedrock_client()

    for attempt in range(MAX_RETRIES):
        try:
            response = bedrock.invoke_model(
                modelId=EMBEDDING_MODEL, body=body,
                contentType="application/json", accept="application/json"
            )
            result = json.loads(response["body"].read())
            return chunk_id, result["embedding"]

        except Exception as e:
            err_str = str(e)

            # Token overflow → troncature progressive
            if "Too many input" in err_str or "ValidationException" in err_str:
                text = text[:int(len(text) * 0.7)]
                body = json.dumps({
                    "inputText": text,
                    "dimensions": EMBEDDING_DIMENSION,
                    "normalize": True
                })
                continue

            # Throttling → backoff exponentiel
            if "ThrottlingException" in err_str:
                wait = min(2 ** attempt, 30)
                time.sleep(wait)
                continue

            # Autre erreur → retry avec backoff léger
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue

            return chunk_id, None

    return chunk_id, None


# =====================================================
# Exécution
# =====================================================
print("=" * 60)
print("GÉNÉRATION DES EMBEDDINGS — BEDROCK TITAN V2 (PARALLÈLE)")
print(f"Workers: {MAX_WORKERS} | Batch écriture: {WRITE_BATCH_SIZE}")
print("=" * 60)

# 1. Charger les IDs déjà traités
processed_ids = set()
if os.path.exists(OUTPUT_FILE):
    print("Chargement des chunks déjà traités...")
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                processed_ids.add(data["chunk_id"])
            except:
                continue
    print(f"  → {len(processed_ids)} chunks déjà prêts.")

# 2. Charger les chunks à traiter
if not os.path.exists(INPUT_FILE):
    print(f"❌ {INPUT_FILE} introuvable.")
    raise SystemExit(1)

chunks_to_process = []
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        try:
            chunk = json.loads(line)
            if chunk["chunk_id"] not in processed_ids:
                chunks_to_process.append(chunk)
        except:
            continue

total_remaining = len(chunks_to_process)
print(f"Total restants  : {total_remaining}")
print(f"Coût estimé     : ~${total_remaining * 0.00002:.2f}")
print(f"Temps estimé    : ~{total_remaining / MAX_WORKERS / 12:.0f} min\n")

if total_remaining == 0:
    print("✅ Tous les chunks ont déjà un embedding.")
    raise SystemExit(0)

# 3. Traitement parallèle
errors = 0
written = 0
write_buffer = []
write_lock = threading.Lock()

def flush_buffer(fout):
    global write_buffer, written
    with write_lock:
        for chunk_json in write_buffer:
            fout.write(chunk_json + "\n")
        written += len(write_buffer)
        fout.flush()
        write_buffer = []

start_time = time.time()

with open(OUTPUT_FILE, "a", encoding="utf-8") as fout:
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_chunk = {}
        for chunk in chunks_to_process:
            future = executor.submit(get_embedding_with_retry, chunk["text"], chunk["chunk_id"])
            future_to_chunk[future] = chunk

        pbar = tqdm(total=total_remaining, desc="Embedding")
        for future in as_completed(future_to_chunk):
            chunk = future_to_chunk[future]
            chunk_id, embedding = future.result()

            if embedding is not None:
                chunk["embedding"] = embedding
                chunk_json = json.dumps(chunk, ensure_ascii=False)
                with write_lock:
                    write_buffer.append(chunk_json)
                if len(write_buffer) >= WRITE_BATCH_SIZE:
                    flush_buffer(fout)
            else:
                errors += 1

            pbar.update(1)

        pbar.close()
        if write_buffer:
            flush_buffer(fout)

elapsed = time.time() - start_time
rate = (total_remaining - errors) / elapsed if elapsed > 0 else 0

print(f"\n{'=' * 60}")
print(f"✅ Terminé en {elapsed:.0f}s ({elapsed/60:.1f} min)")
print(f"   Chunks traités : {total_remaining - errors}")
print(f"   Erreurs        : {errors}")
print(f"   Débit moyen    : {rate:.1f} chunks/sec")
print(f"📁 {OUTPUT_FILE}")
```

**Lance :** `python 05_embedding.py`

> **Durée estimée :** Pour ~20 000 chunks avec 15 workers, environ **5-10 minutes** (vs 30-60 min en séquentiel). Coût : ~$0.50.

---

## Étape 6 — Stockage dans PostgreSQL + pgvector + table documents

### 6.1 Créer l'instance RDS

Dans la console AWS (ou en CLI) :

```bash
# Créer une instance Postgres minimale
aws rds create-db-instance \
  --db-instance-identifier smarterplan-rag \
  --db-instance-class db.t4g.micro \
  --engine postgres \
  --engine-version 16.4 \
  --master-username ragadmin \
  --master-user-password "CHOISIS_UN_MOT_DE_PASSE_ICI" \
  --allocated-storage 20 \
  --region eu-west-1 \
  --publicly-accessible \
  --vpc-security-group-ids sg-XXXXXXXX
```

> **Important :** Remplace `sg-XXXXXXXX` par un security group qui autorise le port 5432 depuis ton IP. Si tu ne sais pas lequel utiliser, fais-le via la console AWS > RDS > Create database > Easy create > PostgreSQL > Free tier.

> **Coût :** ~$12/mois pour un t4g.micro. Tu peux le supprimer après les essais.

Attends que l'instance soit disponible (~5-10 minutes), puis récupère le endpoint :

```bash
aws rds describe-db-instances --db-instance-identifier smarterplan-rag \
  --query "DBInstances[0].Endpoint.Address" --output text
```

### 6.2 Initialiser la base et l'extension pgvector

Crée `06a_init_db.py` :

```python
"""
ÉTAPE 6a — Initialisation de la base PostgreSQL avec pgvector
Lance : python 06a_init_db.py
"""
import psycopg2

# =====================================================
# CONFIGURATION — Remplace par tes valeurs
# =====================================================
DB_HOST = "smarterplan-rag.xxxxxxxxxxxx.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "CHOISIS_UN_MOT_DE_PASSE_ICI"  # ← MODIFIER

# =====================================================
# Connexion et initialisation
# =====================================================
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
conn.autocommit = True
cur = conn.cursor()

# Activer pgvector
cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
print("✅ Extension pgvector activée")

# Créer la table principale
cur.execute("""
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id        TEXT PRIMARY KEY,
        copropriete     TEXT NOT NULL,
        source_file     TEXT NOT NULL,
        nom_fichier     TEXT NOT NULL,
        doc_type        TEXT NOT NULL,
        chunk_index     INTEGER,
        total_chunks    INTEGER,
        themes          TEXT[],          -- Array de thèmes pour filtrage
        theme_scores    JSONB,
        text            TEXT NOT NULL,
        nb_caracteres   INTEGER,
        embedding       vector(1024),    -- Dimension Titan V2
        text_search     tsvector         -- Full-text search BM25 (français)
    );
""")
print("✅ Table 'chunks' créée")

# Index vectoriel pour recherche par similarité
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_embedding 
    ON chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
""")
print("✅ Index vectoriel IVFFlat créé")

# Index GIN sur les thèmes pour filtrage rapide
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_themes 
    ON chunks USING gin (themes);
""")
print("✅ Index GIN sur themes créé")

# Index sur la copropriété
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_copro 
    ON chunks (copropriete);
""")
print("✅ Index sur copropriete créé")

# Index sur le type de document
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_doctype 
    ON chunks (doc_type);
""")
print("✅ Index sur doc_type créé")

# Ajouter la colonne text_search si elle n'existe pas (table existante)
cur.execute("""
    ALTER TABLE chunks 
    ADD COLUMN IF NOT EXISTS text_search tsvector;
""")
print("✅ Colonne text_search ajoutée (ou déjà présente)")

# Index GIN pour recherche full-text BM25 (français)
cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_chunks_textsearch 
    ON chunks USING gin (text_search);
""")
print("✅ Index GIN full-text (BM25) créé")

# =====================================================
# Table documents — métadonnées document-level (étape 4b)
# =====================================================
cur.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        source_file         TEXT PRIMARY KEY,
        copropriete         TEXT NOT NULL,
        nom_fichier         TEXT NOT NULL,
        doc_type            TEXT NOT NULL,
        doc_type_corrige    TEXT,
        date_document       DATE,
        annee               INTEGER,
        sous_type           TEXT,
        statut              TEXT,
        montant_principal   NUMERIC,
        parties_concernees  TEXT[],
        resume              TEXT,
        total_chunks        INTEGER,
        premier_texte       TEXT
    );
""")
print("✅ Table 'documents' créée")

cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_copro ON documents (copropriete);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_doctype ON documents (doc_type);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_annee ON documents (annee);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_statut ON documents (statut);")
cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_soustype ON documents (sous_type);")
print("✅ Index sur documents créés (copro, doc_type, annee, statut, sous_type)")

cur.close()
conn.close()
print("\n✅ Base de données initialisée avec succès")
```

**Lance :** `python 06a_init_db.py`

### 6.3 Charger les chunks dans la base

Crée `06b_load_db.py` :

```python
"""
ÉTAPE 6b — Chargement des chunks avec embeddings dans PostgreSQL
Lance : python 06b_load_db.py
"""
import json
import os
import psycopg2
from psycopg2.extras import execute_values
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings.jsonl"  # ← MODIFIER
DB_HOST = "smarterplan-rag.xxxxxxxxxxxx.eu-west-1.rds.amazonaws.com"  # ← MODIFIER
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = "CHOISIS_UN_MOT_DE_PASSE_ICI"  # ← MODIFIER

BATCH_SIZE = 100  # Insérer par lots de 100

# =====================================================
# Connexion
# =====================================================
conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
    user=DB_USER, password=DB_PASSWORD
)
cur = conn.cursor()

# Compter les lignes
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    total = sum(1 for _ in f)

print(f"{total} chunks à charger dans PostgreSQL\n")

# =====================================================
# Chargement par batch
# =====================================================
batch = []
loaded = 0

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in tqdm(f, total=total, desc="Chargement DB"):
        chunk = json.loads(line)
        
        # Préparer le tuple pour insertion
        row = (
            chunk["chunk_id"],
            chunk.get("copropriete", ""),
            chunk.get("source_file", ""),
            chunk.get("nom_fichier", ""),
            chunk.get("doc_type", "AUTRE"),
            chunk.get("chunk_index", 0),
            chunk.get("total_chunks", 1),
            chunk.get("themes", []),
            json.dumps(chunk.get("theme_scores", {})),
            chunk["text"],
            chunk.get("nb_caracteres", len(chunk["text"])),
            str(chunk["embedding"])  # pgvector accepte le format string
        )
        
        batch.append(row)
        
        if len(batch) >= BATCH_SIZE:
            execute_values(cur, """
                INSERT INTO chunks 
                (chunk_id, copropriete, source_file, nom_fichier, doc_type,
                 chunk_index, total_chunks, themes, theme_scores,
                 text, nb_caracteres, embedding)
                VALUES %s
                ON CONFLICT (chunk_id) DO NOTHING
            """, batch)
            conn.commit()
            loaded += len(batch)
            batch = []

# Dernier batch
if batch:
    execute_values(cur, """
        INSERT INTO chunks 
        (chunk_id, copropriete, source_file, nom_fichier, doc_type,
         chunk_index, total_chunks, themes, theme_scores,
         text, nb_caracteres, embedding)
        VALUES %s
        ON CONFLICT (chunk_id) DO NOTHING
    """, batch)
    conn.commit()
    loaded += len(batch)

# Vérifier
cur.execute("SELECT COUNT(*) FROM chunks;")
count = cur.fetchone()[0]

# Peupler l'index full-text BM25 (français) pour les chunks qui ne l'ont pas
print("\n⏳ Génération de l'index full-text BM25 (français)...")
cur.execute("""
    UPDATE chunks 
    SET text_search = to_tsvector('french', text)
    WHERE text_search IS NULL;
""")
conn.commit()
updated = cur.rowcount
print(f"✅ Index full-text peuplé pour {updated} chunks")

# =====================================================
# Chargement de la table documents (métadonnées étape 4b)
# =====================================================
METADATA_FILE = r"G:\Mon Drive\...\documents_metadata.jsonl"  # ← MODIFIER (sortie de 04)

if os.path.exists(METADATA_FILE):
    print("\n⏳ Chargement des métadonnées document-level...")
    
    cur.execute("TRUNCATE TABLE documents;")  # Clean reload à chaque fois
    conn.commit()
    
    doc_batch = []
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            row = (
                rec["source_file"],
                rec["copropriete"],
                rec["nom_fichier"],
                rec["doc_type"],
                rec.get("date_document"),     # peut être null
                rec.get("annee"),
                rec.get("sous_type"),
                rec.get("statut"),
                rec.get("montant_principal"),
                rec.get("parties_concernees", []),
                rec.get("resume_une_ligne"),
                rec.get("total_chunks"),
                rec.get("premier_texte", "")[:500]
            )
            doc_batch.append(row)
    
    execute_values(cur, """
        INSERT INTO documents 
        (source_file, copropriete, nom_fichier, doc_type,
         date_document, annee, sous_type, statut, montant_principal,
         parties_concernees, resume, total_chunks, premier_texte)
        VALUES %s
        ON CONFLICT (source_file) DO UPDATE SET
            doc_type = EXCLUDED.doc_type,
            date_document = EXCLUDED.date_document,
            annee = EXCLUDED.annee,
            sous_type = EXCLUDED.sous_type,
            statut = EXCLUDED.statut,
            montant_principal = EXCLUDED.montant_principal,
            parties_concernees = EXCLUDED.parties_concernees,
            resume = EXCLUDED.resume,
            total_chunks = EXCLUDED.total_chunks,
            premier_texte = EXCLUDED.premier_texte
    """, doc_batch)
    conn.commit()
    
    cur.execute("SELECT COUNT(*) FROM documents;")
    doc_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM documents WHERE annee IS NOT NULL;")
    with_date = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM documents WHERE statut IS NOT NULL;")
    with_statut = cur.fetchone()[0]
    
    print(f"✅ {doc_count} documents chargés ({with_date} avec date, {with_statut} avec statut)")
else:
    print(f"\n⚠️ {METADATA_FILE} introuvable — table documents non peuplée.")
    print(f"  Lance d'abord : python 04_metadata_documents.py")

cur.close()
conn.close()

print(f"\n✅ {count} chunks chargés dans PostgreSQL")
```

**Lance :** `python 06b_load_db.py`

---

## Étape 7 — Interface de requête RAG (Streamlit)

C'est le script final qui permet de poser des questions et obtenir des réponses synthétisées, via une interface web Streamlit.

**Fonctionnalités du pipeline de retrieval :**
- Interface web conversationnelle multi-turn avec `st.chat_input` / `st.chat_message`
- **Routeur de requête Haiku (v4)** : un appel Haiku (~300ms, $0.0002/requête) classifie chaque requête et retourne en un seul JSON : la stratégie (inventaire/ciblé/équilibré), le `doc_type` pour le boost RRF, les filtres structurels (année, sous-type, statut), et la détection de suivi conversationnel. Remplace toutes les listes de mots-clés des versions précédentes.
- **`doc_type` détecté = boost dynamique, pas filtre** : le `doc_type` retourné par Haiku est injecté comme bonus **dynamique** dans le score RRF : 0.03 en inventaire, 0.01 en mode équilibré, 0.005 en mode ciblé.
- **Pipeline de retrieval hybride en 5 étapes** :
  0. **Pré-filtrage document** (conditionnel) : si Haiku détecte des contraintes factuelles (année, sous-type, statut), un CTE SQL pré-sélectionne les `source_file` éligibles via la table `documents` (utilise `COALESCE(doc_type_corrige, doc_type)` pour bénéficier des corrections Haiku). Si 0 ou >50 documents → fallback au pipeline complet. Zone idéale : 1 à 20 documents pré-sélectionnés. Protégé par `try/except` → si la table `documents` n'existe pas, fallback silencieux.
  1. **SQL** : similarité vectorielle (pgvector cosine) + BM25 (`ts_rank` français) → classements indépendants (avec `WHERE source_file IN (pre_filtre)` si activé)
  2. **SQL** : **Reciprocal Rank Fusion (RRF)** — fusionne par rang (pas par score). Formule : `score = 1/(60+vec_rank) + 1/(60+bm25_rank) + doc_type_boost`
  3. **SQL** : **Diversité par source** (`PARTITION BY source_file`) — empêche un document volumineux de monopoliser les résultats. Hard cap dynamique par type de requête (inventaire=2/source, ciblé=8/source, défaut=3/source)
  4. **Python** : **FlashRank reranking adaptatif** (cross-encoder `ms-marco-MultiBERT-L-12`) — 3 améliorations pour protéger les chunks OCR bruités :
     - **(D) Bypass FlashRank si pré-filtrage actif** : quand Haiku pré-sélectionne les documents (inventaire sinistres, etc.), FlashRank est court-circuité. Le RRF suffit car les bons docs sont déjà sélectionnés, et FlashRank pénalise lourdement les formulaires manuscrits/scans dégradés. Cap dynamique par source appliqué sur l'ordre RRF.
     - **(A) Injection métadonnées** : quand FlashRank est actif, chaque passage reçoit un en-tête structuré `[DOC_TYPE] nom_fichier_nettoyé` avant le texte OCR. Le cross-encoder voit des termes propres ("Constat amiable", "SINISTRE", "LEMEAU") même quand le contenu est bruité.
     - **(B) Score hybride RRF × FlashRank** : au lieu d'un reranking pur (FlashRank remplace le RRF), le score final est `α × rrf_norm + (1-α) × flashrank_norm` avec `RERANK_RRF_WEIGHT=0.4`. Empêche les chutes brutales (un chunk rang #3 RRF ne peut plus tomber à #68 post-rerank). ~50-100ms de latence.
- **Quota minimum RCP** : après reranking, si <3 chunks RCP dans le top, des chunks RCP sont remontés du pool inférieur en éjectant les derniers non-RCP.
- **Sources PRIMAIRES vs CONTEXTUELLES** : chaque source envoyée à Claude est marquée `[PRIMAIRE]` (SINISTRE, ENTRETIEN, COMPTABILITE, DEVIS, FACTURE — événements distincts) ou `[CONTEXTUEL]` (PV_AG, RCP, CONTRAT). Le prompt exige de couvrir CHAQUE source primaire pour les inventaires.
- **Fenêtre d'analyse dynamique** : `MAX_CHUNKS_LLM_TEMPORAL` = 120 pour tout inventaire, 50 en équilibré/ciblé (réduits à 60/30 en mode démo). `TOP_K_DISPLAY = 20` sources principales + `TOP_K_EXTRA = 120` sources supplémentaires (repliées par défaut).
- **Filtrage résolutions en SQL (v0.4.0)** : les catégories PROCEDURE_AG et ELECTION_CS sont exclues directement dans la clause WHERE SQL (paramètre `exclude_categories`), AVANT le `dynamic_cap` par source. Évite de gaspiller des slots sur des chunks filtrés ensuite.
- **Prefilter doc_type sans date (v0.4.0)** : le prefilter s'active aussi quand Haiku détecte un `doc_type` (ex: PV_AG) même sans filtre temporel. "Liste tous les travaux votés en AG" active le prefilter PV_AG et exclut COURRIER/RCP.
- **Décomposition temporelle (v0.4.0)** : pour les plages >= 3 ans, la requête est décomposée en N sous-requêtes par année avec round-robin (quota minimum ~5 chunks/an garanti).
- `max_tokens` adaptatif : 4096 si >30 chunks analysés, 2500 sinon
- Timeout Bedrock augmenté à 300s (nécessaire pour 120 chunks)
- **System prompt concis** : instruction explicite de concision (~400 mots max sauf inventaires), pas de reformulation de la question, pas de formules de politesse.
- **Mode juriste conditionnel (v0.4.0)** : quand les sources contiennent des documents juridiques (PV_AG, RCP, CONTRAT, ASSURANCE), un bloc "RIGUEUR JURIDIQUE" est ajouté au system prompt. Force le LLM à restituer les verdicts exactement tels qu'écrits, interdit les interprétations ("vote indicatif", "reporté") sauf mention explicite dans le document.
- **Auth gate (v0.4.0)** : login simple avec utilisateurs pilotes (dictionnaire dans `st.secrets[pilot_users]`). Lookup insensible à la casse. `st.stop()` bloque tout le contenu tant que non authentifié.
- **Langfuse tracing (v0.4.0, enrichi v0.5.0)** : une trace par Q/A avec spans `retrieval` et `generation`, user_id, session_id, latences. Feedback 👍👎💬 rattaché aux traces. Flush après chaque réponse. **(v0.5.0)** : tokens LLM (input/output), coût estimé par requête ($/requête basé sur tarifs Sonnet/Haiku), tags (copropriété, stratégie, mode), metadata enrichie (n_chunks_retrieved, n_docs_retrieved, dossier_id). Pinned `langfuse==2.60.4` (v3 casse `.trace()`).
- **Filtrage prompts hors-sujet (v0.4.0)** : classification Haiku (~300ms) avant le retrieval. Si hors-sujet → message de redirection, trace taggée `filtered` dans Langfuse, pas de retrieval ni LLM.
- **Numéro de version (v0.4.0)** : fichier `VERSION` lu au démarrage, affiché dans la sidebar à côté du titre PALIM.
- **Sync sidebar dossier ↔ checkbox (v0.5.0)** : sélectionner un dossier coche automatiquement le filtre ; décocher le filtre désélectionne le dossier. Bidirectionnel.
- **Double retrieval contextuel sécurisé (v0.5.0)** : quand un dossier est actif, le 2e retrieval sans filtre est gardé par `_dossier_filter_on` et un seuil vectoriel minimum (`_CTX_VEC_MIN=0.25`) pour éviter la pollution par des chunks non liés.
- **Exclusion BORDEREAU_AR conditionnelle (v0.5.0)** : Haiku détecte si la requête nécessite des bordereaux AR (traçabilité juridique) via `include_bordereau_ar`. Par défaut exclus du SQL.
- Sidebar avec sliders : "Chunks analysés par l'IA", "Sources affichées", stratégie auto/manuelle

**Multi-turn conversationnel :**
- **UX chat** : `st.chat_input` (barre fixe en bas) + `st.chat_message` (bulles user/assistant). Historique défilable avec sources attachées à chaque réponse.
- **Historique LLM** : les 3 derniers tours de conversation (plafond ~4K tokens) sont injectés dans le tableau `messages` du payload Bedrock.
- **Suivi conversationnel via Haiku (v4)** : le routeur Haiku reçoit la question précédente et détecte automatiquement si la question actuelle est un suivi. Si oui, il produit une `expanded_query` autonome (ex: "et en 2024 ?" → "liste des sinistres en 2024") utilisée pour le retrieval. Badge visuel "🔗 Suite de la conversation". Remplace l'ancienne détection par mots-clés (`expand_followup_query`).
- **Prompt multi-turn** : instruction au LLM de ne pas répéter les infos déjà données.
- **Bouton "🗑️ Nouvelle conversation"** dans la sidebar pour reset.
- **Nettoyage mémoire** : les sources des anciens messages sont purgées de `session_state` ; seul le dernier message conserve ses sources complètes.

**Sources en 2 sections (v2) :**
- **Sources principales** (top 20) : affichées directement avec expanders, scores, rangs rerankés.
- **Sources supplémentaires** (rangs 21 à 50) : dans un `st.expander` replié par défaut, même format d'affichage. Numérotation continue (Source 21, Source 22...).
- Les liens `linkify_sources` couvrent jusqu'à la Source 50, cliquables vers les ancres dans les deux sections.

**Mode Démo et UX :**
- **Mode Démo (toggle sidebar)** : bascule sur `Claude Haiku 4.5` + **streaming** (`invoke_model_with_response_stream`) + chunks réduits (40/30/30 au lieu de 80/50/50). Latence ~15-20s vs ~90s en mode Sonnet. UI allégée (pas d'affichage des thèmes/stratégie pour un rendu plus "produit fini").
- **Liens 3D contextuels** : fichier externe `URL_SP_demo.txt` (format `MOT_CLE : URL`, un par ligne, lignes `#` ignorées). En mode démo, les mots-clés sont cherchés dans la requête, les noms de copro, les noms de fichiers ET les 200 premiers caractères des chunks. Si match, un bandeau avec lien 3D cliquable apparaît avant la réponse.
- **Bouton 📋 Copier** (v2) : copie la réponse complète dans le presse-papier. Le texte est encodé en base64 côté Python pour éviter tout problème d'échappement HTML/JS (backticks, accolades, markdown dans la réponse LLM), puis décodé côté navigateur via `atob()` + `navigator.clipboard.writeText()`.
- **Sidebar lisible en mobile** (v2) : CSS étendu forçant tous les labels, toggles, expanders, spans en blanc (`#e2e8f0 !important`) sur le fond bleu marine.
- **Refactoring `build_llm_payload()`** : construction du system prompt, user prompt et messages (avec historique) extraite dans une fonction partagée entre `generate_answer()` (sync, Sonnet) et `generate_answer_stream()` (streaming, Haiku).
- **Sources cliquables** : `linkify_sources()` transforme les "Source N" dans la réponse en liens `<a href="#source-N">` avec ancres dans les expanders de sources (scroll to source).
- **Connexion DB résiliente** : `get_db_connection()` utilise `st.session_state` au lieu de `@st.cache_resource`, avec test de vivacité (`SELECT 1`) et reconnexion automatique en cas de perte. `autocommit=True` pour éviter les transactions bloquantes.
- **Warmup Bedrock** : au chargement de la page, un appel d'embedding factice élimine ~2-3s de latence TLS+auth sur la première vraie requête.
- **Cache FlashRank** : utilise `tempfile.gettempdir()` au lieu d'un chemin codé en dur (portable Linux/Windows).
- **Sidebar collapse** : `initial_sidebar_state="collapsed"` pour un affichage plus propre en démo.

> **Prérequis :** `pip install flashrank` pour le reranker FlashRank (modèle léger, pas de PyTorch).

Le script `07_query_rag_ui.py` (version locale, pipeline complet avec FlashRank) et `streamlit_app.py` (version Streamlit Cloud, sans FlashRank pour raisons de ressources serveur, compensé par `RERANK_CANDIDATES=200` au lieu de 120, credentials via `st.secrets`) sont maintenus séparément. Copie la dernière version dans ton dossier de scripts.

> **Note :** L'absence de FlashRank en version cloud dégrade légèrement l'exhaustivité des requêtes inventaire (ex: sinistres). La version desktop avec FlashRank reste la référence pour les démonstrations nécessitant une couverture maximale.

> **Diagnostic FlashRank vs OCR (cas LEMEAU) :** Le constat amiable DDE LEMEAU (formulaire manuscrit) atteignait rang #3 RRF mais chutait à #68 post-FlashRank — le cross-encoder pénalise le texte OCR bruité ("Examplaire pour deptine assuréte"). Les corrections A+B+D (bypass pré-filtrage, injection métadonnées, score hybride) ramènent ce chunk en top 5 (inventaire) ou top 20-25 (requête ciblée sans pré-filtrage). Impact sur tout le corpus : tous les constats amiables manuscrits, déclarations de sinistre, vieux scans bénéficient du même traitement.

**Scripts de diagnostic :**
- **`diag_dde_lemeau.py`** : diagnostic complet du constat LEMEAU — recherche dans chunks (par source_file, nom_fichier, full-text), table documents, exploration dossier TARIEL/SINISTRE. Usage : `python diag_dde_lemeau.py`
- **`diag_scores_lemeau.py`** : calcule les scores retrieval détaillés (vec, BM25, RRF, rang global, rank_in_source, pré-filtrabilité) des chunks LEMEAU avec une requête donnée. Usage : `python diag_scores_lemeau.py "requête"`
- **`diag_avant_apres_flashrank.py`** : validation AVANT/APRÈS des corrections FlashRank (A+B+D). Simule 3 classements (FlashRank pur, A+B hybride, D RRF pur) sur les mêmes candidats et compare les rangs des chunks sinistre surveillés. Usage : `python diag_avant_apres_flashrank.py "requête"`
- **`diag_resolutions_ag.py`** : diagnostic couverture des résolutions AG — utilise la table `documents` pour identifier les PV_AG par année, simule le pré-filtrage, mesure l'impact du cap `rank_in_source` à différents niveaux, détaille la couverture par année et le contenu 📋résolution de chaque chunk. Usage : `python diag_resolutions_ag.py "requête"`
- **`diag_metadata.py`** : diagnostic qualité des métadonnées extraites par Haiku (étape 4). Vérifie : couverture des champs, reclassifications doc_type, contrats syndic correctement taggés, convocations → COURRIER, factures liées à sinistres, intégrité des chunks (fix chunk_whole_document). Usage : `python diag_metadata.py`
- **`diag_db_inventory.py`** : inventaire rapide de la base sans appel Bedrock. Usage : `python diag_db_inventory.py "NOM_COPRO"`
- **`diag_retrieval.py`** : trace pas à pas du pipeline de retrieval (étapes A→H). Usage : `python diag_retrieval.py "requête" "NOM_COPRO"`
- **`sim_retrieval.py`** : simulation comparative AVANT/APRÈS corrections de retrieval. Usage : `python sim_retrieval.py "requête" "NOM_COPRO"`

**Intégration Airtable (dossiers sinistres) :**
- **Table `dossiers`** : créée par `06a_init_db.py`, peuplée par `08_airtable_sync.py`. Contient ~100 champs par sinistre (réf Assynco, réf compagnie, lésé, expert, assureur, montants, dates, pipeline, alertes, textes libres).
- **Sidebar dossiers** : affiche la liste des sinistres de la copropriété sélectionnée. Un clic sur un dossier l'injecte comme **chunk virtuel prioritaire** (Source 1) dans chaque requête RAG.
- **`dossier_to_virtual_chunk()`** : convertit un enregistrement `dossiers` en bloc texte structuré (sections : header, alertes, identification, références, parties prenantes, pipeline, dates clés, financier, textes). Score RRF = 1.0 (priorité absolue). Injecté en tête des `search_results` avant le passage au LLM.
- **Limites de troncature des champs texte** (mis à jour mars 2026) :
  - `circonstances`, `dommages_description` : 1500 chars
  - `conclusion_expert` : **2000 chars** (rapport d'expert complet)
  - `commentaire_assureur`, `observations_declaration` : 1500 / 1000 chars
  - `commentaire_assynco` : 1000 chars
  - Commentaires relance, motif rappel : 600 chars
- **Prompt dossier sélectionné** : si un dossier est sélectionné, le system prompt reçoit des instructions impératives de focus exclusif sur ce dossier (ne pas lister les autres), de citation exacte des données Airtable, et de proposition d'actions concrètes (relancer expert, vérifier prescription, etc.).

**Persistance de session (chat_sessions) :**
- **Table `chat_sessions`** : `session_id`, `chat_history` (JSONB), `selected_dossier`, `pending_query`, `updated_at`. Créée par `06a_init_db.py`.
- **UUID de session** dans `st.query_params["sid"]` — survit à un refresh navigateur.
- **Reconnexion automatique** : si `sid` présent dans l'URL au chargement, la conversation et le dossier sélectionné sont restaurés depuis la DB.
- **Récupération de requête interrompue** : si `pending_query` non-NULL au chargement (requête lancée avant déconnexion mobile), un bouton "🔄 Relancer" permet de la resoumettre en 1 clic.
- **TTL** : sessions purgées après 24h. Configurable en étendant la requête de nettoyage dans `06a_init_db.py`.

**Configuration à modifier :**
- `DB_HOST`, `DB_PASSWORD` : coordonnées de ton instance RDS
- `LLM_MODEL` : `eu.anthropic.claude-sonnet-4-6` (production)
- `LLM_MODEL_FAST` : `eu.anthropic.claude-haiku-4-5-20251001-v1:0` (mode démo)
- `DEMO_3D_LINKS_FILE` : chemin vers le fichier `URL_SP_demo.txt` contenant les liens 3D de démo (format `MOT_CLE : URL`, un par ligne)
- `CLIENT_LOGO_FILE` : chemin vers le logo du client (PNG/JPG, fond sombre ou transparent recommandé pour s'intégrer au header bleu marine). Le logo est affiché dans le bandeau header en haut à droite. Par défaut pointe vers `Logo_NCG.png` dans le même dossier que le script. Pour un autre client : remplacer le fichier et adapter le nom dans la constante `CLIENT_LOGO_FILE`. Mettre le chemin à `""` ou supprimer le fichier pour désactiver.
- `RERANK_RRF_WEIGHT` : poids du score RRF dans le mix hybride RRF×FlashRank (défaut 0.4). 0=FlashRank pur, 1=RRF pur. Augmenter vers 0.5-0.6 si le corpus contient beaucoup de scans OCR bruités.

**Lance :** `streamlit run 07_query_rag_ui.py`

> **Première fois :** Streamlit ouvre automatiquement un onglet dans ton navigateur sur `http://localhost:8501`.

---

## Étape 8 — Synchronisation Airtable Assynco → PostgreSQL

Cette étape alimente la table `dossiers` avec les données des sinistres issues de la base Airtable du courtier Assynco. Elle est indépendante du pipeline OCR/RAG et peut être relancée à tout moment pour mettre à jour les données.

**Script :** `08_airtable_sync.py`

**Mode :** UPSERT par `airtable_record_id` (insert si nouveau, update si existant)

**Fonctionnement :**
1. Appel API Airtable REST (v0) avec filtre par formule (`filterByFormula`) pour cibler les sinistres d'une copropriété
2. Pagination automatique (100 records/page via le champ `offset`)
3. Mapping champs Airtable → colonnes PostgreSQL (normalisation des types : dates ISO, booléens, listes → arrays)
4. UPSERT dans la table `dossiers` via `ON CONFLICT (airtable_record_id) DO UPDATE`
5. Calcul automatique de `date_prescription_estimate` (date_ouverture + 2 ans) si non fournie par Airtable
6. Dérivation de `ref_assynco` depuis le champ `Name` du dossier Airtable (regex `A\d{7}`)

**Configuration à modifier dans `08_airtable_sync.py` :**
```python
AIRTABLE_PAT = "patXXX..."          # Personal Access Token Airtable
AIRTABLE_BASE_ID = "appi1ee5p..."   # ID de la base Airtable
AIRTABLE_TABLE_ID = "tblvvkh..."    # ID de la table Sinistres

# Filtres par copropriété — clé = copropriete dans dossiers, valeur = formule Airtable
COPRO_FILTERS = {
    "SOURCE_ARCHIVES": 'OR(FIND("5390",{Name}),FIND("TIVOLI",{Name}))',
    # Ajouter d'autres copros ici :
    # "COPRO_XYZ": 'FIND("1234",{Name})',
}
```

**Pour ajouter une copropriété :**
1. Identifier le code NCG ou nom dans le champ `Name` des dossiers Airtable
2. Ajouter une entrée dans `COPRO_FILTERS` avec la formule de filtre Airtable
3. Relancer `python 08_airtable_sync.py`

**Champs synchronisés (~60 champs) :** références (Assynco, compagnie, expert, client), lésé (nom, tel, email, appt), pipeline (déclaration, expertise, accord, règlement, mise en cause), dates clés (déclaration, mission expert, invitation, première visite, PV, dépôt rapport, règlement, relances, prescription), financier (coût assureur, estimation, franchise, provisions, dommages, indemnités, total réglé, honoraires), textes libres (circonstances, description dommages, conclusion expert, commentaires assureur/Assynco, observations, motif rappel, commentaires relance), alertes (important, judiciaire, en carence, à relancer, prescription_status), contacts (gestionnaire syndic, email, tél, adresse syndic, gestionnaire sinistre).

**Lance :** `python 08_airtable_sync.py`

> **Note :** La table `dossiers` doit exister avant de lancer ce script (créée par `python 06a_init_db.py`).

---

## Récapitulatif des coûts

| Étape | Service | Coût estimé |
|---|---|---|
| OCR (Textract) | ~10 000 pages scannées | ~$15 |
| Classification doc_type (Bedrock Haiku) | ~500-1000 documents AUTRE | ~$0.01 |
| Metadata documents (Bedrock Haiku) | ~1500 documents (étape 4) | ~$1.20 |
| Embeddings (Bedrock Titan) | ~20 000 chunks | ~$0.50 |
| Reranking (FlashRank local) | cross-encoder multilingue | gratuit |
| Requêtes LLM (Bedrock Claude Sonnet) | ~100 requêtes de test | ~$4 |
| RDS Postgres (t4g.micro) | 1 mois | ~$12 |
| S3 stockage | 7 GO | ~$0.16 |
| **TOTAL** | | **~$33** |

> Pense à supprimer l'instance RDS quand tu as fini les essais pour arrêter les frais.

---

## Checklist d'exécution

```
□ Installer Python 3.12+, AWS CLI, pip packages (dont langfuse==2.60.4 — PAS v3+)
□ Activer Textract, Bedrock (Titan + Claude Sonnet 4.6 + Claude Haiku 4.5), RDS dans la console AWS
□ Étape 0 : python 00_inventaire.py → vérifier le rapport
□ Étape 0 : python 01_filtrage.py → vérifier les décisions LLM dans le log
□ Étape 1 : aws s3 sync → upload des fichiers filtrés
□ Étape 2 : PYTHONIOENCODING=utf-8 python 02_extraction_optimized.py → extraction de texte
□ Étape 3 : copier content_filter.py dans le même dossier que 03_chunking.py
□ Étape 3 : PYTHONIOENCODING=utf-8 python 03_chunking.py → découpage intelligent + dédup contenu + classification 3 passes + Haiku verification PV_AG
□          Vérifier le rapport : fichiers placeholder, chunks filtrés, doublons éliminés, classification résolutions
□ Étape 4 : python 04_metadata_documents.py → extraction métadonnées (10 workers parallèles)
□ Étape 5 : ⚠️ SUPPRIMER chunks_avec_embeddings.jsonl et chunks_avec_embeddings_sq.jsonl AVANT re-run
□          PYTHONIOENCODING=utf-8 python 05_embedding.py → embeddings Titan parallèles (~3min)
□ Étape 5b: PYTHONIOENCODING=utf-8 python 05b_synthetic_questions.py → questions Haiku (PV_AG, RCP, CONTRAT)
□ Étape 6 : python 06a_init_db.py (crée tables si nécessaire)
□          PYTHONIOENCODING=utf-8 python 06b_load_db.py (TRUNCATE + INSERT chunks + documents + dossiers)
□         ⚠️ 06b fait TRUNCATE global — efface TOUT y compris les chunks virtuels Airtable
□ Étape 8 : ⚠️ OBLIGATOIRE après chaque Étape 6 :
□          AIRTABLE_PAT="..." DB_HOST="..." DB_PASSWORD="..." python 08_airtable_sync.py
□          → UPSERT des sinistres dans la table dossiers + restauration chunks Airtable
□          Vérifier : SELECT COUNT(*), statut FROM dossiers GROUP BY statut;
□ Streamlit Cloud : vérifier que le code est mergé dans main (seule branche déployée)
□         Configurer st.secrets : [db], [aws], [langfuse], [pilot_users]
□         Reboot app après chaque mise à jour des secrets
□         🔍 Diagnostic : python diag_db_inventory.py "COPRO" → santé de la base
□         🔍 Diagnostic : python diag_retrieval.py "requête" "COPRO" → trace pipeline
```

---

## Exemples de requêtes à tester

Voici des requêtes types qu'un gestionnaire de syndic poserait :

**Requêtes simples (recherche directe) :**
- "Que dit l'article 24 du RCP de la copropriété X ?"
- "Quel est le montant de la dernière facture d'ascenseur ?"
- "Quand a eu lieu la dernière AG ?"

**Requêtes croisées (multi-hop) :**
- "Quels articles du RCP sont liés aux obligations du syndic concernant les parties communes ET les charges spéciales ?"
- "Comparer les budgets prévisionnels des 3 dernières années pour la copro X"
- "Quels travaux ont été votés en AG qui concernent les parties communes ?"

**Requêtes transverses (multi-copropriétés) :**
- "Quelles copropriétés ont des diagnostics amiante positifs ?"
- "Comparer les clés de répartition des charges d'ascenseur entre nos copros"

**Requêtes exhaustives (stratégie auto → diversité, 2/source, boost 0.03, 80 chunks) :**
- "Liste tous les sinistres déclarés dans la copro X depuis 2010"
- "Quels sont tous les travaux votés en AG depuis 5 ans ?"
- "Liste tous les contrats en cours pour la copro X"

**Requêtes ciblées (stratégie auto → profondeur, 8/source, boost 0.005, 50 chunks) :**
- "Que dit l'article 24 du règlement de copropriété ?"
- "Détaille le rapport d'expertise du dégât des eaux bâtiment B"
- "Explique la résolution n°7 du PV d'AG 2023"

**Requêtes factuelles avec pré-filtrage document (v3 — metadata-first) :**
- "Quel est le contrat d'assurance MRI en cours ?" → pré-filtre : `CONTRAT + sous_type=MRI + statut=actif` → 1-2 docs
- "Quel était le budget prévisionnel 2022 ?" → pré-filtre : `BUDGET + annee=2022` → 1-3 docs
- "Liste les sinistres clos depuis 2020" → pré-filtre : `SINISTRE + statut=cloture + annee>=2020` → N docs
- "Quel montant pour le devis ravalement ?" → pré-filtre : `DEVIS + sous_type=RAVALEMENT` → 1-3 docs
- "Quand expire le contrat ascenseur ?" → pré-filtre : `CONTRAT + sous_type=ASCENSEUR + statut=actif` → 1 doc
- "Compare les charges de chauffage entre 2021 et 2023" → pré-filtre : `BUDGET,COMPTABILITE + annee IN (2021,2023)` → 4-6 docs

**Requêtes multi-turn (v2 — query expansion automatique) :**
- Tour 1 : "Quels sinistres ont été déclarés cette année ?"
- Tour 2 : "Et en 2023 ?" → enrichi automatiquement en "Quels sinistres ont été déclarés cette année ? — Et en 2023 ?"
- Tour 3 : "Quel montant pour le dégât des eaux ?" → enrichi avec le contexte du tour 2
- Tour 4 : "Détaille le rapport d'expertise" → utilise l'historique LLM pour comprendre le contexte

---

## Pour aller plus loin (après les essais)

1. **Centraliser la configuration** — Créer un `config.py` partagé avec variables d'environnement pour basculer local→cloud facilement (chemins en dur dans 02-07, DB credentials, modèles Bedrock). Priorité haute avant migration cloud.
2. **Passage à Claude Opus 4.6 Thinking** — Remplacer Sonnet par Opus 4.6 avec adaptive thinking pour les réponses de production. Coût estimé ~×2.5 à ×3.5 par requête (~$95-120/mois vs ~$35-40 pour Sonnet). Paramètre `effort` = "medium" par défaut, "high" pour inventaires. Nécessite de vérifier la disponibilité en Bedrock EU et d'adapter le payload avec le paramètre `thinking`.
3. **Corrections retrieval sinistres** — ✅ **Résolu** (A+B+D) — Détail complet en section « Problèmes connus » ci-dessous et Section 9.1.
4. **⭐ Pipeline adaptatif (Modes 1/2/4 + Query Decomposition + Synthetic Questions)** — Architecture conçue et documentée en **Section 9** ci-dessous. Implémentation à venir : questions synthétiques (ingestion), routeur Haiku multi-mode, Mode 4 SQL exhaustif (Section 10).
5. **Enrichissement metadata v2** — Extraction de métadonnées plus fines par Haiku sur le document entier (pas juste tête+queue) : clauses contractuelles clés, résolutions votées avec résultat du vote, montants détaillés par poste, croisement inter-documents pour le statut (ex: courrier de résiliation → marquer le contrat comme "résilié"). Permet des requêtes type "quelles résolutions ont été votées à l'unanimité ?".
6. **Outil LLM `filtre_documents`** — Ajouter un outil Bedrock (tool use) permettant à Claude de requêter directement la table `documents` pendant la génération de réponse, pour des filtrages plus complexes que ce que le routeur Haiku pré-requête peut détecter.
7. **Reranker API (Cohere)** — Remplacer FlashRank local par le reranker Cohere `rerank-v3.5` (API multilingue, plus précis sur le français juridique, ~$0.001/requête)
8. **Feedback loop** — Logger les requêtes et noter si les réponses sont satisfaisantes pour affiner les poids RRF et le seuil de similarité
9. **Multi-tenant** — Structurer la base pour que chaque syndic n'accède qu'à ses copros
10. **Mise à jour incrémentale** — Ajouter un nouveau document sans tout réindexer
11. **Traitement email** — Use case prioritaire NCG : triage/catégorisation intelligente des emails entrants (non encore implémenté)

---

## Problèmes connus et diagnostics

### Sinistres OCR bruités (RÉSOLU par A+B+D)

**Problème :** Les constats amiables manuscrits (DDE LEMEAU, ADICEBM, CRUET...) étaient dégradés par FlashRank. Le constat LEMEAU atteignait rang #3 RRF mais chutait à #68 post-FlashRank — le cross-encoder pénalise le texte OCR bruité.

**Cause racine :** FlashRank est un cross-encoder qui évalue la qualité linguistique du passage. L'OCR manuscrit ("Examplaire pour deptine assuréte", "2 B's Ruc Herri Tarid") produit un signal de faible qualité → pénalisation.

**Solution implémentée (A+B+D) :**
- **(D)** Bypass FlashRank quand le pré-filtrage est actif → RRF pur, LEMEAU en rang #3
- **(A)** Injection `[DOC_TYPE] nom_fichier_nettoyé` dans le texte FlashRank → signal propre avant le bruit OCR
- **(B)** Score hybride `α×RRF + (1-α)×FlashRank` avec α=0.4 → empêche les chutes brutales

**Validation :** `diag_avant_apres_flashrank.py` — gains de +4 à +71 rangs sur tous les sinistres surveillés. Top 20 en mode A+B = 100% SINISTRE (vs mix CONTRAT/PV_AG/COURRIER avant).

### Résolutions AG à couverture incomplète (DIAGNOSTIC RÉALISÉ — FIX PLANIFIÉ via Mode 4, Section 10)

**Problème :** "Liste toutes les résolutions votées en AG de 2010 à 2018" ne remonte que 15 résolutions sur 71 pour 2018, et manque des années entières.

**Causes racines identifiées (via `diag_resolutions_ag.py`) :**
1. **Cap de diversité trop restrictif** : `dynamic_cap = 80 / 14 groupes = 5`. Un PV de 17 résolutions ne peut en faire passer que 5. À cap=5, seulement 28/84 chunks 2018 passent. Il faut cap=15 pour 76/84.
2. **Duplication massive** : 6 fichiers PV pour 2018 (3 paires de doublons PDF/DOCX/V1/V2), gaspillent des slots sur du contenu identique.
3. **Scores faibles des résolutions techniques** : Les résolutions sur des sujets spécifiques (ravalement, ascenseur) ont un faible score vectoriel face à "liste des résolutions" — le gap sémantique entre le contenu métier et la requête d'inventaire.

**Données clés :**
- 30 fichiers PV_AG pour 2010-2018, 14 groupes uniques, mais seulement ~9 PV distincts (un par année)
- L'AG 2018 contient ~17 résolutions uniques dont ~5 nominations de routine → **~12 résolutions de fond** à couvrir
- Les 71 📋chunks du diagnostic incluent les doublons (6 fichiers × ~12 résolutions chacun)
- Pré-filtrage s'active correctement (30 ≤ 50)
- Impact du cap : cap=5 → ~5 résolutions de fond couvertes par fichier. Avec questions synthétiques : 8-10/12

**Solution retenue : Paire A (Query Decomposition + Synthetic Questions)** — voir **Section 9** ci-dessous.


---

## Section 9 — Pipeline Adaptatif : Architecture Multi-Mode

> Ce chapitre intègre les spécifications de l'évolution vers un pipeline adaptatif piloté par Haiku.
> Il remplace le fichier séparé `specs-pipeline-adaptatif.md` (désormais supprimé).
> Dernière mise à jour : 28 mars 2026

### 9.1 Contexte et décisions actées

#### Ce qui est déjà implémenté

**Corrections FlashRank A+B+D** dans `07_query_rag_ui.py` :
- **(D)** Bypass FlashRank quand `prefilter_active=True` → tri par RRF pur + cap dynamique par source
- **(A)** Injection `[DOC_TYPE] nom_fichier_nettoyé` dans le texte passé à FlashRank
- **(B)** Score hybride `α × rrf_norm + (1-α) × flashrank_norm` avec `RERANK_RRF_WEIGHT=0.4`

**Intégration Airtable + persistance session** dans `streamlit_app.py` :
- Étape 8 — `08_airtable_sync.py` : synchronisation Airtable → table `dossiers` PostgreSQL (UPSERT)
- Chunk virtuel prioritaire (`dossier_to_virtual_chunk`) : Source 1 avec RRF=0.99 quand dossier sélectionné
- Limites troncature textes libres (mars 2026) : `conclusion_expert` → 2000 chars, `circonstances`/`dommages_description`/`commentaire_assureur` → 1500 chars
- Table `chat_sessions` : persistance UUID de session PostgreSQL, récupération après déconnexion mobile

**Refactoring portabilité framework (29 mars 2026)** :
- Toute intelligence applicative extraite de `streamlit_app.py` vers `dossiers_api.py` (module 09)
- Fonctions exportées : `get_dossiers()`, `get_dossier_detail()`, `search_dossiers_for_query()`, `dossier_to_virtual_chunk()`, `enrich_query_with_dossier()`, `merge_with_airtable_chunks()`
- `streamlit_app.py` réduit à des wrappers fins — aucune logique métier inline

#### Problèmes résiduels identifiés

1. **Cap de diversité** : `dynamic_cap ≈ 5` pour PV_AG → ~8-10/12 résolutions de fond couvertes avec questions synthétiques
2. **Pas de couverture temporelle garantie** : années récentes (plus de chunks) dominent les requêtes pluriannuelles
3. **Hard cap LLM = 80 chunks** : insuffisant pour inventaires exhaustifs pluriannuels

### 9.2 Architecture cible : 3 modes de retrieval

Le routeur Haiku (`detect_strategy_haiku`) choisit le mode :

#### Mode 1 : Single-shot sémantique (requête ciblée)
- **Déclencheur :** Question métier précise — "Quel est le contrat d'ascenseur en cours ?"
- **Pipeline :** PgVector + BM25 → RRF → FlashRank A+B → Claude
- **Budget :** 50 chunks, 3 chunks max par source
- **Changement requis :** aucun (comportement actuel)

#### Mode 2 : Single-shot pré-filtré (inventaire court, ≤ 2 ans)
- **Déclencheur :** Inventaire sur périmètre restreint
- **Pipeline :** Pré-filtrage SQL → PgVector + BM25 → RRF pur (bypass FlashRank D) → Claude
- **Budget :** 80 chunks, cap dynamique par source
- **Changement requis :** aucun (comportement A+B+D actuel)

#### Mode 4 : Structurel Exhaustif (grand inventaire pluriannuel)
- **Déclencheur :** Demande exhaustive sur documents structurés (PV_AG, RCP, Contrats)
- **Pipeline :** Extraction JSON par Haiku → SQL pur (zéro vecteur) → Map-Reduce Haiku si > 30k tokens → Claude Sonnet
- **Garantie :** Rappel 100%
- **Détail complet :** voir **Section 10** ci-dessous

*(Le Mode 3 "Map-Reduce vectoriel multi-requêtes" est abandonné au profit du Mode 4 SQL pur, plus rapide et plus fiable.)*

### 9.3 Plan Phase 1 : Questions Synthétiques + Query Decomposition

#### Chantier 1 : Synthetic Questions — ~2h (ingestion)

**Fichiers :** `03_chunking.py`, `06b_load_db.py`, `06a_init_db.py`

- Générer 1-3 questions hypothétiques par chunk pour PV_AG, RCP, CONTRAT (chunk_index > 0)
- Ne PAS générer pour : préambules (chunk_index = 0), SINISTRE, DEVIS, FACTURE, COURRIER
- Stocker dans colonne `synthetic_questions TEXT` — indexé dans `text_search` BM25

Schéma DB :

```sql
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS synthetic_questions TEXT;
UPDATE chunks SET text_search = to_tsvector('french', text || ' ' || COALESCE(synthetic_questions, ''))
WHERE synthetic_questions IS NOT NULL;
```

Prompt Haiku de génération :

```
Tu es un expert en gestion de copropriété. Génère 1 à 3 questions COURTES auxquelles
cet extrait répond directement. Règles :
- Chaque question doit avoir sa réponse COMPLÈTE dans le texte
- Utilise le vocabulaire métier syndic/copro
- Ne génère PAS de question dont la réponse n'est pas dans le texte
- Format : une question par ligne, sans numérotation
Extrait : {chunk_text}
```

Coût estimé : ~5000 chunks × ~$0.0001 = **~$0.50**

#### Chantier 2 : Query Decomposition — ~2h (retrieval)

Dans `detect_strategy_haiku()`, ajouter `decompose` au JSON de sortie.
Si `strategie=inventaire` ET plage > 3 ans : `decompose = ["résolutions AG 2010", ..., "résolutions AG 2018"]`

Avec parallélisation ThreadPoolExecutor : ~18-21s total vs ~105s actuel.
Claude final reçoit des synthèses compactes (~4K tokens) au lieu de 80 chunks bruts (~120K tokens).

### 9.4 Métriques de succès

| Métrique | Baseline | Cible Phase 1 | Cible Phase 2 |
|---|---|---|---|
| Résolutions de fond 2018 couvertes | ~5/12 | 8-10/12 | 12/12 |
| Couverture temporelle 2010-2018 | ~6/9 (67%) | 9/9 (100%) | 9/9 (100%) |
| Latence inventaire large | ~110s | ~25-30s | ~20s |
| Coût par requête inventaire | ~$0.03 | ~$0.035 | ~$0.04 |

---

## Section 10 — Mode 4 : Structurel Exhaustif (Self-Querying SQL)

> Ce chapitre remplace le fichier séparé `specs-pipeline-mode4-exhaustif.md` (désormais supprimé).
> Dernière mise à jour : 28 mars 2026

### 10.1 Changement de paradigme

Les requêtes d'inventaire exhaustif ("Toutes les résolutions AG 2010-2025") sont des problèmes **relationnels**, pas sémantiques. Le pipeline RAG classique échoue par :
1. **Éclipse temporelle** : 2018 (dense en chunks) écrase 2010
2. **Cap de diversité** : X chunks max par fichier → impossible d'extraire toutes les résolutions d'un PV
3. **Plafond contextuel** : 80 chunks bruts ~120K chars → "Lost in the middle"

**Solution Mode 4** : bypass total vectoriel/BM25. Haiku traduit la requête en filtres SQL exacts. Rappel = 100%.

### 10.2 Routeur Haiku — Nouveau JSON de sortie

```json
{
  "strategie": "inventaire_exhaustif",
  "explication": "Demande exhaustive pluriannuelle — extraction de base de données",
  "sql_filters": {
    "doc_type": "PV_AG",
    "annee_min": 2010,
    "annee_max": 2025,
    "require_chunk_index_gt_zero": true
  }
}
```

> **Règle métier :** Si `doc_type == "PV_AG"`, forcer `require_chunk_index_gt_zero = true` pour exclure les feuilles de présence et préambules.

### 10.3 Fonction `search_exact_sql()`

À créer dans `07_query_rag_ui.py`. Aucun calcul vectoriel ni BM25 — SQL pur.

```sql
SELECT chunk_id, file_name, chunk_index, text, annee, doc_type
FROM chunks
WHERE code_ncg = :copropriete
  AND doc_type = :doc_type          -- si fourni
  AND annee >= :annee_min           -- si fourni
  AND annee <= :annee_max           -- si fourni
  AND chunk_index > 0               -- si require_chunk_index_gt_zero
ORDER BY annee DESC, file_name ASC, chunk_index ASC
```

Garantie : 100% des chunks correspondant aux filtres sont retournés.

### 10.4 Filtre anti-bruit post-SQL

Puisque 150-300 chunks peuvent être retournés (>100k tokens sur 15 ans d'AG), deux passes de filtrage :

**Passe 1 — Heuristique regex (gratuit, ~30-40% de réduction)**

Mots-clés d'exclusion : "désignation du président de séance", "scrutateur", "élection du conseil syndical", "approbation des comptes", "quitus au syndic"

**Passe 2 — Map-Reduce Haiku (si > 30k tokens)**

1. Grouper les chunks filtrés par année
2. Envoyer chaque année en parallèle (ThreadPoolExecutor) à Haiku avec ce prompt :

```
Tu es un expert en copropriété. Voici les résolutions de l'année {annee}.
L'utilisateur cherche : "{requete}".
Extrais un résumé concis des résolutions MÉTIER pertinentes (travaux, litiges,
sinistres, modifications RCP, contrats).
IGNORE absolument les résolutions administratives de routine (élection de syndic,
présidence de séance, approbation comptable, renouvellement du conseil) sauf si
la requête porte explicitement sur la gouvernance.
```

3. Synthèses annuelles Haiku (~10% du volume original) → Claude Sonnet agrège en réponse finale

### 10.5 Plan d'implémentation

| Phase | Contenu | Durée |
|---|---|---|
| Phase 1 | Modifier routeur Haiku + créer `search_exact_sql()` + tests unitaires | ~2h |
| Phase 2 | Groupeur Python par année + synthèse Haiku async + gestion budget tokens | ~3h |
| Phase 3 | UI Streamlit (indicateur "Recherche exhaustive...") + adapter prompt système | ~1h |

### 10.6 Métriques cibles

| Métrique | Mode vectoriel actuel | Mode 4 SQL cible |
|---|---|---|
| Rappel résolutions 2018 | ~5/12 (42%) | **12/12 (100%)** |
| Couverture temporelle 2010-2025 | Partielle (éclipse temporelle) | **100% des années disponibles** |
| Latence | ~20-30s | **~15s** (1 appel SQL + Map-Reduce Haiku parallèle) |
| Bruit chunks hors-sujet | Moyen | **Zéro** (chunk_index > 0 + filtre regex) |
| Fiabilité perçue | Frustration sur les "oublis" | Exhaustivité de type ERP/base de données |

---

## Section 11 — UX Dossiers et Anti-contamination RAG (mise à jour 30 mars 2026)

### 11.1 Messages contextuels utilisateur

**Problème identifié :** L'utilisateur peut oublier qu'un filtre dossier est actif et poser des questions générales qui seront mal traitées. Inversement, une question mentionnant un dossier précis sans filtre activé produit des résultats moins précis.

**Implémentation dans `streamlit_app.py` :**

**Message 1 — Filtre actif** (bandeau persistent en haut du chat) :
Quand `selected_dossier` est défini et `dossier_filter_active=True`, afficher un `st.info` avec le nom/ref du dossier filtré :

> *"📋 Filtre dossier actif : **A2110292** — [Nom dossier]. Vos questions portent sur ce dossier uniquement. Pour une question générale sur les archives, décochez « 📋 Filtrer par ce dossier » dans le panneau latéral (☰ sur mobile)."*

**Message 2 — Dossier auto-détecté** (dans la réponse, si aucun filtre actif) :
Quand `merge_with_airtable_chunks()` injecte des chunks Airtable via matching textuel mais qu'aucun dossier n'est explicitement sélectionné, afficher :

> *"💡 Dossier(s) Assynco trouvé(s) dans votre question : **[Nom]**. Pour concentrer la recherche sur ce dossier, sélectionnez-le dans le panneau latéral (☰ sur mobile) et activez 📋 Filtrer par ce dossier."*

### 11.2 Anti-contamination du retrieval RAG — Double Retrieval avec provenance (30 mars 2026)

**Problème initial :** Quand un dossier est sélectionné, `enrich_query_with_dossier()` injectait le `lese_nom` (ex: "MARROUNI") et les 50 premiers chars de `circonstances` dans la requête BM25/vectorielle. Ces termes génériques remontaient des chunks d'**autres dossiers** partageant le même lésé ou le même type de sinistre — Claude les traitait alors comme s'ils décrivaient le dossier principal.

**Première correction (trop restrictive) :** Suppression totale de `lese_nom` et `circonstances` de la requête enrichie. Résultat : plus de pollution, mais perte des associations légitimes (sinistre antérieur du même lésé, travaux ayant causé le dommage en cascade, etc.).

**Solution finale : Double Retrieval avec étiquetage de provenance**

Deux requêtes SQL parallèles dans le bloc `search_decomposed()` :

| Retrieval | Termes d'enrichissement | Budget | Fonction |
|---|---|---|---|
| **Strict** (`enrich_query_with_dossier`) | `ref_assynco` + `ref_cie` uniquement | MCL=3, CPS=1 | Documents *de* ce dossier |
| **Contextuel** (`enrich_query_contextual`) | refs + `lese_nom` + `circonstances[:40]` | max=2, CPS=1 | Sinistres *connexes* |

Les deux ensembles sont fusionnés (déduplication par `chunk_id`), puis étiquetés dans le contexte LLM :

| Label dans le prompt | Signification |
|---|---|
| `[DOSSIER PRINCIPAL]` | Source 1 — chunk virtuel Airtable du dossier sélectionné |
| `[DOCUMENT ASSOCIÉ AU DOSSIER]` | Chunk issu du retrieval strict (même dossier, archives) |
| `[CONTEXTE CONNEXE]` | Chunk issu du retrieval contextuel (autre dossier potentiellement lié) |

**System prompt dossier (instruction point 4) :**
- Sources `[DOCUMENT ASSOCIÉ]` → compléter la Source 1 (constats, rapports, courriers)
- Sources `[CONTEXTE CONNEXE]` → signaler le lien si pertinent (`"Note connexe : [nom source] — lié car [raison]"`) ; ne pas attribuer leurs données au dossier principal sans le mentionner ; ignorer si non pertinent

**Fichiers modifiés :**
- `dossiers_api.py` : ajout de `enrich_query_contextual()` ; `enrich_query_with_dossier()` reste strict (refs uniquement)
- `streamlit_app.py` : double appel `search_decomposed()` dans le spinner ; `_strict_chunk_ids` transmis à `build_llm_payload()`→`generate_answer[_stream]()`; boucle de contexte utilise `dossier_strict_ids` pour les labels ; system prompt point 4 remplace "FOCUS EXCLUSIF" par la logique de provenance

**Résultat attendu :** Sur MARROUNI DDE, les chunks du dossier sélectionné arrivent en `[DOCUMENT ASSOCIÉ]`, un éventuel sinistre antérieur du même lésé arrive en `[CONTEXTE CONNEXE]`. Claude peut signaler le lien sans confondre les deux dossiers.

### 11.3 Mobile

- `initial_sidebar_state="collapsed"` : sidebar fermée par défaut (UX mobile propre)
- CSS POINT 1 : tous les labels sidebar en blanc `#e2e8f0` sur fond bleu marine
- Session persistée PostgreSQL (`chat_sessions`) avec UUID dans `st.query_params["sid"]` : résiste aux déconnexions mobiles
- Tous les messages contextuels (§11.1) incluent `(☰ sur mobile)` pour guider vers le menu hamburger

---
