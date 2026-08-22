"""add_copro.py - Pre-vol et post-vol de l'ajout d'une copro au profil client.

`ingest.py --copro X` sait ingerer une copro de bout en bout. Ce script couvre
ce qu'il y a AVANT (verifications + ecriture de l'entree dans client.json) et
APRES (recette en base), c'est-a-dire les etapes qu'on refaisait a la main a
chaque copro et ou l'on se trompait.

Usage type (regime immatriculation RNIC, Delacour / CSG / nouveaux clients) :

    PALIM_CLIENT=csg PYTHONIOENCODING=utf-8 python add_copro.py \\
        --immat AB0-835-843 --folder "Document extract - 33 Rue Lacepede" \\
        --label "SDC 33-35-41 rue Lacepede, 75005 Paris" --lobby-code C0216

Regime codes internes (NCG) :

    PALIM_CLIENT=ncg PYTHONIOENCODING=utf-8 python add_copro.py \\
        --code 8050 --folder "8050 - STYLE - 145 AVENUE DE FRANCE"

Options :
    --raw-dir PATH   source hors RAW_ROOT (share VPN/UNC), ecrite telle quelle
    --check-only     execute les controles, n'ecrit pas dans client.json
    --verify         mode post-ingestion : recette en base pour ce code
    --skip-rnic / --skip-assynco   coupe un controle externe

Le script n'ecrit JAMAIS en base et ne lance JAMAIS l'ingestion : il prepare et
il controle. Relire `git diff Scripts/clients/<client>/client.json` apres coup.
"""
import argparse
import json
import sys
from pathlib import Path

import pipeline_config as pcfg
from copro_id import canon, display, is_immatriculation

RNIC_RESOURCE = "3ea8e2c3-0038-464a-b17e-cd5c91f65ce2"  # RNIC "actualisation quotidienne"
RNIC_URL = f"https://tabular-api.data.gouv.fr/api/resources/{RNIC_RESOURCE}/data/"
ASSYNCO_BASE = "appi1ee5p93EBHtLR"
ASSYNCO_TABLE_COPRO = "tblsPUcmAXwWcZFjj"
ASSYNCO_TABLE_ORG = "tblKwYRub475OfjMI"

_warnings: list[str] = []


def warn(msg):
    _warnings.append(msg)
    print(f"  ⚠️  {msg}")


def fail(msg):
    print(f"\n❌ {msg}")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────
# CONTROLES
# ──────────────────────────────────────────────────────────────

def check_profil(code, folder, lobby_code):
    """Le code est-il libre, le format est-il coherent avec le regime du client ?"""
    print(f"\n[1/5] Profil client {pcfg.CLIENT_CODE} ({pcfg.CLIENT_NAME})")
    if code in pcfg.COPRO_META:
        fail(f"Le code {display(code)} est deja dans le profil "
             f"(dossier : {pcfg.COPRO_META[code]['folder']}). Rien a ajouter.")

    existing = pcfg.COPRO_META
    regime_immat = sum(1 for c in existing if is_immatriculation(c))
    if existing and is_immatriculation(code) and regime_immat == 0:
        warn("ce client indexe ses copros par codes internes, tu ajoutes une "
             "immatriculation RNIC : melange de regimes dans le meme profil.")
    if existing and not is_immatriculation(code) and regime_immat == len(existing):
        warn("ce client est en regime immatriculation RNIC, tu ajoutes un code "
             "interne. Preferer l'immatriculation comme cle (standard produit).")

    if lobby_code:
        alias = canon(lobby_code)
        if alias in existing:
            fail(f"L'alias {lobby_code} est deja une CLE du profil ({alias}).")
        for c, meta in existing.items():
            if meta.get("lobby_code") and canon(meta["lobby_code"]) == alias:
                fail(f"L'alias {lobby_code} pointe deja vers {display(c)}.")
    # collision de dossier : deux codes sur le meme dossier = shards melanges
    for c, meta in existing.items():
        if meta.get("folder") == folder:
            fail(f"Le dossier '{folder}' est deja rattache a {display(c)}.")
    print(f"  ✅ code {display(code)} libre, dossier libre")


