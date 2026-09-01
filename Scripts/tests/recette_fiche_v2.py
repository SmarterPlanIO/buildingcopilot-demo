"""C6 — RECETTE de la fiche v2 (annuaire) : invariants + golden case.

Rejouable par tenant (PALIM_CLIENT) : c'est la porte de sortie de chaque rollout
(NCG d'abord, puis Delacour, puis CSG) et la sonde de non-régression à rejouer
après chaque deploy — cf. PLAN_FIABILITE_SYNTHESE.md §C6 et PLAN_SELF_LEARNING.md.

Ce que la recette prouve (et pas seulement « ça tourne ») :
  I1  structure : les 5 sections attendues, aucune clé de prose libre
  I2  pointeurs non vides sur chaque question clé et chaque dossier chaud
  I3  INTÉGRITÉ RÉFÉRENTIELLE : tout chunk_id / resolution_id / dossier_id cité
      existe réellement en base — un pointeur mort est pire qu'inutile, il envoie
      le LLM appelant dans le vide
  I4  cohérence des chiffres : la fiche ne peut pas se contredire avec la base
      (c'était le symptôme v1 : narratif 25 dossiers vs faits 37)
  I5  aucune résolution à résultat établi sans source de résultat
  I6  contrat MCP : overview renvoie bien fiche_version=v2 et AUCUN narratif
  G   golden case : une question clé « comptes de l'exercice N » doit exister
      quand aucune approbation n'est établie, et pointer des résolutions réelles

Lance (nécessite la DB du tenant) :
    DB_PASSWORD=... python tests/recette_fiche_v2.py
    PALIM_CLIENT=delacour DB_PASSWORD=... python tests/recette_fiche_v2.py
"""
import os
import sys

import psycopg2

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SCRIPTS, "mcp_server"))
sys.path.insert(0, SCRIPTS)

import pipeline_config as pcfg  # noqa: E402
from PALIM_overview import get_overview  # noqa: E402

SECTIONS = {"identite", "chiffres_cles", "dossiers_chauds", "questions_cles", "pv_recents"}
CLES_ADMISES = SECTIONS | {"fiche_version", "usage", "note_dossiers_chauds"}

echecs = []
alertes = []


def check(cond, message, dur=True):
    if not cond:
        (echecs if dur else alertes).append(message)
    return cond


