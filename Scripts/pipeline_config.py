"""Configuration commune au pipeline d'ingestion per-copro — multi-client.

Source de vérité unique pour :
- Le profil client courant (chargé depuis `clients/<client>.json`, sélectionné
  par la variable d'env PALIM_CLIENT, défaut "ncg")
- Map code copro -> nom de dossier dans `Données brutes/`
- Helpers de calcul de paths per-copro (filtré, extrait, JSONLs intermédiaires)
- Paramètres DB du client (host/port/name/users), surchargés par l'env

Les scripts 01..05b acceptent un flag `--copro <code>` qui résout les paths
via ce module. Sans `--copro`, ils retombent sur les chemins historiques
(rétro-compatibilité avec l'ancien mode "tout d'un coup").

Multi-client : un clone du repo par client (dans le dossier mission du client),
un dossier `clients/<client>/` par client (client.json = profil ; docs/, tools/,
skills/ = tout ce qui est spécifique à ce client). `project_root` null dans le
profil = racine du clone (parent de Scripts/), donc aucun path absolu à éditer
en déclinant le produit. Aucun secret dans les profils : mots de passe via env
(pipeline) ou Secrets Manager (MCP).
"""
import json
import os
from pathlib import Path

from copro_id import canon, is_immatriculation  # identité copro (produit partagé)

_SCRIPTS_DIR = Path(__file__).resolve().parent
CLIENTS_DIR = _SCRIPTS_DIR / "clients"

PALIM_CLIENT = (os.environ.get("PALIM_CLIENT", "ncg").strip().lower() or "ncg")

_client_file = CLIENTS_DIR / PALIM_CLIENT / "client.json"
if not _client_file.exists():
    _known = sorted(p.parent.name for p in CLIENTS_DIR.glob("*/client.json"))
    raise SystemExit(
        f"❌ Profil client introuvable : {_client_file} (PALIM_CLIENT={PALIM_CLIENT}). "
        f"Profils connus : {_known}"
    )
with open(_client_file, encoding="utf-8") as _f:
    _cfg = json.load(_f)

CLIENT_CODE = _cfg["client_code"]
CLIENT_NAME = _cfg["client_name"]

# Racine projet : null dans le profil = racine du clone (parent de Scripts/).
PROJECT_ROOT = Path(_cfg["project_root"]) if _cfg.get("project_root") else _SCRIPTS_DIR.parent

# Sources documentaires : "raw_root" du profil permet de lire DIRECTEMENT un Drive
# partagé client (lecture seule, zéro recopie). Défaut : Données brutes/ du projet.
RAW_ROOT       = Path(_cfg["raw_root"]) if _cfg.get("raw_root") else PROJECT_ROOT / "Données brutes"
RESULTS_ROOT   = PROJECT_ROOT / "Résultats bruts"
FILTERED_ROOT  = RESULTS_ROOT / "Archives_Filtrees"
EXTRACTED_ROOT = RESULTS_ROOT / "Archives_Extraites"
PER_COPRO_ROOT = RESULTS_ROOT / "per_copro"

# Registre des copros du client. Deux formats acceptés dans le profil :
#   "code": "nom de dossier"                                    (legacy, NCG)
#   "code": {"folder": ..., "lobby_code": ..., "label": ...,
#            "immatriculation": ...}                            (riche, Delacour+)
# Clés stockées en forme canonique (copro_id.canon) ; `lobby_code` devient un
# ALIAS de résolution (les gestionnaires pensent encore en codes internes).
# `immatriculation` = attribut RNIC (AA0000000) : jamais une clé pour les clients
# à codes internes ; auto-dérivée de la clé quand celle-ci EST une immatriculation
# (régime RNIC, Delacour+). Liste explicite : tout ce qui n'est pas listé dans le
# profil client est ignoré.
_COPROS = {}
_ALIASES = {}
for _k, _v in (_cfg.get("included_copros") or {}).items():
    _ck = canon(_k)
    _meta = {"folder": _v} if isinstance(_v, str) else dict(_v)
    _im = canon(_meta.get("immatriculation") or "")
    if _im and not is_immatriculation(_im):
        raise SystemExit(
            f"❌ Immatriculation RNIC invalide pour la copro {_ck} : "
            f"{_meta.get('immatriculation')!r} (format attendu AA0000000)."
        )
    if not _im and is_immatriculation(_ck):
        _im = _ck  # régime RNIC : la clé du registre est l'immatriculation
    _meta["immatriculation"] = _im or None
    _COPROS[_ck] = _meta
    if _meta.get("lobby_code"):
        _ALIASES[canon(_meta["lobby_code"])] = _ck

