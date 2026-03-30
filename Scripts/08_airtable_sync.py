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

# Filtrer par copropriété TARIEL (ref NCG = 5390)
# Pour ajouter d'autres copros, étendre cette liste
COPRO_FILTERS = {
    "SOURCE_ARCHIVES": 'OR(FIND("5390",{Name}),FIND("TIVOLI",{Name}))',
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


def map_airtable_to_dossier(record, copropriete):
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
    _name = f.get("Name", "")
    _code_ncg_match = re.search(r'\((\d{4,6})\)', _name)
    _code_ncg = _code_ncg_match.group(1) if _code_ncg_match else None

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

    for copro_name, formula in COPRO_FILTERS.items():
        print(f"\n📡 Copropriété : {copro_name}")
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
            dossier = map_airtable_to_dossier(rec, copro_name)
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
