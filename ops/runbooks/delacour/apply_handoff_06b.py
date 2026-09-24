"""Applique le handoff 06B (registre copros Delacour) sur le checkout `main`.

Lance depuis le dossier Scripts/ du checkout NCG (ou passe son chemin en argument) :
    python ops/handoffs/apply_handoff_06b.py [chemin/vers/Scripts]

Ce que ça fait (idempotent : relancer ne double rien) :
  C1+C2  06b_load_db.py       : registre copros complet (nom_residence, adresse) et bloquant
  C3     06b_load_db.py       : UPDATE chunks.doc_type <- documents.doc_type_corrige (par copro)
  C1     pipeline_config.py   : registre_row(code, meta=None) — contrat testable sans DB
  C2     tests/recette_fiche_v2.py : invariant I0b (bloquant)
  T      tests/test_06b_registre.py : test unitaire du contrat (créé)
  P      clients/delacour/client.json : label Escudier sans la parenthèse « dossier Drive »

Chaque remplacement exige exactement UNE occurrence de son ancre : sinon arrêt net,
rien n'est écrit pour ce fichier. Les fins de ligne d'origine (CRLF/LF) sont conservées.
"""
import os
import py_compile
import re
import sys

SCRIPTS = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
if not os.path.exists(os.path.join(SCRIPTS, "06b_load_db.py")):
    raise SystemExit(f"❌ 06b_load_db.py introuvable dans {SCRIPTS} — lancer depuis Scripts/ ou passer son chemin.")


def _read(path):
    with open(path, "rb") as f:
        raw = f.read()
    crlf = b"\r\n" in raw
    return raw.decode("utf-8").replace("\r\n", "\n"), crlf


def _write(path, text, crlf):
    if crlf:
        text = text.replace("\n", "\r\n")
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def patch(relpath, edits):
    """edits = [(marqueur_deja_applique, regex_ancre, remplacement_fn)]."""
    path = os.path.join(SCRIPTS, relpath)
    text, crlf = _read(path)
    changed = False
    for marker, pattern, repl in edits:
        if marker in text:
            print(f"  = {relpath} : déjà appliqué ({marker[:40]}…)")
            continue
        hits = list(re.finditer(pattern, text, re.S))
        if len(hits) != 1:
            raise SystemExit(f"❌ {relpath} : ancre trouvée {len(hits)} fois (attendu 1) — "
                             f"fichier différent de la version du 25/08 ? Rien n'a été écrit.\n   ancre : {pattern[:80]}")
        m = hits[0]
        text = text[:m.start()] + repl(m) + text[m.end():]
        changed = True
        print(f"  + {relpath} : {marker[:60]}")
    if changed:
        _write(path, text, crlf)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# pipeline_config.py — C1 : contrat du registre, testable sans DB
# ─────────────────────────────────────────────────────────────────────────────
REGISTRE_ROW_FN = '''

def registre_row(code: str, meta=None) -> tuple:
    """Ligne du registre `copros` écrite par 06b :
    (code_ncg, immatriculation, nom_residence, adresse).

    Contrat (handoff 06B, C1 — relevé Delacour du 21/09/2026) :
    - nom_residence = `label` du profil s'il existe, sinon `folder` : JAMAIS None,
      c'est ce que l'annuaire (PALIM_list_copros) affiche et sur quoi il filtre ;
    - adresse = `label` (chez Delacour le label EST l'adresse postale complète) ;
      None quand le profil n'a pas de label (NCG : annuaire par code, acceptable).
    `meta` (dict du profil) permet de tester le contrat sans profil chargé.
    """
    if meta is None:
        code = resolve(code)
        meta = _COPROS[code]
    label = (meta.get("label") or "").strip() or None
    folder = (meta.get("folder") or "").strip()
    return (code, meta.get("immatriculation") or None, label or folder or code, label)
'''

patch("pipeline_config.py", [(
    "def registre_row(",
    r"\ndef raw_source_dir\(code: str\) -> Path:",
    lambda m: REGISTRE_ROW_FN + m.group(0),
)])

