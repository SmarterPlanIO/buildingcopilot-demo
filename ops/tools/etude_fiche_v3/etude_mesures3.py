# -*- coding: utf-8 -*-
"""T20-T23 : anatomie des indeterminees (non-votes legitimes vs echecs
d'extraction), gap 'adoptee' recuperable, taille d'un index des dossiers,
resumes de PV."""
import os, sys
import psycopg2

SCRIPTS = r"G:/Mon Drive/Projet SmarterPlan/Sales/Prospects/NCG/202512 Mission Déploiement IA interne/Scripts"
sys.path.insert(0, SCRIPTS)
import pipeline_config as pcfg

conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT, dbname=pcfg.DB_NAME,
                        user=pcfg.DB_USER_ADMIN, password=os.environ["DB_PASSWORD"])
cur = conn.cursor()


def q(sql, p=None):
    cur.execute(sql, p)
    return cur.fetchall()


def one(sql, p=None):
    return q(sql, p)[0][0]


print("=== T20. anatomie des 7 565 INDETERMINEES (sur le texte du 1er chunk) ===")
cur.execute("""
    CREATE TEMP TABLE ind AS
    SELECT r.resolution_id, r.code_ncg, r.numero, r.flags, c.text, length(c.text) AS n
    FROM resolutions r JOIN chunks c ON c.chunk_id = r.chunk_ids[1]
    WHERE r.resultat = 'indetermine'
""")
ni = one("SELECT COUNT(*) FROM ind")
cats = [
    ("non soumise au vote (explicite)", r"n['’]a pas fait l['’]objet d['’]un vote|pas soumis[e]? au vote|sans vote|ne donne pas lieu à (un )?vote|point d['’]information|pour information"),
    ("gabarit vide 'POUR : sur tantièmes'", r"POUR\s*:\s*sur tantièmes|CONTRE\s*:\s*sur tantièmes|totalisant\s*\.+\s*/"),
    ("sommaire / sous-titres seuls (<400c, aucun verbe de vote)", None),
    ("contient une proclamation 'adoptée' NON captée", r"r[ée]solution (est |a [ée]t[ée] )?adopt[ée]e|adopt[ée]e à (la majorit|l['’]unanimit)"),
    ("contient une proclamation 'rejetée' NON captée", r"r[ée]solution (est |a [ée]t[ée] )?rejet[ée]e|rejet[ée]e à la majorit"),
    ("contient un décompte chiffré (pour/contre + nombre)", r"(pour|contre)\s*:?\s*\d"),
    ("reporté / ajourné", r"report[ée]e?|ajourn[ée]e?|renvoy[ée]e? à une prochaine"),
]
for lab, rx in cats:
    if rx is None:
        c = one(r"SELECT COUNT(*) FROM ind WHERE n < 400 AND text !~* '(vot|adopt|rejet|abstention)'")
    else:
        c = one("SELECT COUNT(*) FROM ind WHERE text ~* %s", (rx,))
    print(f"  {lab:58s} {c:5d}  ({100*c/ni:.1f}%)")
print(f"  longueur mediane des indeterminees : {one('SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY n) FROM ind')}")
print(f"  longueur mediane des ETABLIES      : {one(chr(39).join(['SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY length(c.text)) FROM resolutions r JOIN chunks c ON c.chunk_id=r.chunk_ids[1] WHERE r.resultat IN (', 'adoptee', ',', 'rejetee', ')']))}")

print("\n=== T21. le 'gap adoptee' : echantillon de 4 indeterminees contenant 'adoptée' ===")
for code, num, txt in q(r"SELECT code_ncg, numero, text FROM ind WHERE text ~* 'r[ée]solution (est |a [ée]t[ée] )?adopt[ée]e' ORDER BY random() LIMIT 4"):
    i = txt.lower().find("adopt")
    print(f"  [{code} res.{num}] …{txt[max(0, i-230):i+60].replace(chr(10), ' ')}…")

print("\n=== T22. taille d'un INDEX COMPLET des dossiers par copro ===")
print("  dossiers/copro :", q("SELECT code_ncg, COUNT(*) FROM dossiers GROUP BY 1 ORDER BY 2 DESC LIMIT 5"))
print("  ~150 octets/ligne -> 8050 (102 dossiers) = ~15 Ko ; median copro =",
      q("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY n) FROM (SELECT COUNT(*) n FROM dossiers GROUP BY code_ncg) s")[0][0], "dossiers")

print("\n=== T23. resumes (04, Haiku) des PV_AG : capturent-ils les decisions ? (3 au hasard) ===")
for nf, resume, n_res in q("""SELECT d.nom_fichier, d.resume,
        (SELECT COUNT(*) FROM resolutions r WHERE r.source_file = d.source_file AND r.resultat IN ('adoptee','rejetee'))
        FROM documents d WHERE COALESCE(d.doc_type_corrige, d.doc_type)='PV_AG' AND length(COALESCE(d.resume,''))>40
        ORDER BY random() LIMIT 3"""):
    print(f"  {nf[:60]} ({n_res} res. etablies)\n     RESUME : {resume[:330]}")
conn.close()
