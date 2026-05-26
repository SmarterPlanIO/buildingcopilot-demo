"""
ÉTAPE 8 — Synchronisation Airtable Assynco → PostgreSQL dossiers
Lance : python 08_airtable_sync.py

Lit les sinistres depuis la base Airtable d'Assynco (courtier assurance),
et synchronise avec la table dossiers de PALIM.

Mode : UPSERT (insert si nouveau, update si existant)
Clé de matching : airtable_record_id (ID unique Airtable)
"""

import json
import os
import re
import unicodedata
import urllib.request
import urllib.parse
import psycopg2
import psycopg2.extras
from datetime import datetime, date

# =====================================================
# CONFIGURATION
# =====================================================
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appi1ee5p93EBHtLR")
AIRTABLE_TABLE_ID = os.environ.get("AIRTABLE_TABLE_ID", "tblvvkhcHZjDyHLdp")  # 🏄‍♂️Sinistre

# Filtres par copropriété : match précis sur le code NCG parenthésé dans {Name}.
# Le champ {Name} suit le format canonique "DDE-... @ NOM(CODE) Nos Ref:..." et
# 920/922 sinistres (100%) respectent ce format (vérifié via diag_copro_filters.py).
# On filtre donc sur FIND("(CODE)") : zéro faux positif. Les alias mot-clé (rue/résidence)
# sont volontairement ABSENTS car ils rattrapent des sinistres d'AUTRES immeubles partageant
# le nom de rue (ex: alias TARIEL → TARIEL(5448)/(5443) ; CRESSON → 5 autres codes ; PATAY → 8031/8032).
# Clé = nom copro interne, valeur = (formule Airtable, code_ncg)
COPRO_FILTERS = {
    "5033_TORCY":         ('FIND("(5033)",{Name})', "5033"),
    "5354_UNIVERSITE":    ('FIND("(5354)",{Name})', "5354"),
    "5390_TARIEL":        ('FIND("(5390)",{Name})', "5390"),
    "5427_CRESSON":       ('FIND("(5427)",{Name})', "5427"),
    "5480_LE_STADE":      ('FIND("(5480)",{Name})', "5480"),
    "5499_GUILLEMIN":     ('FIND("(5499)",{Name})', "5499"),
    "5548_HOCHE_MESSINE": ('FIND("(5548)",{Name})', "5548"),
    "5553_FREGATES":      ('FIND("(5553)",{Name})', "5553"),
    "8030_PATAY":         ('FIND("(8030)",{Name})', "8030"),
    "8050_STYLE":         ('FIND("(8050)",{Name})', "8050"),
}

DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "ragadmin")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# Champs Airtable à récupérer (évite de tout télécharger)
AIRTABLE_FIELDS = [
    "Name", "Situation Dossier", "Survenance", "Ouverture", "Clôture",
    "Nom du Lésé", "Tel Lésé", "Email Lésé", "Appt d'origine",
    "Garantie Impactée", "Cause", "IRSI", "Cause DDE Identifiée", "Cause DDE Réparée",
    "Ref Cie", "Ref Expert", "Ref Sinistre Client",
    "Coût Assureur", "Estimation", "Franchise", "Provisions Assureur",
    "Règlement Frais", "Règlement Réalisé", "Recours En Cours", "Recours Réalisé",
    "Coût Client", "💸Honoraire de Syndic", "💸 Dommages",
    "💸 immédiate", "💸 Différée", "💸 Total Réglé",
    "Circonstances", "Dommages", "Conclusion de l'expert",
    "Commentaire Assureur", "Commentaire Assynco",
    "Observations Déclaration", "Motif Rappel",
    "Commentaire relance expert", "Commentaire relance compagnie", "Commentaire relance client",
    "Element Manquant", "Attente",
    "🚦Déclaration", "🚦Expertise", "🚦 Accord", "🚦Règlement", "🚦 Etat - Mise en Cause",
    "Important", "🚨 Judiciaire", "En carence",
    "💩Situation Sinistré", "Dommage Copro",
    "Date de déclaration", "Mission Expert", "Invitation Expertise",
    "Premiere Visite", "PV", "Lettre d'acceptation", "Depot Rapport",
    "Date du Reglement Principal", "Dernière relance gestionnaire",
    "Relance Expert", "Relance Compagnie", "Relance Client",
    "Rappel", "Prescription April",
    "Short Adresse",
    # === NEW FIELDS (audit Mars 2026) ===
    "Statut details", "Triage", "Nom Gestionnaire syndic",
    "Prescription estimate", "Client URL", "Etat Expert",
    "Adresse Copro", "A relancer", "🛠Prescription Status",
    "Dossier Inch",
    "Email Gestionnaire", "Tel Syndic", "Contact Gestionnaire",
    "Email Gestionnaire Sinistre", "Adresse Syndic",
]


