"""
ÉTAPE 4 — Enrichissement thématique des chunks
Ajoute les tags thématiques métier à chaque chunk pour le filtrage hybride.
Lance : python 04_enrichissement.py
"""
import os
import json
import re
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"     # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_enrichis.jsonl"  # ← MODIFIER

# =====================================================
# Dictionnaire thématique métier — syndic de copropriété
# =====================================================
# Chaque thème contient des mots-clés et variantes.
# Un chunk est taggé avec un thème si au moins 2 mots-clés sont présents
# (seuil ajustable par thème).

THEMES = {
    "syndic_obligations": {
        "keywords": [
            "syndic", "obligation du syndic", "mission", "mandat", "gestionnaire",
            "responsabilité du syndic", "compte séparé", "reddition des comptes",
            "rapport de gestion", "représentant légal"
        ],
        "min_matches": 1
    },
    "parties_communes": {
        "keywords": [
            "parties communes", "partie commune", "communs", "hall", "toiture",
            "façade", "escalier", "ascenseur", "jardin commun", "parking commun",
            "couloir", "palier", "cave commune", "local commun", "gros oeuvre",
            "structure", "fondation", "terrasse commune"
        ],
        "min_matches": 1
    },
    "parties_privatives": {
        "keywords": [
            "parties privatives", "partie privative", "privatif", "privative",
            "lot", "appartement", "cave privative", "box", "parking privatif",
            "tantième", "millième", "quote-part", "jouissance exclusive"
        ],
        "min_matches": 1
    },
    "charges_generales": {
        "keywords": [
            "charges générales", "charge générale", "conservation", "entretien",
            "administration", "article 10", "budget prévisionnel", "appel de fonds",
            "provision", "régularisation", "dépenses courantes"
        ],
        "min_matches": 1
    },
    "charges_speciales": {
        "keywords": [
            "charges spéciales", "charge spéciale", "utilité", "ascenseur",
            "chauffage collectif", "eau chaude", "antenne collective",
            "service collectif", "équipement commun", "clé de répartition"
        ],
        "min_matches": 1
    },
    "assemblee_generale": {
        "keywords": [
            "assemblée générale", "ag", "ordre du jour", "convocation",
            "majorité", "article 24", "article 25", "article 26", "article 49",
            "vote", "résolution", "quorum", "procuration", "mandataire",
            "scrutin", "scrutateur"
        ],
        "min_matches": 1
    },
    "conseil_syndical": {
        "keywords": [
            "conseil syndical", "président du conseil", "membre du conseil",
            "contrôle", "assistance", "avis du conseil"
        ],
        "min_matches": 1
    },
    "travaux": {
        "keywords": [
            "travaux", "ravalement", "rénovation", "amélioration", "urgence",
            "mise en conformité", "réhabilitation", "maître d'oeuvre",
            "appel d'offre", "devis", "entreprise", "chantier",
            "travaux privatifs", "autorisation de travaux"
        ],
        "min_matches": 1
    },
    "mutations_ventes": {
        "keywords": [
            "vente", "cession", "mutation", "état daté", "pré-état daté",
            "notaire", "acquéreur", "copropriétaire sortant",
            "opposition", "privilège", "dette du vendeur"
        ],
        "min_matches": 1
    },
    "assurance_sinistres": {
        "keywords": [
            "assurance", "sinistre", "dommage", "responsabilité civile",
            "dégât des eaux", "incendie", "multirisque", "indemnisation",
            "déclaration de sinistre", "expert", "franchise"
        ],
        "min_matches": 1
    },
    "contentieux": {
        "keywords": [
            "contentieux", "mise en demeure", "assignation", "tribunal",
            "impayé", "recouvrement", "huissier", "procédure judiciaire",
            "injonction", "saisie", "commandement de payer"
        ],
        "min_matches": 1
    },
    "diagnostics_techniques": {
        "keywords": [
            "diagnostic", "dpe", "amiante", "plomb", "termite", "gaz",
            "électricité", "carnet d'entretien", "dtt", "dtg",
            "audit énergétique", "performance énergétique"
        ],
        "min_matches": 1
    },
    "reglement_interieur": {
        "keywords": [
            "règlement intérieur", "nuisance", "bruit", "usage",
            "destination de l'immeuble", "jouissance", "trouble",
            "bon voisinage", "animaux", "local poubelle"
        ],
        "min_matches": 1
    },
    "comptabilite": {
        "keywords": [
            "budget", "comptabilité", "bilan", "annexe comptable",
            "solde", "crédit", "débit", "trésorerie", "fonds de travaux",
            "compte bancaire", "relevé", "exercice comptable"
        ],
        "min_matches": 1
    },
    "personnel_immeuble": {
        "keywords": [
            "gardien", "concierge", "employé d'immeuble", "contrat de travail",
            "convention collective", "salaire", "loge", "ménage"
        ],
        "min_matches": 1
    }
}

def tag_themes(text):
    """Retourne la liste des thèmes matchés pour un texte donné."""
    text_lower = text.lower()
    matched_themes = []
    theme_scores = {}
    
    for theme_name, config in THEMES.items():
        matches = 0
        for keyword in config["keywords"]:
            if keyword.lower() in text_lower:
                matches += 1
        
        if matches >= config["min_matches"]:
            matched_themes.append(theme_name)
            theme_scores[theme_name] = matches
    
    return matched_themes, theme_scores

# =====================================================
# Exécution
# =====================================================
# Nettoyage de l'ancien fichier d'enrichissement
if os.path.exists(OUTPUT_FILE):
    print(f"Nettoyage de l'ancien fichier : {OUTPUT_FILE}")
    os.remove(OUTPUT_FILE)

print("=" * 50)
print("ENRICHISSEMENT THÉMATIQUE DES CHUNKS")
print("=" * 50)

# Vérifier si le fichier source existe
if not os.path.exists(INPUT_FILE):
    print(f"❌ Le fichier {INPUT_FILE} n'existe pas. Lance d'abord l'étape 03.")
    import sys
    sys.exit(1)

# Compter les lignes
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    total = sum(1 for _ in f)

print(f"\n{total} chunks à enrichir\n")

theme_global_stats = {}
chunks_sans_theme = 0

with open(INPUT_FILE, "r", encoding="utf-8") as fin, \
     open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    
    for line in tqdm(fin, total=total, desc="Enrichissement"):
        chunk = json.loads(line)
        
        # Tagger les thèmes
        themes, scores = tag_themes(chunk["text"])
        
        chunk["themes"] = themes
        chunk["theme_scores"] = scores
        
        if not themes:
            chunks_sans_theme += 1
        
        for t in themes:
            theme_global_stats[t] = theme_global_stats.get(t, 0) + 1
        
        fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")

# =====================================================
# Rapport
# =====================================================
print("\n" + "=" * 50)
print("RAPPORT D'ENRICHISSEMENT")
print("=" * 50)
print(f"\nChunks sans thème identifié : {chunks_sans_theme} / {total}")
print(f"\nDistribution des thèmes :")
for theme, count in sorted(theme_global_stats.items(), key=lambda x: -x[1]):
    print(f"  {theme:30s} : {count:5d} chunks")
print(f"\n📁 Chunks enrichis : {OUTPUT_FILE}")