def check_source(folder, raw_dir):
    """La source existe-t-elle et que contient-elle ?"""
    print("\n[2/5] Source documentaire")
    src = Path(raw_dir) if raw_dir else pcfg.RAW_ROOT / folder
    print(f"  {src}")
    if not src.is_dir():
        fail("Source introuvable. Sur un raccourci Google Drive, viser la cible "
             "reelle (G:\\.shortcut-targets-by-id\\<id>), pas le fichier .lnk.")

    n, taille, exts = 0, 0, {}
    for p in src.rglob("*"):
        if p.is_file() and p.name != "desktop.ini":
            n += 1
            taille += p.stat().st_size
            exts[p.suffix.lower()] = exts.get(p.suffix.lower(), 0) + 1
    if n == 0:
        fail("Source vide (0 fichier exploitable).")

    top = sorted(exts.items(), key=lambda kv: -kv[1])[:6]
    print(f"  ✅ {n} fichiers, {taille / 1e6:.0f} Mo, {dict(top)}")
    gsheets = sum(v for k, v in exts.items() if k in (".gsheet", ".gdoc", ".gslides"))
    if gsheets:
        warn(f"{gsheets} fichiers Google natifs : illisibles hors API Drive, "
             "l'etape 01 les exclut (contenu perdu pour le RAG).")
    print(f"  Ordre de grandeur cout ingestion : {n * 0.004:.0f} a {n * 0.02:.0f} $ HT "
          "(depend surtout du taux de PDF scannes)")


def check_rnic(code):
    """L'immatriculation existe-t-elle au Registre national ?"""
    print("\n[3/5] Registre national des coproprietes (RNIC)")
    if not is_immatriculation(code):
        print("  (code interne, pas d'immatriculation a verifier)")
        return
    try:
        import requests
        r = requests.get(RNIC_URL, params={"numero_immatriculation__exact": code}, timeout=30)
        rows = r.json().get("data", [])
    except Exception as exc:  # noqa: BLE001 - controle non bloquant
        warn(f"RNIC injoignable ({exc}). Verification a refaire.")
        return
    if not rows:
        warn(f"{display(code)} introuvable au registre. Verifier la saisie, ou "
             "copropriete non immatriculee / immatriculation en cours.")
        return
    d = rows[0]
    print(f"  ✅ {d.get('nom_usage_copropriete')} | {d.get('adresse_reference')}")
    print(f"     lots hab/bur/com : {d.get('nombre_lots_habitation_bureaux_commerces')} | "
          f"syndic : {d.get('type_syndic')} | maj : {d.get('date_derniere_maj')}")
    if d.get("date_fin_mandat"):
        warn(f"mandat declare expire au {d['date_fin_mandat']} : le syndic doit "
             "regulariser sa declaration au registre.")


