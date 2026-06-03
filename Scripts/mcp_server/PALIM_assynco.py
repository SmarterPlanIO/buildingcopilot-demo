"""
PALIM_assynco.py — Accès lecture à l'ERP Assynco (Airtable) pour le MCP PALIM.

R1 = read-only sur 3 tables : Copropriétés (hub), Police, Sinistre.
cf. PLAN_ACTION_MCP_ASSYNCO.md et skills/assynco-erp/references/data-model.md.

Modèle de scope : la table Copropriétés est le HUB. On résout un code NCG en
record Copropriété (FIND sur le champ Nom), puis on suit ses liens Polices /
Sinistres (record IDs) pour récupérer les enregistrements exacts. Les entités
liées (assureur, syndic, courtier, expert, gestionnaire) sont résolues en NOMS
via une passe batch sur Organisation / Contacts.

Le PAT vient de Secrets Manager (AIRTABLE_PAT_SECRET_ARN) en prod ; AIRTABLE_PAT
en env reste un fallback de dev. Le PAT n'est jamais loggé.

Fonctions publiques (les exceptions remontent au tool appelant qui les wrappe
en réponse MCP structurée) :
  - get_copro(code_ncg)                       -> dict | None
  - list_polices(code_ncg, max_records)       -> list[dict]
  - search_sinistres(code_ncg, query, max_records) -> list[dict]
"""
import json
import urllib.parse
import urllib.request

import PALIM_config as cfg

_AT_API = "https://api.airtable.com/v0"
_pat_cache = None


# ──────────────────────────────────────────────────────────────
# Secret PAT (Secrets Manager, mis en cache — jamais loggé)
# ──────────────────────────────────────────────────────────────
def _get_pat():
    global _pat_cache
    if _pat_cache is not None:
        return _pat_cache
    if cfg.AIRTABLE_PAT_SECRET_ARN:
        import boto3  # local : évite l'import si pas de Secrets Manager
        sm = boto3.client("secretsmanager", region_name=cfg.AWS_REGION_SECRETS)
        raw = sm.get_secret_value(SecretId=cfg.AIRTABLE_PAT_SECRET_ARN)["SecretString"]
        try:
            data = json.loads(raw)
            _pat_cache = data.get("AIRTABLE_PAT") or data.get("pat") or data.get("password") or ""
        except (json.JSONDecodeError, TypeError):
            _pat_cache = raw
    else:
        _pat_cache = cfg.AIRTABLE_PAT  # fallback dev
    if not _pat_cache:
        raise RuntimeError("AIRTABLE_PAT indisponible (ni Secrets Manager ni env).")
    return _pat_cache


