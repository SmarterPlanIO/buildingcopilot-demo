"""
Diagnostic qualité des métadonnées extraites par Haiku (étape 04).
Lance : python diag_metadata.py
"""
import json
import os

METADATA = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\documents_metadata.jsonl"
CHUNKS = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"

records = []
with open(METADATA, "r", encoding="utf-8") as f:
    for line in f:
        records.append(json.loads(line))

print("=" * 70)
print(f"  DIAGNOSTIC MÉTADONNÉES — {len(records)} documents")
print("=" * 70)

# ── 1. Couverture des champs ──
with_date = sum(1 for r in records if r.get("date_document"))
with_annee = sum(1 for r in records if r.get("annee"))
with_sous_type = sum(1 for r in records if r.get("sous_type"))
with_statut = sum(1 for r in records if r.get("statut"))
with_montant = sum(1 for r in records if r.get("montant_principal"))
with_corrige = sum(1 for r in records if r.get("doc_type_corrige"))
reclassified = sum(1 for r in records if r.get("doc_type_corrige") and r["doc_type_corrige"] != r["doc_type"])

print(f"\n📊 Couverture des champs :")
print(f"  date_document    : {with_date}/{len(records)} ({100*with_date/len(records):.0f}%)")
print(f"  annee            : {with_annee}/{len(records)} ({100*with_annee/len(records):.0f}%)")
print(f"  sous_type        : {with_sous_type}/{len(records)} ({100*with_sous_type/len(records):.0f}%)")
print(f"  statut           : {with_statut}/{len(records)} ({100*with_statut/len(records):.0f}%)")
print(f"  montant_principal: {with_montant}/{len(records)} ({100*with_montant/len(records):.0f}%)")
print(f"  doc_type_corrige : {with_corrige}/{len(records)} ({100*with_corrige/len(records):.0f}%)")
print(f"  reclassifiés     : {reclassified}/{len(records)} ({100*reclassified/len(records):.0f}%)")

# ── 2. Répartition doc_type avant/après correction ──
orig_types = {}
corr_types = {}
for r in records:
    orig = r["doc_type"]
    corr = r.get("doc_type_corrige") or orig
    orig_types[orig] = orig_types.get(orig, 0) + 1
    corr_types[corr] = corr_types.get(corr, 0) + 1

print(f"\n📋 Répartition doc_type AVANT vs APRÈS correction Haiku :")
print(f"  {'Type':20s} {'Avant':>6s} {'Après':>6s} {'Delta':>7s}")
print(f"  {'-'*20} {'-'*6} {'-'*6} {'-'*7}")
all_types = sorted(set(list(orig_types.keys()) + list(corr_types.keys())))
for t in all_types:
    avant = orig_types.get(t, 0)
    apres = corr_types.get(t, 0)
    delta = apres - avant
    arrow = f"+{delta}" if delta > 0 else str(delta) if delta < 0 else "="
    print(f"  {t:20s} {avant:6d} {apres:6d} {arrow:>7s}")

# ── 3. Détail des reclassifications ──
reclass_details = {}
for r in records:
    orig = r["doc_type"]
    corr = r.get("doc_type_corrige") or orig
    if orig != corr:
        key = f"{orig} → {corr}"
        reclass_details[key] = reclass_details.get(key, 0) + 1

if reclass_details:
    print(f"\n🔄 Reclassifications détaillées :")
    for key, count in sorted(reclass_details.items(), key=lambda x: -x[1]):
        print(f"    {key:35s} : {count}")

# ── 4. Vérifications ciblées ──

# 4a. Contrats syndic : doc_type_corrige = CONTRAT + sous_type = SYNDIC ?
contrats_syndic = [r for r in records if "contrat" in r["nom_fichier"].lower() and "syndic" in r["nom_fichier"].lower()]
print(f"\n🔍 Contrats syndic (nom fichier contient 'contrat' + 'syndic') : {len(contrats_syndic)}")
for r in contrats_syndic[:10]:
    corr = r.get("doc_type_corrige") or r["doc_type"]
    st = r.get("sous_type") or "-"
    ok = "✅" if corr == "CONTRAT" and st == "SYNDIC" else "⚠️"
    print(f"  {ok} {corr:10s} [{st:10s}] {r['nom_fichier']}")

# 4b. Convocations : doc_type_corrige = COURRIER ?
convocations = [r for r in records if "convoc" in r["nom_fichier"].lower()]
print(f"\n🔍 Convocations (nom fichier contient 'convoc') : {len(convocations)}")
for r in convocations[:10]:
    corr = r.get("doc_type_corrige") or r["doc_type"]
    st = r.get("sous_type") or "-"
    ok = "✅" if corr == "COURRIER" else "⚠️"
    print(f"  {ok} {corr:10s} [{st:15s}] {r['nom_fichier']}")