# ─────────────────────────────────────────────────────────────────────────────
# 06b_load_db.py — C3 puis C1+C2
# ─────────────────────────────────────────────────────────────────────────────
C3_BLOCK = '''
    # ── C3 : propager doc_type_corrige (04) aux chunks ──────────────────────────
    # 03 classe par dossier Drive d'origine ; 04 corrige dans documents.doc_type_corrige ;
    # la recherche filtre bien sur COALESCE(doc_type_corrige, doc_type) mais
    # chunks.doc_type (celui des citations) restait figé — le PV du 25/03/2026
    # d'Escudier ressortait en COMPTABILITE (relevé Delacour du 21/09/2026).
    # Restreint à la copro chargée, jamais global. BORDEREAU_AR n'est jamais une
    # valeur corrigée : on ne le touche pas.
    if COPRO:
        cur.execute("""
            UPDATE chunks c
            SET doc_type = d.doc_type_corrige
            FROM documents d
            WHERE d.source_file = c.source_file
              AND c.code_ncg = %s
              AND d.doc_type_corrige IS NOT NULL
              AND d.doc_type_corrige <> c.doc_type
              AND c.doc_type <> 'BORDEREAU_AR'
        """, (COPRO,))
        conn.commit()
        print(f"✅ doc_type réaligné sur doc_type_corrige pour {cur.rowcount} chunk(s) de {COPRO}")
'''

REGISTRE_BLOCK = '''# =====================================================
# Registre copros : identité (libellé, adresse, immatriculation RNIC) — annuaire
# =====================================================
# Upsert depuis le profil client (per-copro : la copro chargée ; legacy : tout le
# profil). Jamais de purge : la table copros survit aux TRUNCATE/DELETE ci-dessus.
# Contrat des lignes : pipeline_config.registre_row (testé par tests/test_06b_registre.py).
# BLOQUANT (C2) : un registre incomplet = copro introuvable par son nom (Escudier) ou
# sans immatriculation (Nocard, ligne jamais écrite le 27/08 — l'échec était avalé).
# Toute erreur arrête 06b avec un code de sortie non nul ; ingest.py (check=True) suit.
_reg_codes = [COPRO] if COPRO else sorted(pcfg.COPRO_META)
_reg_rows = [pcfg.registre_row(c) for c in _reg_codes]
try:
    execute_values(cur, """
        INSERT INTO copros (code_ncg, immatriculation, nom_residence, adresse) VALUES %s
        ON CONFLICT (code_ncg) DO UPDATE SET
            immatriculation = EXCLUDED.immatriculation,
            nom_residence   = EXCLUDED.nom_residence,
            adresse         = COALESCE(EXCLUDED.adresse, copros.adresse)
    """, _reg_rows)
except psycopg2.errors.UndefinedTable:
    conn.rollback()
    print("❌ Table copros absente — lancer 06a_init_db.py pour la créer, puis relancer 06b.")
    raise
conn.commit()
print(f"✅ Registre copros mis à jour ({len(_reg_rows)} ligne(s)) :")
for _code, _immat, _nom, _adresse in _reg_rows:
    print(f"   {_code}  immat={_immat or '—'}  nom={_nom!r}  adresse={_adresse or '—'}")
'''

patch("06b_load_db.py", [
    (
        "C3 : propager doc_type_corrige",
        r'(        """, doc_batch\)\n        conn\.commit\(\)\n)',
        lambda m: m.group(1) + C3_BLOCK,
    ),
    (
        "Registre copros : identité (libellé, adresse, immatriculation RNIC)",
        r"# =+\n# Registre copros : immatriculation RNIC en attribut \(annuaire\)\n.*?"
        r"Registre copros non mis à jour[^\n]*\n",
        lambda m: REGISTRE_BLOCK,
    ),
])