def fetch_airtable_records(filter_formula, offset=None):
    """Fetch records from Airtable with pagination."""
    params = {"filterByFormula": filter_formula, "pageSize": 100}
    for field in AIRTABLE_FIELDS:
        params[f"fields[]"] = field  # Will be overridden — use list below

    # Build URL with proper field encoding
    base_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE_ID}"
    field_params = "&".join(f"fields[]={urllib.parse.quote(f)}" for f in AIRTABLE_FIELDS)
    formula_param = f"filterByFormula={urllib.parse.quote(filter_formula)}"
    url = f"{base_url}?{formula_param}&pageSize=100&{field_params}"
    if offset:
        url += f"&offset={offset}"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {AIRTABLE_PAT}"})
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())


def _first_or_none(val):
    """Extract first element from Airtable lookup lists, or return string directly."""
    if val is None:
        return None
    if isinstance(val, list):
        return val[0] if val else None
    return str(val)


def map_airtable_to_dossier(record, copropriete, default_code_ncg=None):
    """Map an Airtable record to our dossiers table schema."""
    f = record["fields"]

    # Map situation → statut PALIM
    situation = f.get("Situation Dossier", "")
    statut_map = {
        "En cours": "EN_COURS",
        "Attente expert": "EN_ATTENTE",
        "Expertise confirmée": "EN_COURS",
        "Réouverture": "EN_COURS",
        "En attente interv.": "EN_ATTENTE",
        "En attente client": "EN_ATTENTE",
        "En attente cie": "EN_ATTENTE",
        "Clos": "CLOTURE",
        "Sans Suite": "CLOTURE",
        "Rejeté": "CLOTURE",
        "ND": "EN_ATTENTE",
    }
    statut = statut_map.get(situation, "EN_ATTENTE")

    # Build type_dossier from garantie
    garanties = f.get("Garantie Impactée", [])
    if garanties:
        # Take first garantie code (before " - ")
        code = garanties[0].split(" - ")[0].strip()
        type_dossier = f"SINISTRE_{code}"
    else:
        type_dossier = "SINISTRE_DDE"

    # Helper for safe date parsing
    def parse_date(val):
        if not val:
            return None
        try:
            return val[:10]  # ISO format YYYY-MM-DD
        except Exception:
            return None

    def parse_bool(val):
        if val is None:
            return None
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("oui", "yes", "true", "1")

    def parse_numeric(val):
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    # Extract code_ncg from Airtable Name field: "DDE-... @ ... TIVOLI(5390)" → "5390"
    # Fallback sur le code_ncg de la copro si pas trouvé dans le Name
    _name = f.get("Name", "")
    _code_ncg_match = re.search(r'\((\d{4,6})\)', _name)
    _code_ncg = _code_ncg_match.group(1) if _code_ncg_match else default_code_ncg

    # Extract ref_assynco from Name: "... Nos Ref: A1910058 ..."
    _ref_assynco_match = re.search(r'Nos\s+Ref[:\s]+([A-Z]\d+)', _name, re.IGNORECASE)
    _ref_assynco = _ref_assynco_match.group(1) if _ref_assynco_match else (_name[:30] if _name else None)

    # Extract ref_cie from Name as fallback: "... RefCie: I191340NC ..."
    _ref_cie_match = re.search(r'RefCie:\s*([A-Z0-9]+)', _name, re.IGNORECASE)
    _ref_cie_from_name = _ref_cie_match.group(1) if _ref_cie_match else None

    return {
        "airtable_record_id": record["id"],
        "copropriete": copropriete,
        "code_ncg": _code_ncg,
        "type_dossier": type_dossier,
        "nom_dossier": f.get("Name", "Sans nom"),
        "statut": statut,
        "date_ouverture": parse_date(f.get("Survenance")) or parse_date(f.get("Ouverture")),
        "date_cloture": parse_date(f.get("Clôture")),
        "lese_nom": f.get("Nom du Lésé"),
        "lese_tel": f.get("Tel Lésé"),
        "lese_email": f.get("Email Lésé"),
        "appt_origine": f.get("Appt d'origine"),
        # References
        "ref_cie": f.get("Ref Cie") or _ref_cie_from_name,
        "ref_expert": f.get("Ref Expert"),
        "ref_sinistre_client": f.get("Ref Sinistre Client"),
        "ref_assynco": _ref_assynco,
        # Pipeline 🚦
        "at_declaration": f.get("🚦Déclaration"),
        "at_expertise": f.get("🚦Expertise"),
        "at_accord": f.get("🚦 Accord"),
        "at_reglement": f.get("🚦Règlement"),
        "at_mise_en_cause": f.get("🚦 Etat - Mise en Cause"),
        "at_situation": situation,
        "at_attente": f.get("Attente"),
        # Cause & IRSI
        "cause": f.get("Cause"),
        "irsi": parse_bool(f.get("IRSI")),
        "cause_identifiee": parse_bool(f.get("Cause DDE Identifiée")),
        "cause_reparee": parse_bool(f.get("Cause DDE Réparée")),
        "garantie_impactee": garanties or None,
        # Financier
        "montant_estime": parse_numeric(f.get("Estimation")),
        "montant_reel": parse_numeric(f.get("Coût Assureur")),
        "franchise": parse_numeric(f.get("Franchise")),
        "provisions": parse_numeric(f.get("Provisions Assureur")),
        "reglement_realise": parse_numeric(f.get("Règlement Réalisé")),
        "reglement_frais": parse_numeric(f.get("Règlement Frais")),
        "recours_en_cours": parse_numeric(f.get("Recours En Cours")),
        "recours_realise": parse_numeric(f.get("Recours Réalisé")),
        "cout_client": parse_numeric(f.get("Coût Client")),
        "honoraire_syndic": parse_numeric(f.get("💸Honoraire de Syndic")),
        "dommages": parse_numeric(f.get("💸 Dommages")),
        "indemnite_immediate": parse_numeric(f.get("💸 immédiate")),
        "indemnite_differee": parse_numeric(f.get("💸 Différée")),
        "total_regle": parse_numeric(f.get("💸 Total Réglé")),
        # Dates
        "date_declaration": parse_date(f.get("Date de déclaration")),
        "date_mission_expert": parse_date(f.get("Mission Expert")),
        "date_invitation_expertise": f.get("Invitation Expertise"),
        "date_premiere_visite": parse_date(f.get("Premiere Visite")),
        "date_pv": parse_date(f.get("PV")),
        "date_lettre_acceptation": parse_date(f.get("Lettre d'acceptation")),
        "date_depot_rapport": parse_date(f.get("Depot Rapport")),
        "date_reglement": parse_date(f.get("Date du Reglement Principal")),
        "date_derniere_relance": parse_date(f.get("Dernière relance gestionnaire")),
        "date_relance_expert": parse_date(f.get("Relance Expert")),
        "date_relance_compagnie": parse_date(f.get("Relance Compagnie")),
        "date_relance_client": parse_date(f.get("Relance Client")),
        "date_rappel": parse_date(f.get("Rappel")),
        "date_prescription": parse_date(f.get("Prescription April")),
        # Textes
        "circonstances": f.get("Circonstances"),
        "dommages_description": f.get("Dommages"),
        "conclusion_expert": f.get("Conclusion de l'expert"),
        "commentaire_assureur": f.get("Commentaire Assureur"),
        "commentaire_assynco": f.get("Commentaire Assynco"),
        "observations_declaration": f.get("Observations Déclaration"),
        "motif_rappel": f.get("Motif Rappel"),
        "commentaire_relance_expert": f.get("Commentaire relance expert"),
        "commentaire_relance_compagnie": f.get("Commentaire relance compagnie"),
        "commentaire_relance_client": f.get("Commentaire relance client"),
        # Flags
        "important": bool(f.get("Important")),
        "judiciaire": bool(f.get("🚨 Judiciaire")),
        "en_carence": bool(f.get("En carence")),
        # Selects
        "elements_manquants": f.get("Element Manquant") or None,
        "situation_sinistre": f.get("💩Situation Sinistré"),
        "dommage_copro": parse_bool(f.get("Dommage Copro")),
        # === NEW FIELDS (audit Mars 2026) ===
        # Haute priorité — suivi opérationnel
        "statut_detail": f.get("Statut details"),
        "triage": f.get("Triage"),
        "gestionnaire_syndic": _first_or_none(f.get("Nom Gestionnaire syndic")),
        "date_prescription_estimate": parse_date(f.get("Prescription estimate")),
        "airtable_url": f.get("Client URL"),
        "adresse_sinistre": f.get("Short Adresse"),
        "etat_expert": f.get("Etat Expert"),
        "adresse_copro": _first_or_none(f.get("Adresse Copro")),
        "a_relancer": f.get("A relancer"),
        "prescription_status": f.get("🛠Prescription Status"),
        "ref_inch": f.get("Dossier Inch"),
        # Moyenne priorité — contacts & coordination
        "email_gestionnaire": _first_or_none(f.get("Email Gestionnaire")),
        "tel_syndic": _first_or_none(f.get("Tel Syndic")),
        "tel_gestionnaire_sinistre": _first_or_none(f.get("Contact Gestionnaire")),
        "email_gestionnaire_sinistre": _first_or_none(f.get("Email Gestionnaire Sinistre")),
        "adresse_syndic": _first_or_none(f.get("Adresse Syndic")),
    }