# 4c. Règlements intérieurs : doc_type_corrige = RCP ?
reglements = [r for r in records if "reglement" in r["nom_fichier"].lower() and "interieur" in r["nom_fichier"].lower().replace("é", "e")]
print(f"\n🔍 Règlements intérieurs (nom fichier) : {len(reglements)}")
for r in reglements[:10]:
    corr = r.get("doc_type_corrige") or r["doc_type"]
    ok = "✅" if corr == "RCP" else "⚠️"
    print(f"  {ok} {corr:10s} {r['nom_fichier']}")

# 4d. Factures avec sinistre : le resume mentionne-t-il sinistre/dégât ?
factures = [r for r in records if (r.get("doc_type_corrige") or r["doc_type"]) == "FACTURE"]
factures_sinistre = [r for r in factures if r.get("resume_une_ligne") and
                     any(kw in r["resume_une_ligne"].lower() for kw in ["sinistre", "dégât", "degat", "dégorgement", "fuite"])]
print(f"\n🔍 Factures liées à un sinistre (via resume) : {len(factures_sinistre)}/{len(factures)} factures")
for r in factures_sinistre[:10]:
    mt = r.get("montant_principal") or "?"
    print(f"  💰 {mt:>10}€  {r['nom_fichier']}")
    print(f"              → {r.get('resume_une_ligne', '-')}")

# 4e. Documents avec montant : top 10
with_montant_list = [(r, r["montant_principal"]) for r in records if r.get("montant_principal")]
with_montant_list.sort(key=lambda x: -x[1])
print(f"\n💰 Top 10 documents par montant :")
for r, mt in with_montant_list[:10]:
    corr = r.get("doc_type_corrige") or r["doc_type"]
    print(f"  {mt:>12,.2f}€  [{corr:12s}] {r['nom_fichier']}")

# 4f. Facture Jean Lucy / Mesika — le cas test
mesika = [r for r in records if "20180503131754" in r.get("source_file", "") or "mesika" in r.get("resume_une_ligne", "").lower()]
print(f"\n🔍 Cas test : facture Jean Lucy / Mesika")
if mesika:
    for r in mesika:
        corr = r.get("doc_type_corrige") or r["doc_type"]
        print(f"  doc_type_corrige : {corr}")
        print(f"  date_document    : {r.get('date_document')}")
        print(f"  sous_type        : {r.get('sous_type')}")
        print(f"  statut           : {r.get('statut')}")
        print(f"  montant          : {r.get('montant_principal')}")
        print(f"  resume           : {r.get('resume_une_ligne')}")
else:
    print("  ⚠️ Non trouvé dans les métadonnées")

# ── 5. Chunk intégrité — vérifier que le fix chunk_whole_document fonctionne ──
if os.path.exists(CHUNKS):
    print(f"\n📦 Vérification intégrité chunks (fix chunk_whole_document) :")
    chunk_docs = {}
    with open(CHUNKS, "r", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            sf = c["source_file"]
            if sf not in chunk_docs:
                chunk_docs[sf] = {"chunks": 0, "total_chars": 0, "doc_type": c["doc_type"]}
            chunk_docs[sf]["chunks"] += 1
            chunk_docs[sf]["total_chars"] += len(c["text"])

    # Docs courts (<5000) qui ont quand même plusieurs chunks → le fix n'a pas marché
    WHOLE_DOC_TYPES = {"FACTURE", "DEVIS", "BUDGET", "COURRIER", "COMPTABILITE", "PLAN"}
    problematic = []
    for sf, d in chunk_docs.items():
        if d["doc_type"] in WHOLE_DOC_TYPES and d["total_chars"] < 5000 and d["chunks"] > 1:
            problematic.append((sf, d))

    if problematic:
        print(f"  ⚠️ {len(problematic)} docs courts (<5000 chars) encore multi-chunks :")
        for sf, d in problematic[:10]:
            print(f"    {d['chunks']} chunks  {d['total_chars']} chars  {os.path.basename(sf)}")
    else:
        print(f"  ✅ Tous les docs courts en chunk_whole_document sont bien en 1 chunk")

    # Cas test Mesika
    mesika_chunks = {sf: d for sf, d in chunk_docs.items() if "20180503131754" in sf}
    if mesika_chunks:
        for sf, d in mesika_chunks.items():
            status = "✅" if d["chunks"] == 1 else f"⚠️ {d['chunks']} chunks"
            print(f"  Facture Mesika : {d['total_chars']} chars, {status}")