def check_assynco(code, folder):
    """La copro est-elle resolvable dans l'ERP, et appartient-elle au bon tenant ?"""
    print("\n[4/5] ERP Assynco (isolation tenant)")
    assynco = getattr(pcfg, "_cfg", {}).get("assynco", {}) if hasattr(pcfg, "_cfg") else {}
    labels = assynco.get("syndic_labels", [])
    if not assynco.get("enabled"):
        print("  (Assynco desactive pour ce client)")
        return
    if not labels:
        warn("allowlist syndic vide dans le profil : fail-closed cote MCP, "
             "aucun acces Assynco ne fonctionnera.")
        return
    try:
        import boto3
        import requests
        sm = boto3.client("secretsmanager", region_name="eu-west-1")
        pat = json.loads(sm.get_secret_value(SecretId="palim/airtable_pat")["SecretString"])["AIRTABLE_PAT"]
        h = {"Authorization": f"Bearer {pat}"}
        formula = ("SUBSTITUTE(SUBSTITUTE({Numéro d'immatriculation},'-',''),' ','')="
                   f"'{code}'") if is_immatriculation(code) else f"{{Ref client}}='{code}'"
        recs = requests.get(f"https://api.airtable.com/v0/{ASSYNCO_BASE}/{ASSYNCO_TABLE_COPRO}",
                            headers=h, params={"filterByFormula": formula, "maxRecords": 3},
                            timeout=30).json().get("records", [])
    except Exception as exc:  # noqa: BLE001 - controle non bloquant
        warn(f"Controle Assynco impossible ({exc}). A refaire avant le smoke test.")
        return

    if not recs:
        champ = "Numéro d'immatriculation" if is_immatriculation(code) else "Ref client"
        warn(f"aucune fiche Assynco resolvable par {champ} = {display(code)}. "
             "Faire remplir ce champ par Assynco, sinon les tools PALIM_assynco_* "
             "repondront introuvable pour cette copro.")
        return
    f = recs[0]["fields"]
    print(f"  ✅ fiche ERP : {f.get('Nom')} ({len(f.get('Sinistres', []))} sinistres, "
          f"{len(f.get('Polices', []))} polices)")
    syndic_ids = f.get("Syndic") or []
    if not syndic_ids:
        warn("fiche ERP sans syndic rattache : isolation tenant invérifiable.")
        return
    try:
        import requests
        org = requests.get(f"https://api.airtable.com/v0/{ASSYNCO_BASE}/{ASSYNCO_TABLE_ORG}/{syndic_ids[0]}",
                           headers=h, timeout=30).json().get("fields", {})
    except Exception:  # noqa: BLE001
        return
    nom_syndic = org.get("Name", "")
    if nom_syndic in labels:
        print(f"  ✅ syndic ERP '{nom_syndic}' dans l'allowlist du profil")
    else:
        warn(f"syndic ERP '{nom_syndic}' ABSENT de l'allowlist {labels}. "
             "Soit la copro appartient a un autre tenant (ne pas l'ajouter), "
             "soit le libelle du profil est a corriger.")


def ecrire_profil(code, folder, label, lobby_code, raw_dir, check_only):
    """Ajoute l'entree dans clients/<client>/client.json (indent 2, accents preserves)."""
    print("\n[5/5] Ecriture du profil")
    entry = {"folder": folder}
    if lobby_code:
        entry["lobby_code"] = lobby_code
    if label:
        entry["label"] = label
    if raw_dir:
        entry["raw_dir"] = raw_dir

    if check_only:
        print("  (--check-only) entree qui SERAIT ajoutee :")
        print(f'  "{code}": {json.dumps(entry, ensure_ascii=False)}')
        return

    path = pcfg.CLIENTS_DIR / pcfg.CLIENT_CODE / "client.json"
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.setdefault("included_copros", {})[code] = entry
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"  ✅ {code} ajoute dans {path}")