def upsert_dossier(cur, dossier):
    """Insert or update a dossier in PostgreSQL.
    Uses DELETE + INSERT strategy for reliability (ON CONFLICT UPSERT had issues
    with new columns not being written on UPDATE)."""
    at_id = dossier["airtable_record_id"]
    dossier_id = f"AT_{at_id}"

    # Delete existing record if any
    cur.execute("DELETE FROM dossiers WHERE airtable_record_id = %s", [at_id])

    # Build clean INSERT with all fields
    dossier["dossier_id"] = dossier_id
    dossier["updated_at"] = datetime.now()
    if "created_at" not in dossier:
        dossier["created_at"] = datetime.now()

    columns = list(dossier.keys())
    values = list(dossier.values())
    placeholders = ", ".join(["%s"] * len(values))
    col_names = ", ".join(columns)

    sql = f"INSERT INTO dossiers ({col_names}) VALUES ({placeholders})"
    cur.execute(sql, values)


def _normalize_name(name):
    """Normalize a person name for fuzzy matching.
    'M. LEMEAU' / 'LEMEAU Yves' / 'DDE LEMEAU' → 'lemeau'
    """
    if not name:
        return ""
    # Remove accents
    s = unicodedata.normalize("NFD", name)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper()
    # Strip common prefixes: M., Mme, Mr, Syndicat..., DDE
    # Each prefix must be a standalone word (\b...\b) to avoid clipping real names
    s = re.sub(r'\bSYNDICAT\b.*', '', s)  # "Syndicat des Copropriétaires..." → ""
    s = re.sub(r'\b(MR|MME|MADAME|MONSIEUR|SINISTRE_DDE|DDE)\b', '', s)
    s = re.sub(r'\bM\b\.?', '', s)  # standalone "M" or "M."
    # Keep only alpha tokens
    tokens = re.findall(r'[A-Z]{2,}', s)
    return " ".join(sorted(tokens))


