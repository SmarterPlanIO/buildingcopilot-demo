"""Configuration commune au pipeline d'ingestion per-copro.

Source de vérité unique pour :
- Map code NCG -> nom de dossier dans `Données brutes/`
- Helpers de calcul de paths per-copro (filtré, extrait, JSONLs intermédiaires)

Les scripts 01..05b acceptent un flag `--copro <code>` qui résout les paths
via ce module. Sans `--copro`, ils retombent sur les chemins historiques
(rétro-compatibilité avec l'ancien mode "tout d'un coup").
"""
from pathlib import Path

PROJECT_ROOT = Path(r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne")

RAW_ROOT       = PROJECT_ROOT / "Données brutes"
RESULTS_ROOT   = PROJECT_ROOT / "Résultats bruts"
FILTERED_ROOT  = RESULTS_ROOT / "Archives_Filtrees"
EXTRACTED_ROOT = RESULTS_ROOT / "Archives_Extraites"
PER_COPRO_ROOT = RESULTS_ROOT / "per_copro"

# Map code NCG -> nom de dossier dans Données brutes/
# Liste explicite : tout ce qui n'est pas listé ici est ignoré.
# 5412 TOUR LYON BERCY exclu (volume trop important).
INCLUDED_COPROS = {
    "5033": "5033 - 24 TORCY",
    "5354": "5354 - 2 UNIVERSITE",
    "5390": "5390 - 2-6 BIS HENRI TARIEL",
    "5427": "5427 - 33 VICTOR CRESSON",
    "5480": "5480 - 88-90 GR GAL EBOUE",
    "5499": "5499 - 22-24 GUILLEMIN",
    "5548": "5548 - VILLA HAUSSMANN",
    "5553": "5553 - 8 Jaurès - LES FREGATES",
    "8030": "8030 - 21 PATAY",
    "8050": "8050 - STYLE - 145 AVENUE DE FRANCE",
}


def folder_for(code: str) -> str:
    if code not in INCLUDED_COPROS:
        raise ValueError(f"Code copro inconnu ou exclu : {code}. Codes valides : {sorted(INCLUDED_COPROS)}")
    return INCLUDED_COPROS[code]


def raw_source_dir(code: str) -> Path:
    return RAW_ROOT / folder_for(code)


def filtered_dir(code: str) -> Path:
    return FILTERED_ROOT / folder_for(code)


def extracted_dir(code: str) -> Path:
    return EXTRACTED_ROOT / folder_for(code)


def per_copro_dir(code: str) -> Path:
    """Dossier de staging per-copro : rapports, checkpoints, JSONLs intermédiaires."""
    return PER_COPRO_ROOT / code


def paths_for(code: str) -> dict:
    """Bundle de tous les paths pour un code donné. Ne crée pas les dossiers."""
    pcd = per_copro_dir(code)
    return {
        "code": code,
        "folder_name": folder_for(code),
        "raw_source": raw_source_dir(code),
        "filtered": filtered_dir(code),
        "extracted": extracted_dir(code),
        "per_copro": pcd,
        "filtrage_report": pcd / "filtrage_rapport.json",
        "extraction_checkpoint": pcd / "extraction_checkpoint.json",
        "extraction_log": pcd / "extraction.log",
        "chunks_jsonl": pcd / "chunks.jsonl",
        "documents_metadata_jsonl": pcd / "documents_metadata.jsonl",
        "dossiers_jsonl": pcd / "dossiers.jsonl",
        "embeddings_jsonl": pcd / "chunks_avec_embeddings.jsonl",
        "embeddings_sq_jsonl": pcd / "chunks_avec_embeddings_sq.jsonl",
    }