# ──────────────────────────────────────────────────────────────
# Client Airtable read générique
# ──────────────────────────────────────────────────────────────
def _airtable_list(table_id, formula=None, fields=None, max_records=None):
    """GET paginé sur une table. Retourne une liste de records bruts {id, fields}.

    formula : filterByFormula (str) ou None.
    fields  : liste de champs à récupérer (réduit la charge) ou None (tous).
    max_records : plafond global (cappé par ASSYNCO_MAX_RECORDS_CAP).
    """
    cap = cfg.ASSYNCO_MAX_RECORDS_CAP
    limit = cap if max_records is None else max(1, min(int(max_records), cap))
    pat = _get_pat()
    out, offset = [], None
    while len(out) < limit:
        params = [("pageSize", str(min(100, limit - len(out))))]
        if formula:
            params.append(("filterByFormula", formula))
        for f in (fields or []):
            params.append(("fields[]", f))
        if offset:
            params.append(("offset", offset))
        url = f"{_AT_API}/{cfg.ASSYNCO_BASE_ID}/{table_id}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {pat}"})
        with urllib.request.urlopen(req, timeout=cfg.ASSYNCO_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return out[:limit]


def _by_record_ids(table_id, record_ids, fields=None):
    """Récupère des records par liste d'IDs via OR(RECORD_ID()=...). Une requête.

    Borne la formule (limite de longueur Airtable) au cap serveur.
    """
    ids = [r for r in (record_ids or []) if r][: cfg.ASSYNCO_MAX_RECORDS_CAP]
    if not ids:
        return []
    clause = "OR(" + ",".join(f"RECORD_ID()='{rid}'" for rid in ids) + ")"
    return _airtable_list(table_id, formula=clause, fields=fields, max_records=len(ids))


# ──────────────────────────────────────────────────────────────
# Helpers de projection
# ──────────────────────────────────────────────────────────────
def _first(v):
    """Déballe un champ Airtable qui peut être scalaire ou liste à 1 élément."""
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _ids(v):
    """Liste de record IDs d'un champ linked (toujours une liste)."""
    return [x for x in v if x] if isinstance(v, list) else ([v] if v else [])


def _resolve_names(records, link_fields):
    """Résout les champs linked (record IDs) en NOMS.

    records      : liste de records bruts {id, fields}.
    link_fields  : {nom_de_champ: table_id} à résoudre via le champ primaire.
    Construit une table {record_id: nom} en une requête par table cible, puis
    retourne un dict {field_name: {record_id: nom}} (best-effort : si échec, vide).
    """
    wanted = {}  # table_id -> set(record_ids)
    for rec in records:
        f = rec.get("fields", {})
        for fld, tid in link_fields.items():
            for rid in _ids(f.get(fld)):
                wanted.setdefault(tid, set()).add(rid)
    id_to_name = {}  # record_id -> nom (toutes tables confondues : IDs uniques)
    for tid, idset in wanted.items():
        try:
            for r in _by_record_ids(tid, list(idset), fields=None):
                rf = r.get("fields", {})
                # champ primaire ≈ 'Name' (Organisation/Contacts ont PK 'Name')
                nm = rf.get("Name") or rf.get("Nom") or rf.get("Référence") or r["id"]
                id_to_name[r["id"]] = nm
        except Exception:
            pass  # best-effort : on garde les IDs non résolus
    return id_to_name


def _names_of(fields, key, id_to_name):
    """Noms résolus d'un champ linked (liste de str)."""
    return [id_to_name.get(rid, rid) for rid in _ids(fields.get(key))]


# Champs récupérés par table (curés — cf. data-model.md)
_COPRO_FIELDS = [
    "Nom", "Ref client", "Numéro d'immatriculation", "Type de Syndicat",
    "Nombre de copropriétaires", "Nombre de sinistre", "Adresse de référence",
    "Code Postal", "Ville", "Année de construction", "Surface m2", "Bâtiments",
    "Ascenseurs", "Chauffage", "TOTAL Prime", "TOTAL Sinistres", "Prime MRI",
    "Montant du fonds de travaux", "Polices", "Sinistres", "Syndic", "Gestionnaire",
]
_POLICE_FIELDS = [
    "Numéro de Police", "Statut Contrat", "Type souscripteur", "Garantie", "Garanties",
    "Franchise", "Franchise Générale", "Franchise DDE", "Prime Annuelle TTC Souscription",
    "Prime Annuelle HT hors frais", "LAST Prime Quittance TTC", "Date Effet Police",
    "Date de Résiliation", "Echéance Annuelle", "Fractionnement", "Adresse du Risque",
    "Assureur", "Syndic", "Courtier", "Type Contrat Assurance",
]
_SINISTRE_FIELDS = [
    "Name", "Situation Dossier", "Statut details", "Survenance", "Ouverture", "Clôture",
    "Nom du Lésé", "Cause", "Garantie Impactée", "Franchise", "Plafond",
    "Estimation", "Coût Assureur", "Provisions Assureur", "💸 Total Réglé",
    "🚦Déclaration", "🚦Expertise", "🚦 Accord", "🚦Règlement",
    "Ref Cie", "Ref Expert", "Ref Sinistre Client", "Conclusion de l'expert",
    "Assureur", "Expert",
]

_POLICE_LINKS = {"Assureur": cfg.ASSYNCO_TABLE_ORG, "Syndic": cfg.ASSYNCO_TABLE_ORG,
                 "Courtier": cfg.ASSYNCO_TABLE_ORG,
                 "Type Contrat Assurance": cfg.ASSYNCO_TABLE_PRODUIT}
_SINISTRE_LINKS = {"Assureur": cfg.ASSYNCO_TABLE_ORG, "Expert": cfg.ASSYNCO_TABLE_ORG}


def _project_copro(rec):
    f = rec.get("fields", {})
    return {
        "airtable_record_id": rec["id"],
        "nom": f.get("Nom"),
        "ref_client": f.get("Ref client"),
        "immatriculation": f.get("Numéro d'immatriculation"),
        "type_syndicat": f.get("Type de Syndicat"),
        "nb_coproprietaires": f.get("Nombre de copropriétaires"),
        "nb_sinistres": f.get("Nombre de sinistre"),
        "adresse": f.get("Adresse de référence"),
        "code_postal": f.get("Code Postal"),
        "ville": f.get("Ville"),
        "annee_construction": f.get("Année de construction"),
        "surface_m2": f.get("Surface m2"),
        "batiments": f.get("Bâtiments"),
        "ascenseurs": f.get("Ascenseurs"),
        "chauffage": f.get("Chauffage"),
        "total_prime": _first(f.get("TOTAL Prime")),
        "total_sinistres": _first(f.get("TOTAL Sinistres")),
        "prime_mri": _first(f.get("Prime MRI")),
        "fonds_travaux": f.get("Montant du fonds de travaux"),
        "nb_polices_liees": len(_ids(f.get("Polices"))),
    }


def _project_police(rec, id_to_name):
    f = rec.get("fields", {})
    return {
        "airtable_record_id": rec["id"],
        "numero_police": f.get("Numéro de Police"),
        "statut_contrat": f.get("Statut Contrat"),
        "type_souscripteur": f.get("Type souscripteur"),
        "type_contrat": _names_of(f, "Type Contrat Assurance", id_to_name) or None,
        "garanties": f.get("Garanties") or f.get("Garantie"),
        "franchise": f.get("Franchise"),
        "franchise_generale": f.get("Franchise Générale"),
        "franchise_dde": f.get("Franchise DDE"),
        "prime_annuelle_ttc": f.get("Prime Annuelle TTC Souscription"),
        "prime_annuelle_ht": f.get("Prime Annuelle HT hors frais"),
        "derniere_prime_quittance_ttc": _first(f.get("LAST Prime Quittance TTC")),
        "date_effet": f.get("Date Effet Police"),
        "date_resiliation": f.get("Date de Résiliation"),
        "echeance_annuelle": f.get("Echéance Annuelle"),
        "fractionnement": f.get("Fractionnement"),
        "adresse_risque": f.get("Adresse du Risque"),
        "assureur": _names_of(f, "Assureur", id_to_name),
        "syndic": _names_of(f, "Syndic", id_to_name),
        "courtier": _names_of(f, "Courtier", id_to_name),
    }


def _project_sinistre(rec, id_to_name):
    f = rec.get("fields", {})
    return {
        "airtable_record_id": rec["id"],
        "nom": f.get("Name"),
        "situation": f.get("Situation Dossier") or f.get("Statut details"),
        "date_survenance": f.get("Survenance") or f.get("Ouverture"),
        "date_cloture": f.get("Clôture"),
        "lese_nom": f.get("Nom du Lésé"),
        "cause": f.get("Cause"),
        "garantie_impactee": f.get("Garantie Impactée"),
        "franchise": f.get("Franchise"),
        "plafond": f.get("Plafond"),
        "estimation": f.get("Estimation"),
        "cout_assureur": f.get("Coût Assureur"),
        "provisions": f.get("Provisions Assureur"),
        "total_regle": f.get("💸 Total Réglé"),
        "pipeline": {
            "declaration": f.get("🚦Déclaration"), "expertise": f.get("🚦Expertise"),
            "accord": f.get("🚦 Accord"), "reglement": f.get("🚦Règlement"),
        },
        "ref_cie": f.get("Ref Cie"),
        "ref_expert": f.get("Ref Expert"),
        "ref_sinistre_client": f.get("Ref Sinistre Client"),
        "conclusion_expert": f.get("Conclusion de l'expert"),
        "assureur": _names_of(f, "Assureur", id_to_name),
        "expert": _names_of(f, "Expert", id_to_name),
    }


# ──────────────────────────────────────────────────────────────
# Résolution copro (hub)
# ──────────────────────────────────────────────────────────────
def _get_copro_record(code_ncg):
    """Record Copropriété brut pour un code NCG, ou None.

    Le code NCG vit dans le champ `Ref client` de la table Copropriétés (le `Nom`
    est l'adresse/nom d'immeuble). `code_ncg` est validé numérique → pas d'injection.
    NB : quelques codes matchent >1 record (ex. 5548, doublon Assynco) ; on retient
    le premier.
    """
    code = str(code_ncg).strip()
    if not code.isdigit():
        return None
    recs = _airtable_list(cfg.ASSYNCO_TABLE_COPRO,
                          formula='{Ref client}="' + code + '"',
                          fields=_COPRO_FIELDS, max_records=1)
    return recs[0] if recs else None


# ──────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────
def get_copro(code_ncg):
    """Identité + synthèse assurance d'une copropriété. None si introuvable."""
    rec = _get_copro_record(code_ncg)
    if not rec:
        return None
    out = _project_copro(rec)
    out["code_ncg"] = str(code_ncg).strip()
    return out


def list_polices(code_ncg, max_records=None):
    """Polices d'assurance souscrites d'une copro (via les liens du record copro)."""
    rec = _get_copro_record(code_ncg)
    if not rec:
        return []
    police_ids = _ids(rec.get("fields", {}).get("Polices"))
    if not police_ids:
        return []
    raw = _by_record_ids(cfg.ASSYNCO_TABLE_POLICE, police_ids, fields=_POLICE_FIELDS)
    id_to_name = _resolve_names(raw, _POLICE_LINKS)
    return [_project_police(r, id_to_name) for r in raw[: (max_records or cfg.ASSYNCO_MAX_RECORDS_CAP)]]


def search_sinistres(code_ncg, query=None, max_records=None):
    """Sinistres d'une copro (live), via les liens du record copro.

    query optionnel : filtre texte côté serveur (insensible casse) sur le Name.
    """
    rec = _get_copro_record(code_ncg)
    if not rec:
        return []
    sin_ids = _ids(rec.get("fields", {}).get("Sinistres"))
    if not sin_ids:
        return []
    raw = _by_record_ids(cfg.ASSYNCO_TABLE_SINISTRE, sin_ids, fields=_SINISTRE_FIELDS)
    q = (query or "").strip().lower()
    if q:
        raw = [r for r in raw if q in str(r.get("fields", {}).get("Name", "")).lower()]
    id_to_name = _resolve_names(raw, _SINISTRE_LINKS)
    return [_project_sinistre(r, id_to_name) for r in raw[: (max_records or cfg.ASSYNCO_MAX_RECORDS_CAP)]]