_MERGE_DATE_TOLERANCE_DAYS = 30  # max écart entre dates d'ouverture pour considérer un doublon


def _match_rag_dossier(cur, at_lese_nom, at_nom_dossier, at_date_ouverture, code_ncg):
    """Find a RAG-only dossier matching an Airtable sinistre by lese_nom + date proximity.

    Matching rules:
    1. Normalized lese_nom (or fallback nom_dossier) must share all tokens of the shorter name.
    2. If both dossiers have a date_ouverture, they must be within _MERGE_DATE_TOLERANCE_DAYS.

    Returns (dossier_id, lese_nom) of the best RAG match, or (None, None).
    """
    if not code_ncg:
        return None, None

    # Fetch all RAG dossiers for this copro
    cur.execute("""
        SELECT dossier_id, lese_nom, nom_dossier, date_ouverture
        FROM dossiers
        WHERE code_ncg = %s AND airtable_record_id IS NULL
    """, [code_ncg])
    rag_rows = cur.fetchall()
    if not rag_rows:
        return None, None

    # Build normalized Airtable name for matching
    at_norm = _normalize_name(at_lese_nom)
    if not at_norm:
        return None, None

    at_tokens = set(at_norm.split())

    # Normalize at_date_ouverture to a date object for comparison
    at_date = None
    if at_date_ouverture:
        if isinstance(at_date_ouverture, str):
            try:
                at_date = date.fromisoformat(at_date_ouverture[:10])
            except (ValueError, TypeError):
                pass
        elif isinstance(at_date_ouverture, date):
            at_date = at_date_ouverture

    candidates = []
    for rag_id, rag_lese, rag_nom, rag_date in rag_rows:
        # ── Name matching on lese_nom ──
        rag_lese_norm = _normalize_name(rag_lese)
        rag_nom_norm = _normalize_name(rag_nom)
        rag_norm = rag_lese_norm or rag_nom_norm
        if not rag_norm:
            continue
        rag_tokens = set(rag_norm.split())
        overlap = at_tokens & rag_tokens
        if not overlap:
            continue
        score = len(overlap) / min(len(at_tokens), len(rag_tokens))
        if score < 1.0:
            continue

        # ── Date proximity guard ──
        if at_date and rag_date:
            rag_d = rag_date if isinstance(rag_date, date) else None
            if rag_d:
                delta = abs((at_date - rag_d).days)
                if delta > _MERGE_DATE_TOLERANCE_DAYS:
                    continue  # same person, different sinistre

        # ── Bonus: lese_nom matched directly (not via nom_dossier fallback) ──
        lese_direct = 1 if rag_lese_norm and (at_tokens & set(rag_lese_norm.split())) else 0
        # ── Bonus: nom_dossier also contains the person name ──
        nom_also = 1 if rag_nom_norm and (at_tokens & set(rag_nom_norm.split())) else 0
        # ── Bonus: closer date ──
        date_closeness = 0
        if at_date and rag_date and isinstance(rag_date, date):
            date_closeness = max(0, _MERGE_DATE_TOLERANCE_DAYS - abs((at_date - rag_date).days))

        candidates.append((
            (lese_direct, nom_also, date_closeness, score),  # sort key
            rag_id, rag_lese
        ))

    if not candidates:
        return None, None

    # Pick the best candidate: prefer lese_nom match, then nom_dossier match, then closest date
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_id, best_name = candidates[0]
    return best_id, best_name


