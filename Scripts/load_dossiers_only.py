"""Charge UNIQUEMENT la table dossiers (chunks + documents déjà chargés par 06b).

Reproduit verbatim le bloc dossiers de 06b_load_db.py (avec le fix parse_date),
sans re-TRUNCATE/recharger les 166k chunks. TRUNCATE dossiers pour repartir propre,
puis INSERT des dossiers du fichier global. 08_airtable_sync réinjecte ensuite
les dossiers Airtable.
"""
import json
import os
import re
import psycopg2
from psycopg2.extras import execute_values

INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings_sq.jsonl"
DOSSIERS_FILE = os.path.join(os.path.dirname(INPUT_FILE), "dossiers.jsonl")

DB_HOST = "sp-rag-ncg-copros.c8ypoidw2hzb.eu-west-1.rds.amazonaws.com"
DB_PORT = 5432
DB_NAME = "postgres"
DB_USER = "ragadmin"
DB_PASSWORD = os.environ.get("DB_PASSWORD")  # secret hors du code — exporter avant de lancer
if not DB_PASSWORD:
    raise SystemExit("❌ DB_PASSWORD manquant. Lance : DB_PASSWORD=... python load_dossiers_only.py")


def clean(s):
    return s.replace("\x00", "") if isinstance(s, str) else s


def extract_code_ncg(text):
    if not text:
        return None
    m = re.match(r'\s*(\d{4,6})\s*-\s*', text)  # dossier copro en tête (prioritaire)
    if m:
        return m.group(1)
    m = re.search(r'\((\d{4,6})\)', text)
    if m:
        return m.group(1)
    m = re.search(r'(?:[\\\\/])(\d{4,6})\s*-\s*', text)
    if m:
        return m.group(1)
    m = re.search(r'(?:^|[^0-9])(\d{4,6})\s+-\s+', text)
    if m:
        return m.group(1)
    return None


def parse_date(v):
    if not isinstance(v, str) or not v.strip():
        return None
    m = re.fullmatch(r'(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?', v.strip())
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2) or "1", m.group(3) or "1"
    return f"{y}-{int(mo):02d}-{int(d):02d}"


conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM dossiers;")
print(f"Dossiers avant : {cur.fetchone()[0]}")
cur.execute("TRUNCATE TABLE dossiers;")
print("Table dossiers vidée")

dossier_batch = []
with open(DOSSIERS_FILE, "r", encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line)
        _dossier_code_ncg = extract_code_ncg(rec.get("nom_dossier", ""))
        if not _dossier_code_ncg:
            for _dl in (rec.get("documents_lies") or []):
                _dossier_code_ncg = extract_code_ncg(_dl)
                if _dossier_code_ncg:
                    break
        if not _dossier_code_ncg:
            _dossier_code_ncg = extract_code_ncg(rec.get("dossier_id", ""))
        row = (
            clean(rec["dossier_id"]),
            clean(rec["copropriete"]),
            clean(rec["type_dossier"]),
            clean(rec["nom_dossier"]),
            rec.get("statut", "EN_ATTENTE"),
            parse_date(rec.get("date_ouverture")),
            parse_date(rec.get("date_cloture")),
            clean(rec.get("lese_nom") or ""),
            clean(rec.get("lese_lot") or ""),
            clean(rec.get("responsable_nom") or ""),
            clean(rec.get("responsable_lot") or ""),
            clean(rec.get("expert_nom") or ""),
            clean(rec.get("assureur") or ""),
            json.dumps(rec.get("etapes", []), ensure_ascii=False),
            rec.get("pieces_requises", []),
            rec.get("pieces_fournies", []),
            rec.get("montant_estime"),
            rec.get("montant_reel"),
            rec.get("documents_lies", []),
            clean(rec.get("resume_ia") or ""),
            _dossier_code_ncg,
        )
        dossier_batch.append(row)

if dossier_batch:
    execute_values(cur, """
        INSERT INTO dossiers
        (dossier_id, copropriete, type_dossier, nom_dossier, statut,
         date_ouverture, date_cloture,
         lese_nom, lese_lot, responsable_nom, responsable_lot,
         expert_nom, assureur,
         etapes, pieces_requises, pieces_fournies,
         montant_estime, montant_reel, documents_lies, resume_ia, code_ncg)
        VALUES %s
        ON CONFLICT (dossier_id) DO UPDATE SET
            statut = EXCLUDED.statut,
            etapes = EXCLUDED.etapes,
            pieces_fournies = EXCLUDED.pieces_fournies,
            montant_estime = EXCLUDED.montant_estime,
            montant_reel = EXCLUDED.montant_reel,
            documents_lies = EXCLUDED.documents_lies,
            resume_ia = EXCLUDED.resume_ia,
            code_ncg = EXCLUDED.code_ncg,
            updated_at = NOW()
    """, dossier_batch)
    conn.commit()

cur.execute("SELECT COUNT(*) FROM dossiers;")
n = cur.fetchone()[0]
cur.execute("SELECT statut, COUNT(*) FROM dossiers GROUP BY statut ORDER BY statut;")
summary = ", ".join(f"{s}: {c}" for s, c in cur.fetchall())
print(f"✅ {n} dossiers chargés ({summary})")
cur.close()
conn.close()