def verify(code):
    """Recette post-ingestion : ce qui doit etre en base pour cette copro."""
    import boto3
    import psycopg2

    print(f"\nRECETTE {display(code)} sur {pcfg.require_db_host()}")
    sm = boto3.client("secretsmanager", region_name="eu-west-1")
    raw = sm.get_secret_value(SecretId=pcfg.DB_SECRET_READER)["SecretString"]
    try:
        j = json.loads(raw)
        pw = next(iter(j.values())) if isinstance(j, dict) else raw
    except ValueError:
        pw = raw
    conn = psycopg2.connect(host=pcfg.require_db_host(), port=pcfg.DB_PORT,
                            dbname=pcfg.DB_NAME, user=pcfg.DB_USER_READER,
                            password=pw, sslmode="require")
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM chunks WHERE code_ncg=%s", (code,))
    chunks = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM documents WHERE code_ncg=%s", (code,))
    docs = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM dossiers WHERE code_ncg=%s", (code,))
    dossiers = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM copro_synthese WHERE code_ncg=%s", (code,))
    fiches = cur.fetchone()[0]
    cur.execute("SELECT immatriculation FROM copros WHERE code_ncg=%s", (code,))
    row = cur.fetchone()
    cur.execute("SELECT doc_type, count(*) FROM chunks WHERE code_ncg=%s "
                "GROUP BY doc_type ORDER BY 2 DESC LIMIT 8", (code,))
    types = cur.fetchall()
    conn.close()

    print(f"  chunks     : {chunks}")
    print(f"  documents  : {docs}")
    print(f"  dossiers   : {dossiers}")
    print(f"  fiche 09   : {'oui' if fiches else 'NON (relancer 09_copro_synthese.py)'}")
    print(f"  registre   : {'immat ' + str(row[0]) if row and row[0] else 'absent ou sans immat'}")
    print(f"  doc_types  : {dict(types)}")
    if chunks == 0:
        fail("aucun chunk en base : l'ingestion n'a pas charge (verifier 06b).")
    print("\n  ✅ copro presente en base. Reste : smoke test MCP "
          "(PALIM_list_copros puis PALIM_search_chunks avec copro_codes=[code]).")


def main():
    ap = argparse.ArgumentParser(description="Pre-vol / post-vol d'ajout d'une copro.")
    ap.add_argument("--immat", help="Immatriculation RNIC (toute graphie).")
    ap.add_argument("--code", help="Code interne (clients en regime NCG).")
    ap.add_argument("--folder", help="Nom du dossier source sous raw_root.")
    ap.add_argument("--label", help="Libelle humain (adresse complete).")
    ap.add_argument("--lobby-code", help="Alias de resolution (code interne du syndic).")
    ap.add_argument("--raw-dir", help="Source absolue hors raw_root (share VPN/UNC).")
    ap.add_argument("--check-only", action="store_true", help="Controles seuls, aucune ecriture.")
    ap.add_argument("--verify", action="store_true", help="Recette post-ingestion en base.")
    ap.add_argument("--skip-rnic", action="store_true")
    ap.add_argument("--skip-assynco", action="store_true")
    args = ap.parse_args()

    brut = args.immat or args.code
    if not brut:
        ap.error("--immat ou --code requis.")
    code = canon(brut)
    if args.immat and not is_immatriculation(code):
        ap.error(f"'{args.immat}' n'est pas une immatriculation valide (format AA0000000).")

    if args.verify:
        verify(code)
        return

    if not args.folder:
        ap.error("--folder requis (nom du dossier source).")

    print(f"AJOUT COPRO {display(code)} au profil {pcfg.CLIENT_CODE}")
    check_profil(code, args.folder, args.lobby_code)
    check_source(args.folder, args.raw_dir)
    if not args.skip_rnic:
        check_rnic(code)
    if not args.skip_assynco:
        check_assynco(code, args.folder)
    ecrire_profil(code, args.folder, args.label, args.lobby_code, args.raw_dir, args.check_only)

    print("\n" + "=" * 60)
    if _warnings:
        print(f"{len(_warnings)} avertissement(s) a traiter :")
        for w in _warnings:
            print(f"  - {w}")
    else:
        print("Aucun avertissement.")
    if not args.check_only:
        print("\nSuite :")
        print(f'  1. git diff "Scripts/clients/{pcfg.CLIENT_CODE}/client.json"')
        print(f"  2. DB_PASSWORD=<secret {pcfg.DB_SECRET_ADMIN}> PALIM_CLIENT={pcfg.CLIENT_CODE} "
              f"PYTHONIOENCODING=utf-8 python ingest.py --copro {code} --keep-shards")
        print(f"  3. PALIM_CLIENT={pcfg.CLIENT_CODE} PYTHONIOENCODING=utf-8 "
              f"python add_copro.py --{'immat' if is_immatriculation(code) else 'code'} {code} --verify")


if __name__ == "__main__":
    main()
