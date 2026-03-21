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