# Fields to absorb from RAG dossier into the Airtable dossier.
# Only copy when the Airtable dossier has NULL/empty for that field.
_RAG_MERGE_FIELDS = [
    "documents_lies", "resume_ia", "etapes",
    "pieces_requises", "pieces_fournies",
    "expert_nom", "assureur", "lese_lot",
]


def merge_rag_into_airtable(cur, code_ncg):
    """Post-sync pass: find RAG dossiers that duplicate an Airtable dossier,
    absorb their exclusive data, and delete the RAG duplicate.

    Returns (merged_count, details_list).
    """
    # All Airtable dossiers for this copro
    cur.execute("""
        SELECT dossier_id, lese_nom, nom_dossier, code_ncg, date_ouverture
        FROM dossiers
        WHERE code_ncg = %s AND airtable_record_id IS NOT NULL
    """, [code_ncg])
    at_dossiers = cur.fetchall()

    merged = []
    already_merged_rag_ids = set()  # prevent same RAG dossier from being absorbed twice
    for at_id, at_lese, at_nom, at_code, at_date in at_dossiers:
        rag_id, rag_lese = _match_rag_dossier(cur, at_lese, at_nom, at_date, at_code)
        if not rag_id or rag_id in already_merged_rag_ids:
            continue
        already_merged_rag_ids.add(rag_id)

        # Fetch the RAG dossier's mergeable fields
        field_list = ", ".join(_RAG_MERGE_FIELDS)
        cur.execute(f"SELECT {field_list} FROM dossiers WHERE dossier_id = %s", [rag_id])
        rag_row = cur.fetchone()
        if not rag_row:
            continue
        rag_data = dict(zip(_RAG_MERGE_FIELDS, rag_row))

        # Fetch current Airtable dossier's values for these fields
        cur.execute(f"SELECT {field_list} FROM dossiers WHERE dossier_id = %s", [at_id])
        at_row = cur.fetchone()
        at_data = dict(zip(_RAG_MERGE_FIELDS, at_row))

        # Build UPDATE SET for fields where AT is empty but RAG has data
        updates = {}
        for field in _RAG_MERGE_FIELDS:
            rag_val = rag_data[field]
            at_val = at_data[field]
            # "empty" = None, empty string, empty list/array, empty jsonb []
            at_empty = (at_val is None
                        or at_val == ""
                        or at_val == []
                        or (isinstance(at_val, list) and len(at_val) == 0)
                        or at_val == "[]")
            rag_has = (rag_val is not None
                       and rag_val != ""
                       and rag_val != []
                       and not (isinstance(rag_val, list) and len(rag_val) == 0)
                       and rag_val != "[]")
            if at_empty and rag_has:
                updates[field] = rag_val

        if updates:
            set_clauses = ", ".join(f"{k} = %s" for k in updates)
            # Serialize jsonb fields (etapes) for psycopg2
            vals = []
            for k in updates:
                v = updates[k]
                if k == "etapes" and not isinstance(v, str):
                    vals.append(psycopg2.extras.Json(v))
                else:
                    vals.append(v)
            vals.append(at_id)
            cur.execute(
                f"UPDATE dossiers SET {set_clauses}, updated_at = NOW() WHERE dossier_id = %s",
                vals
            )

        # Delete the RAG duplicate
        cur.execute("DELETE FROM dossiers WHERE dossier_id = %s", [rag_id])
        merged.append({
            "airtable": at_id,
            "rag_absorbed": rag_id,
            "fields_copied": list(updates.keys()),
            "at_lese": at_lese,
            "rag_lese": rag_lese,
        })

    return len(merged), merged