def main():
    pwd = os.environ.get("DB_PASSWORD")
    if not pwd:
        raise SystemExit("DB_PASSWORD manquant.")
    conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT,
                            dbname=pcfg.DB_NAME, user=pcfg.DB_USER_ADMIN, password=pwd)
    cur = conn.cursor()
    print(f"RECETTE fiche v2 — client={pcfg.CLIENT_CODE} db={pcfg.DB_NAME}\n")

    cur.execute("SELECT code_ncg, faits_v2 FROM copro_synthese WHERE fiche_version = 'v2'")
    fiches = cur.fetchall()
    cur.execute("SELECT COUNT(DISTINCT code_ncg) FROM chunks WHERE code_ncg IS NOT NULL")
    n_copros = int(cur.fetchone()[0] or 0)
    print(f"  {len(fiches)} fiche(s) v2 / {n_copros} copro(s) en base")
    if not fiches:
        print("\nAUCUNE fiche v2 : tenant non migré (lancer 06a + 09b + 09).")
        return 1
    check(len(fiches) == n_copros,
          f"I0 couverture : {len(fiches)} fiches pour {n_copros} copros", dur=False)

    # référentiels pour l'intégrité (I3)
    cur.execute("SELECT chunk_id FROM chunks")
    chunks_ref = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT resolution_id FROM resolutions")
    res_ref = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT dossier_id FROM dossiers")
    dos_ref = {r[0] for r in cur.fetchall()}

    n_q = n_dc = n_ptr = 0
    for code, f in fiches:
        # I1 structure
        check(SECTIONS <= set(f), f"I1 [{code}] sections manquantes : {SECTIONS - set(f)}")
        inconnues = set(f) - CLES_ADMISES
        check(not inconnues, f"I1 [{code}] clés inattendues (prose libre ?) : {inconnues}")

        # I2/I3 questions clés
        for q in f["questions_cles"]:
            n_q += 1
            check(bool(q.get("question")) and bool(q.get("regle")),
                  f"I2 [{code}] question sans énoncé ou sans règle")
            p = q.get("pointeurs") or {}
            check(bool(p), f"I2 [{code}] question sans pointeurs : {q.get('question','')[:50]}")
            for r in p.get("resolutions", []):
                n_ptr += 1
                check(r["resolution_id"] in res_ref,
                      f"I3 [{code}] resolution_id fantôme {r['resolution_id']}")
                for cid in r.get("chunk_ids", []):
                    n_ptr += 1
                    check(cid in chunks_ref, f"I3 [{code}] chunk_id fantôme {cid}")
            for d in p.get("dossiers", []):
                n_ptr += 1
                check(d["dossier_id"] in dos_ref,
                      f"I3 [{code}] dossier_id fantôme {d['dossier_id']}")

        # I2/I3 dossiers chauds
        for d in f["dossiers_chauds"]:
            n_dc += 1
            check(bool(d.get("motif_selection")),
                  f"I2 [{code}] dossier chaud sans motif_selection : {d.get('dossier_id')}")
            check(d["dossier_id"] in dos_ref,
                  f"I3 [{code}] dossier chaud fantôme {d['dossier_id']}")
            for cid in d.get("pointeurs", {}).get("chunk_ids_entree", []):
                n_ptr += 1
                check(cid in chunks_ref, f"I3 [{code}] chunk d'entrée fantôme {cid}")

        # I3 pointeurs d'identité + PV récents
        for cle in ("mandat_syndic_pointeur", "conseil_syndical_pointeur"):
            ptr = f["identite"].get(cle)
            if ptr:
                n_ptr += 1
                check(ptr["resolution_id"] in res_ref,
                      f"I3 [{code}] {cle} fantôme {ptr['resolution_id']}")
        for pv in f["pv_recents"]:
            for r in pv["resolutions_etablies"]:
                n_ptr += 1
                check(r["resolution_id"] in res_ref,
                      f"I3 [{code}] résolution de PV fantôme {r['resolution_id']}")
                check(r["resultat"] in ("adoptee", "rejetee", "retiree"),
                      f"I1 [{code}] PV récent : résultat non établi listé comme établi")

        # I4 cohérence des chiffres avec la base (le symptôme v1)
        c = f["chiffres_cles"]
        cur.execute("SELECT COUNT(*) FROM dossiers WHERE code_ncg = %s", (code,))
        check(c["dossiers"]["total"] == int(cur.fetchone()[0] or 0),
              f"I4 [{code}] total dossiers de la fiche != base")
        cur.execute("SELECT COUNT(DISTINCT source_file) FROM documents WHERE code_ncg = %s", (code,))
        check(c["nb_documents"] == int(cur.fetchone()[0] or 0),
              f"I4 [{code}] nb_documents de la fiche != base")

    # I5 aucune résolution établie sans source
    cur.execute("""SELECT COUNT(*) FROM resolutions
                   WHERE resultat IN ('adoptee','rejetee') AND source_resultat IS NULL""")
    check(int(cur.fetchone()[0] or 0) == 0, "I5 résolution établie sans source_resultat")

    # I6 contrat MCP
    code0 = fiches[0][0]
    ov = get_overview(conn, code0)
    check(ov.get("fiche_version") == "v2", f"I6 overview {code0} : fiche_version != v2")
    check("narratif" not in ov, f"I6 overview {code0} : un narratif est encore servi")
    check(bool(ov.get("usage")), f"I6 overview {code0} : champ usage absent")
    check(SECTIONS <= set(ov.get("fiche", {})), f"I6 overview {code0} : sections incomplètes")

    # G golden case : question « comptes de l'exercice N » fondée
    trouve = False
    for code, f in fiches:
        for q in f["questions_cles"]:
            if "comptes de l'exercice" in q["question"]:
                trouve = True
                res = (q["pointeurs"] or {}).get("resolutions", [])
                check(bool(res), f"G [{code}] question comptes sans résolution pointée")
                for r in res:
                    check(r["resultat"] != "adoptee",
                          f"G [{code}] question comptes alors qu'une adoption est établie")
                break
        if trouve:
            break
    check(trouve, "G aucune question « comptes de l'exercice » sur ce tenant", dur=False)

    print(f"  {n_q} questions clés, {n_dc} dossiers chauds, {n_ptr} pointeurs vérifiés")
    conn.close()

    if alertes:
        print("\nALERTES (non bloquantes) :")
        for a in alertes:
            print(f"  ! {a}")
    if echecs:
        print(f"\nECHECS ({len(echecs)}) :")
        for e in echecs[:25]:
            print(f"  X {e}")
        return 1
    print("\nRECETTE OK — tous les invariants tiennent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
