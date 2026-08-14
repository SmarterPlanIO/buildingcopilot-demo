"""Diagnostic COPRO_FILTERS — vérifie le format {Name} des sinistres Airtable.

But : trancher entre filtre précis FIND("(CODE)") vs OR(code, alias) par copro.
Pour chaque code NCG, compare :
  - n_paren   : sinistres dont {Name} contient "(CODE)"  (format canonique)
  - n_loose   : sinistres dont {Name} contient "CODE"     (peut inclure faux positifs sur réfs)
  - alias_only: sinistres matchés par un alias mot-clé mais SANS "(CODE)"
                → à l'œil : anciens sinistres légitimes ou faux positifs ?

Usage : AIRTABLE_PAT="pat..." python diag_copro_filters.py
Lecture seule. Aucune écriture Airtable/DB.
"""
import json
import os
import urllib.request
import urllib.parse

PAT = os.environ.get("AIRTABLE_PAT", "")
BASE = os.environ.get("AIRTABLE_BASE_ID", "appi1ee5p93EBHtLR")
TABLE = os.environ.get("AIRTABLE_TABLE_ID", "tblvvkhcHZjDyHLdp")  # Sinistre

# (code_ncg, [alias keywords]) — alias tels qu'actuellement dans 08_airtable_sync.py
COPROS = {
    "5033": ["TORCY"],
    "5354": ["UNIVERSITE"],
    "5390": ["TIVOLI", "TARIEL"],
    "5427": ["CRESSON"],
    "5480": ["STADE", "EBOUE"],
    "5499": ["GUILLEMIN"],
    "5548": ["HOCHE", "MESSINE", "HAUSSMANN"],
    "5553": ["FREGATES", "JAURES"],
    "8030": ["PATAY"],
    "8050": ["STYLE"],
}


def fetch_all_names():
    """Récupère le champ Name de tous les sinistres (paginé)."""
    names = []
    offset = None
    while True:
        params = [("fields[]", "Name"), ("pageSize", "100")]
        if offset:
            params.append(("offset", offset))
        url = f"https://api.airtable.com/v0/{BASE}/{TABLE}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {PAT}"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for rec in data.get("records", []):
            nm = rec.get("fields", {}).get("Name", "")
            if isinstance(nm, dict):
                nm = nm.get("name") or nm.get("text") or json.dumps(nm, ensure_ascii=False)
            if nm:
                names.append(str(nm))
        offset = data.get("offset")
        if not offset:
            break
    return names


def main():
    if not PAT:
        raise SystemExit("❌ AIRTABLE_PAT manquant. Lance : AIRTABLE_PAT=\"pat...\" python diag_copro_filters.py")

    names = fetch_all_names()
    print(f"📊 {len(names)} sinistres au total dans la base\n")

    # Combien de sinistres ont une parenthèse (CODE) du tout ?
    with_paren = sum(1 for n in names if "(" in n and ")" in n)
    print(f"Sinistres avec parenthèse '(...)' dans Name : {with_paren}/{len(names)} "
          f"({100 * with_paren / len(names):.0f}%)\n")
    print("=" * 78)

    for code, aliases in COPROS.items():
        paren = [n for n in names if f"({code})" in n]
        loose = [n for n in names if code in n]
        loose_not_paren = [n for n in loose if n not in paren]

        alias_hits = []
        for n in names:
            up = n.upper()
            if any(a in up for a in aliases) and f"({code})" not in n:
                alias_hits.append(n)

        print(f"\n### {code}  (alias: {', '.join(aliases)})")
        print(f"  FIND(\"({code})\")  → {len(paren):>3} sinistres")
        print(f"  FIND(\"{code}\")    → {len(loose):>3} sinistres "
              f"({len(loose_not_paren)} sans parenthèse — faux positifs réf ?)")
        if loose_not_paren:
            for n in loose_not_paren[:6]:
                print(f"        loose+ : {n[:90]}")
        print(f"  alias sans ({code}) → {len(alias_hits)} sinistres :")
        for n in alias_hits[:10]:
            print(f"        alias  : {n[:90]}")

    print("\n" + "=" * 78)
    print("Lecture : si 'alias sans (CODE)' liste des sinistres clairement d'AUTRES")
    print("immeubles → l'alias est dangereux (faux positif). S'ils appartiennent bien")
    print("à la copro mais sans code → garder l'alias (ancien format légitime).")


if __name__ == "__main__":
    main()