INCLUDED_COPROS = {c: m["folder"] for c, m in _COPROS.items()}  # compat historique
COPRO_META = _COPROS


def resolve(code: str) -> str:
    """Code canonique depuis toute graphie (tirets/espaces/casse) ou alias (code Lobby).

    Tout point d'entrée par code (--copro, stamping DB, paths) DOIT passer ici :
    c'est ce qui garantit qu'une même copro n'a qu'un seul shard et qu'un seul
    code_ncg en base, quelle que soit la façon dont l'humain a tapé le code.
    """
    c = canon(code)
    if c in _COPROS:
        return c
    if c in _ALIASES:
        return _ALIASES[c]
    raise ValueError(f"Code copro inconnu ou exclu : {code}. Codes valides : {sorted(_COPROS)}")

# ── DB du client (précédence : env > profil client) ──
# Le mot de passe n'est JAMAIS ici : DB_PASSWORD en env pour le pipeline.
_db = _cfg.get("db") or {}
DB_HOST = os.environ.get("DB_HOST") or _db.get("host", "")
DB_PORT = int(os.environ.get("DB_PORT") or _db.get("port", 5432))
DB_NAME = os.environ.get("DB_NAME") or _db.get("name", "postgres")
DB_USER_ADMIN = os.environ.get("DB_USER") or _db.get("user_admin", "ragadmin")
DB_USER_READER = _db.get("user_reader", "")
DB_SECRET_READER = _db.get("secret_reader", "")
DB_SECRET_ADMIN = _db.get("secret_admin", "")

# ── Assynco (base Airtable du courtier, multi-syndic donc multi-client) ──
_assynco = _cfg.get("assynco") or {}
ASSYNCO_ENABLED = bool(_assynco.get("enabled", False))
ASSYNCO_SYNDIC_LABELS = [s.strip() for s in (_assynco.get("syndic_labels") or []) if s and s.strip()]


def require_db_host() -> str:
    """DB host du client, ou arrêt net si non provisionné (jamais de fallback
    silencieux vers la DB d'un autre client)."""
    if not DB_HOST:
        raise SystemExit(
            f"❌ Aucun host DB pour le client '{CLIENT_CODE}'. Renseigner db.host dans "
            f"clients/{CLIENT_CODE}.json (ou exporter DB_HOST)."
        )
    return DB_HOST


def folder_for(code: str) -> str:
    return _COPROS[resolve(code)]["folder"]


def immatriculation_of(code: str):
    """Immatriculation RNIC (canonique) de la copro, ou None si non renseignée.
    Attribut d'identité : sert au recoupement (Airtable, registre national),
    jamais de clé interne pour les clients à codes courts."""
    return _COPROS[resolve(code)].get("immatriculation")


def raw_source_dir(code: str) -> Path:
    return RAW_ROOT / folder_for(code)


def filtered_dir(code: str) -> Path:
    return FILTERED_ROOT / folder_for(code)


def extracted_dir(code: str) -> Path:
    return EXTRACTED_ROOT / folder_for(code)


def per_copro_dir(code: str) -> Path:
    """Dossier de staging per-copro : rapports, checkpoints, JSONLs intermédiaires."""
    return PER_COPRO_ROOT / resolve(code)


def paths_for(code: str) -> dict:
    """Bundle de tous les paths pour un code donné (résolu en canonique).
    Ne crée pas les dossiers."""
    code = resolve(code)
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
