"""
ÉTAPE 8b — Dédoublonnage RAG ↔ Airtable (runner standalone à garde-fous)

Fusionne les doublons RAG dans leur jumeau Airtable via merge_rag_into_airtable()
de 08_airtable_sync.py (2 passes : nom haute confiance, puis date+type+candidat unique).
Le survivant est la ligne Airtable, qui absorbe les champs RAG vides ; la ligne RAG
est supprimée. Opère TABLE-ONLY (pas de re-chunk/re-embed/TRUNCATE) — léger.

Garde-fous :
  - DRY-RUN par défaut (rollback). --apply pour commiter.
  - Tout dans une transaction unique.
  - Snapshot complet des dossiers AVANT merge -> dump des lignes RAG supprimées
    dans un rapport horodaté, AVANT le commit explicite.

Usage :
  # Dry-run sur les 10 copros (aucune écriture) :
  DB_HOST=... DB_PASSWORD=... PYTHONIOENCODING=utf-8 python 08b_dedup_rag_airtable.py

  # Appliquer pour de vrai (commit) :
  DB_HOST=... DB_PASSWORD=... PYTHONIOENCODING=utf-8 python 08b_dedup_rag_airtable.py --apply

  # Cibler une seule copro :
  ... python 08b_dedup_rag_airtable.py --copro 8050

Creds : DB_HOST, DB_PASSWORD (+ DB_USER/DB_NAME/DB_PORT si non défaut). PAS d'AIRTABLE_PAT.
"""

import argparse
import importlib.util
import os
from datetime import date, datetime
from pathlib import Path

import psycopg2
import psycopg2.extras

# ── Import du module 08 (nom de fichier non importable directement) ──
_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("airtable_sync_08", _HERE / "08_airtable_sync.py")
_m08 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m08)

merge_rag_into_airtable = _m08.merge_rag_into_airtable
COPRO_FILTERS = _m08.COPRO_FILTERS

# ── Colonnes capturées dans le snapshot pré-merge (pour le rapport d'audit) ──
_SNAPSHOT_COLS = [
    "dossier_id", "code_ncg", "airtable_record_id",
    "lese_nom", "nom_dossier", "date_ouverture", "type_dossier",
    "ref_sinistre_client", "statut",
]

REPORT_DIR = _HERE.parent / "Résultats bruts"


def _fmt(v):
    if v is None:
        return "∅"
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "..."


def snapshot_copro(cur, code_ncg):
    """Capture toutes les lignes dossiers d'une copro, indexées par dossier_id."""
    cols = ", ".join(_SNAPSHOT_COLS)
    cur.execute(f"SELECT {cols} FROM dossiers WHERE code_ncg = %s", [code_ncg])
    snap = {}
    for row in cur.fetchall():
        d = dict(zip(_SNAPSHOT_COLS, row))
        snap[d["dossier_id"]] = d
    return snap


def main():
    ap = argparse.ArgumentParser(description="Dédoublonnage RAG↔Airtable (table-only).")
    ap.add_argument("--apply", action="store_true",
                    help="Commit réel. Sans ce flag : dry-run (rollback).")
    ap.add_argument("--copro", default=None,
                    help="Cible un seul code_ncg (ex: 8050). Défaut : toutes COPRO_FILTERS.")
    ap.add_argument("--report", default=None,
                    help="Chemin du rapport. Défaut : Résultats bruts/dedup_rag_airtable_<ts>.txt")
    args = ap.parse_args()

    db_host = os.environ.get("DB_HOST", "")
    db_password = os.environ.get("DB_PASSWORD", "")
    if not db_host or not db_password:
        raise SystemExit("DB_HOST et DB_PASSWORD requis en variables d'environnement.")

    codes = [args.copro] if args.copro else [c for (_f, c) in COPRO_FILTERS.values()]
    mode = "APPLY (commit)" if args.apply else "DRY-RUN (rollback)"

    print("=" * 64)
    print(f"DÉDOUBLONNAGE RAG ↔ AIRTABLE — {mode}")
    print(f"Copros : {', '.join(codes)}")
    print("=" * 64)

    conn = psycopg2.connect(
        host=db_host,
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "ragadmin"),
        password=db_password,
    )
    conn.autocommit = False
    cur = conn.cursor()

    report_lines = []
    report_lines.append(f"DÉDOUBLONNAGE RAG ↔ AIRTABLE — {mode}")
    report_lines.append(f"Copros : {', '.join(codes)}")
    report_lines.append("=" * 64)

    total_merged = 0
    try:
        for code in codes:
            snap = snapshot_copro(cur, code)
            n_before = len(snap)
            merged_count, details = merge_rag_into_airtable(cur, code)
            total_merged += merged_count

            header = f"\n[{code}] {n_before} dossiers avant → {merged_count} fusion(s)"
            print(header)
            report_lines.append(header)

            for d in details:
                rag_id = d["rag_absorbed"]
                at_id = d["airtable"]
                rag = snap.get(rag_id, {})
                at = snap.get(at_id, {})
                line = (
                    f"  - RAG supprimé #{rag_id} "
                    f"[lese={_fmt(rag.get('lese_nom'))} | nom={_fmt(rag.get('nom_dossier'))} | "
                    f"date={_fmt(rag.get('date_ouverture'))} | type={_fmt(rag.get('type_dossier'))} | "
                    f"ref={_fmt(rag.get('ref_sinistre_client'))}]\n"
                    f"      → absorbé par Airtable #{at_id} "
                    f"[lese={_fmt(at.get('lese_nom'))} | date={_fmt(at.get('date_ouverture'))}] "
                    f"via={d['via']} | champs copiés={d['fields_copied'] or '∅'}"
                )
                print(line)
                report_lines.append(line)

        summary = f"\nTOTAL : {total_merged} fusion(s) sur {len(codes)} copro(s)."
        print(summary)
        report_lines.append(summary)

        # ── Dump du rapport AVANT le commit/rollback explicite ──
        report_path = Path(args.report) if args.report else (
            REPORT_DIR / f"dedup_rag_airtable_{'apply' if args.apply else 'dryrun'}.txt"
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        print(f"\n📝 Rapport écrit : {report_path}")

        if args.apply:
            conn.commit()
            print("✅ COMMIT effectué — suppressions appliquées en base.")
        else:
            conn.rollback()
            print("↩️  DRY-RUN — rollback, aucune écriture en base.")
    except Exception:
        conn.rollback()
        print("❌ Erreur — rollback intégral.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
