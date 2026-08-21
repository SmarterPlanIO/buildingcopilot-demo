"""
Profil de conventions documentaires par client — consommé par la détection des
version chains et le soft delete (03_chunking).

Principe : « le LLM apprend, le code applique ». Le moteur de near-duplicate
detection (`dedup_confirm.py`) est invariant produit ; ce qui varie d'un syndic
à l'autre, ce sont les CONVENTIONS de nommage et d'archivage (suffixe `_RGPD`
chez Delacour, préfixes d'anciens syndics, vracs de reprise...). Elles vivent
ici en données, jamais en dur dans le code :

    DEFAULTS (produit)  <-  clients/<client>/dedup_rules.json (override partiel)

Le fichier client ne redéfinit que les clés qu'il surcharge. Les clés de type
liste REMPLACENT la valeur produit (pas de fusion : un client doit pouvoir
retirer un marqueur produit qui le gêne).

À terme, le fichier client sera généré par le skill d'audit corpus à
l'onboarding, puis validé humainement avant ingestion.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULTS = {
    # Marqueurs de version dans les noms de fichier (regex, insensibles casse/accents,
    # appliqués sur texte "asciifié"). Détectent une version chain explicite.
    "version_markers": [
        r"\bv\s?\d+\b", r"\bversion\s*\d*\b", r"\bvf\b", r"\bdef(initif|initive)?\b",
        r"\bfinal(e|ise|isee)?\b", r"\bprojet\b", r"\bbrouillon\b", r"\bdraft\b",
        r"\brelu\b", r"\bcorrig(e|ee)\b", r"\bmodifi(e|ee)\b",
        r"\bsigne?e?\b", r"\bsigned\b",
    ],
    # Suffixes de copie ajoutés par les OS / le Drive (jamais porteurs de sens).
    "copy_markers": [r"\(\d+\)", r"\bcopie\b", r"\bcopy\b", r"[-_ ]+\d{1,2}$"],
    # Marqueurs de l'exemplaire qui FAIT FOI (prioritaire comme survivant).
    "authoritative_markers": [r"sign[ée]e?\b|signed\b", r"\bvf\b|\bdef(initif|initive)?\b|\bfinal(e|isee?)?\b"],
    # Suffixes de variante d'un même document (ex. version expurgée RGPD).
    # variant_keep : "original" = la variante est flaggée, l'original reste en
    # retrieval ; "variant" = l'inverse.
    "variant_suffixes": [],
    "variant_keep": "original",
    # Une famille de fichiers à racine commune plus grande que ce seuil est du
    # publipostage (1 exemplaire par copropriétaire) : JAMAIS une version chain.
    "publipostage_min_famille": 12,
    # Containment (recouvrement du plus court dans le plus long) minimal pour
    # conclure qu'un fichier est une version antérieure d'un autre.
    "containment_min": 0.95,
    # Longueur minimale de la racine de nom pour former une famille (anti-bruit).
    "stem_min_len": 8,
}


def load_rules(client_dir: str | Path | None = None) -> dict:
    """Règles effectives : DEFAULTS surchargés par clients/<client>/dedup_rules.json.

    `client_dir` explicite pour les tests ; sinon résolu depuis le profil client
    courant (PALIM_CLIENT) via pipeline_config.
    """
    rules = dict(DEFAULTS)
    if client_dir is None:
        try:
            import pipeline_config as pcfg
            client_dir = pcfg.CLIENTS_DIR / pcfg.PALIM_CLIENT
        except Exception:
            return rules
    f = Path(client_dir) / "dedup_rules.json"
    if f.exists():
        with open(f, encoding="utf-8") as fh:
            override = json.load(fh)
        inconnues = set(override) - set(DEFAULTS) - {"_comment"}
        if inconnues:
            raise ValueError(f"Clés inconnues dans {f}: {sorted(inconnues)}")
        rules.update({k: v for k, v in override.items() if k != "_comment"})
    return rules


def compile_rules(rules: dict) -> dict:
    """Pré-compile les regex du profil (une fois par run, pas par fichier)."""
    rx = lambda pats: re.compile("|".join(pats), re.I) if pats else None
    return {
        **rules,
        "rx_version": rx(rules["version_markers"]),
        "rx_copy": rx(rules["copy_markers"]),
        "rx_authoritative": [re.compile(p, re.I) for p in rules["authoritative_markers"]],
        "rx_variant": rx([re.escape(s) for s in rules["variant_suffixes"]]),
    }