# ─────────────────────────────────────────────────────────────────────────────
# tests/recette_fiche_v2.py — C2 : invariant I0b (bloquant)
# ─────────────────────────────────────────────────────────────────────────────
I0B_BLOCK = '''
    # I0b registre : toute copro ayant des chunks a sa ligne dans copros, avec un
    # nom_residence — sinon elle est introuvable par son nom dans l'annuaire.
    # Bloquant : aurait attrapé Nocard (AJ6978050) le 10/09/2026.
    cur.execute("""SELECT k.code_ncg
                   FROM (SELECT DISTINCT code_ncg FROM chunks WHERE code_ncg IS NOT NULL) k
                   LEFT JOIN copros r ON r.code_ncg = k.code_ncg
                   WHERE r.code_ncg IS NULL OR r.nom_residence IS NULL
                   ORDER BY 1""")
    sans_registre = [r[0] for r in cur.fetchall()]
    check(not sans_registre,
          f"I0b registre : copro(s) avec chunks sans ligne copros/nom_residence : {sans_registre}")
'''

patch(os.path.join("tests", "recette_fiche_v2.py"), [(
    "I0b registre",
    r'(    check\(len\(fiches\) == n_copros,\n\s+f"I0 couverture : \{len\(fiches\)\} fiches pour \{n_copros\} copros", dur=False\)\n)',
    lambda m: m.group(1) + I0B_BLOCK,
)])

# ─────────────────────────────────────────────────────────────────────────────
# clients/delacour/client.json — label Escudier propre (le dossier Drive est déjà
# dans `folder`, la parenthèse polluerait nom_residence/adresse dans l'annuaire)
# ─────────────────────────────────────────────────────────────────────────────
patch(os.path.join("clients", "delacour", "client.json"), [(
    '"label": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt"}',
    r'"label": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt \(dossier Drive \'SDC - 92100\'\)"\}',
    lambda m: '"label": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt"}',
)])

# ─────────────────────────────────────────────────────────────────────────────
# tests/test_06b_registre.py — fige le contrat (sans DB)
# ─────────────────────────────────────────────────────────────────────────────
TEST_FILE = '''"""Contrat du registre copros écrit par 06b (handoff 06B, C1) — sans DB.

Lance : python tests/test_06b_registre.py   (ou pytest tests/test_06b_registre.py)
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import pipeline_config as pcfg  # noqa: E402


def test_label_present_donne_nom_et_adresse():
    meta = {"folder": "SDC - 92100", "immatriculation": "AH7171655",
            "label": "SDC 67 rue Escudier, 92100 Boulogne-Billancourt"}
    assert pcfg.registre_row("AH7171655", meta) == (
        "AH7171655", "AH7171655",
        "SDC 67 rue Escudier, 92100 Boulogne-Billancourt",
        "SDC 67 rue Escudier, 92100 Boulogne-Billancourt")


def test_label_absent_retombe_sur_folder_sans_adresse():
    meta = {"folder": "5390 - 2-6 BIS HENRI TARIEL", "immatriculation": "AB8635559"}
    assert pcfg.registre_row("5390", meta) == (
        "5390", "AB8635559", "5390 - 2-6 BIS HENRI TARIEL", None)


def test_label_blanc_compte_comme_absent():
    meta = {"folder": "X", "label": "   ", "immatriculation": None}
    assert pcfg.registre_row("0001", meta) == ("0001", None, "X", None)


def test_profil_courant_jamais_sans_nom():
    # Sur le profil chargé (PALIM_CLIENT) : nom_residence jamais vide, code inchangé.
    for code in pcfg.COPRO_META:
        row = pcfg.registre_row(code)
        assert row[0] == code and row[2], row


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"{len(fns)} test(s) OK — client={pcfg.CLIENT_CODE}")
'''

_test_path = os.path.join(SCRIPTS, "tests", "test_06b_registre.py")
if os.path.exists(_test_path):
    print("  = tests/test_06b_registre.py : existe déjà")
else:
    with open(_test_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(TEST_FILE)
    print("  + tests/test_06b_registre.py : créé")

# ─────────────────────────────────────────────────────────────────────────────
# Vérification syntaxique
# ─────────────────────────────────────────────────────────────────────────────
for rel in ("06b_load_db.py", "pipeline_config.py", os.path.join("tests", "recette_fiche_v2.py"),
            os.path.join("tests", "test_06b_registre.py")):
    py_compile.compile(os.path.join(SCRIPTS, rel), doraise=True)
    print(f"  ✓ py_compile {rel}")
print("\nPatch appliqué. Suite : python tests/test_06b_registre.py, puis le canari (cf. RUNBOOK_06B.md).")