def main():
    print("=" * 60)
    print("SYNCHRONISATION AIRTABLE ASSYNCO → PALIM")
    print("=" * 60)

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )
    conn.autocommit = False
    cur = conn.cursor()

    total_synced = 0
    total_created = 0
    total_updated = 0

    for copro_name, (formula, copro_code_ncg) in COPRO_FILTERS.items():
        print(f"\n📡 Copropriété : {copro_name} (code_ncg={copro_code_ncg})")
        print(f"   Formule Airtable : {formula}")

        # Fetch all records with pagination
        all_records = []
        offset = None
        page = 0
        while True:
            page += 1
            data = fetch_airtable_records(formula, offset)
            records = data.get("records", [])
            all_records.extend(records)
            print(f"   Page {page} : {len(records)} records")
            offset = data.get("offset")
            if not offset:
                break

        print(f"   Total Airtable : {len(all_records)} sinistres")

        # Check existing dossiers
        cur.execute(
            "SELECT airtable_record_id FROM dossiers WHERE airtable_record_id IS NOT NULL AND copropriete = %s",
            [copro_name]
        )
        existing_ids = {row[0] for row in cur.fetchall()}

        # Upsert each record
        for rec_idx, rec in enumerate(all_records):
            dossier = map_airtable_to_dossier(rec, copro_name, default_code_ncg=copro_code_ncg)
            is_new = rec["id"] not in existing_ids
            # Debug: print first record's new fields
            if rec_idx == 0:
                for _dk in ['statut_detail', 'triage', 'gestionnaire_syndic', 'email_gestionnaire']:
                    print(f"   DEBUG dossier['{_dk}'] = {dossier.get(_dk)}")

            try:
                upsert_dossier(cur, dossier)
                if is_new:
                    total_created += 1
                else:
                    total_updated += 1
                total_synced += 1
            except Exception as e:
                import traceback
                print(f"   ⚠️ Erreur sur {rec['id']}: {e}")
                traceback.print_exc()
                conn.rollback()
                continue

        conn.commit()

        # ── Merge RAG duplicates — DÉSACTIVÉ (v0.5.0) ──
        # On garde tous les dossiers RAG + Airtable sans dédoublonnage.
        # Claude fait la synthèse quand un utilisateur interroge des dossiers
        # au même nom de lésé (ex: LEMEAU RAG 23/11 + LEMEAU Airtable 17/05).
        # merge_rag_into_airtable(cur, copro_code_ncg)
        print(f"\n   ℹ️  Dédoublonnage RAG↔Airtable désactivé (tous les dossiers conservés)")

        # Summary stats
        cur.execute("""
            SELECT statut, COUNT(*) FROM dossiers
            WHERE copropriete = %s AND airtable_record_id IS NOT NULL
            GROUP BY statut ORDER BY statut
        """, [copro_name])
        print(f"\n   Résumé après synchro :")
        for row in cur.fetchall():
            emoji = {"EN_COURS": "🟡", "EN_ATTENTE": "🔴", "CLOTURE": "🟢"}.get(row[0], "⚪")
            print(f"     {emoji} {row[0]:15s} : {row[1]}")

    # Global stats
    cur.execute("SELECT COUNT(*) FROM dossiers")
    total_dossiers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dossiers WHERE airtable_record_id IS NOT NULL")
    total_airtable = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM dossiers WHERE airtable_record_id IS NULL")
    total_rag = cur.fetchone()[0]

    print(f"\n{'=' * 60}")
    print(f"✅ Synchronisation terminée")
    print(f"   {total_synced} records traités ({total_created} créés, {total_updated} mis à jour)")
    print(f"   {total_dossiers} dossiers totaux en base")
    print(f"     dont {total_airtable} issus d'Airtable")
    print(f"     dont {total_rag} issus du RAG uniquement")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
