"""
ÉTAPE 4b — Extraction métadonnées document-level via Haiku
Lit chunks_enrichis.jsonl (sortie de l'étape 4), agrège par source_file, extrait metadata via LLM.
Fenêtre de lecture adaptative : en-tête seul ou tête+queue selon le doc_type.
Sortie : documents_metadata.jsonl (1 ligne JSON par document source)
Lance : python 04b_metadata_documents.py
"""
import json
import os
import re
import boto3
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_enrichis.jsonl"     # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\documents_metadata.jsonl"  # ← MODIFIER
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRIPT_DIR, "metadata_cache.json")
AWS_REGION = "eu-west-1"
LLM_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

# Types nécessitant une lecture tête+queue (date/statut/montant souvent en fin de document)
TETE_QUEUE_TYPES = {"CONTRAT", "BUDGET", "COMPTABILITE", "SINISTRE", "ASSURANCE"}


# =====================================================
# Agrégation par source_file
# =====================================================
print("=" * 60)
print("  EXTRACTION MÉTADONNÉES DOCUMENT-LEVEL VIA HAIKU")
print("=" * 60)

print("\nChargement et agrégation des chunks par document source...")
docs = {}  # source_file → {copropriete, doc_type, nom_fichier, chunks: [...]}

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        chunk = json.loads(line)
        sf = chunk["source_file"]
        if sf not in docs:
            docs[sf] = {
                "copropriete": chunk["copropriete"],
                "doc_type": chunk["doc_type"],
                "nom_fichier": chunk["nom_fichier"],
                "total_chunks": chunk.get("total_chunks", 1),
                "chunks": []
            }
        docs[sf]["chunks"].append({
            "text": chunk["text"],
            "chunk_index": chunk["chunk_index"]
        })

print(f"  → {len(docs)} documents sources identifiés")

# Répartition par doc_type
type_counts = {}
for d in docs.values():
    dt = d["doc_type"]
    type_counts[dt] = type_counts.get(dt, 0) + 1
print(f"  Répartition : {dict(sorted(type_counts.items(), key=lambda x: -x[1]))}")
tq_count = sum(1 for d in docs.values() if d["doc_type"] in TETE_QUEUE_TYPES)
print(f"  → {tq_count} documents en mode tête+queue, {len(docs) - tq_count} en mode en-tête seul")


# =====================================================
# Fenêtre de lecture adaptative
# =====================================================
def build_extraction_window(chunks, doc_type):
    """Construit la fenêtre de texte pour l'extraction metadata Haiku.
    - En-tête seul (2000 chars) pour la majorité des doc_types
    - Tête+queue (1500+1500 chars) pour CONTRAT, BUDGET, COMPTABILITE, SINISTRE, ASSURANCE
    """
    all_text = [c["text"] for c in sorted(chunks, key=lambda x: x["chunk_index"])]

    if doc_type in TETE_QUEUE_TYPES:
        # Tête : concat depuis le début jusqu'à ~1500 chars
        head = ""
        for t in all_text:
            if len(head) + len(t) > 1500:
                head += t[:1500 - len(head)]
                break
            head += t + "\n"

        # Queue : concat depuis la fin jusqu'à ~1500 chars
        tail = ""
        for t in reversed(all_text):
            if len(tail) + len(t) > 1500:
                tail = t[-(1500 - len(tail)):] + "\n" + tail
                break
            tail = t + "\n" + tail

        return head.strip() + "\n\n[...]\n\n" + tail.strip()
    else:
        # En-tête seul : ~2000 premiers chars
        window = ""
        for t in all_text:
            if len(window) + len(t) > 2000:
                window += t[:2000 - len(window)]
                break
            window += t + "\n"
        return window.strip()


# =====================================================
# Extraction Haiku
# =====================================================
METADATA_PROMPT = """Tu es un assistant spécialisé en gestion de copropriété.
Extrais les métadonnées de ce document. Le type de document ACTUEL est : {doc_type}.
Ce type a été déterminé automatiquement par le dossier parent, il peut être INCORRECT.

Réponds UNIQUEMENT par un objet JSON valide, sans commentaire ni markdown :
{{
  "doc_type_corrige": "Le vrai type basé sur le CONTENU du document, parmi : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, AUTRE. Exemples : une convocation → COURRIER, un contrat syndic → CONTRAT, un règlement intérieur → RCP, un ordre du jour seul → COURRIER, une liste de copropriétaires → AUTRE, un guide résidence → AUTRE",
  "date_document": "YYYY-MM-DD. Si seuls l'année et le mois sont trouvés → YYYY-MM-01. Si seule l'année est trouvée → YYYY-01-01. Si l'année est introuvable ou incertaine → null. Ne JAMAIS inventer une date ou un jour absent du document.",
  "annee": 2024,
  "sous_type": "catégorie précise ou null (exemples : MRI, DDE, RAVALEMENT, ASCENSEUR, CHAUFFAGE, TOITURE, MENAGE, GARDIENNAGE, DIGICODE, PLOMBERIE, ELECTRICITE, ETANCHEITE, FACADE, HONORAIRES, PRIME, SYNDIC, CONVOCATION, ORDRE_DU_JOUR)",
  "parties_concernees": ["nom entreprise", "assureur", "expert"] ou [],
  "statut": "actif|expire|resilie|cloture|en_cours|null",
  "montant_principal": 12500.00,
  "resume_une_ligne": "Description courte du document"
}}

Règles pour doc_type_corrige :
- Un procès-verbal d'assemblée générale (PV, compte-rendu de votes, résolutions) → PV_AG
- Une convocation à l'AG, un ordre du jour, une feuille de présence, une procuration → COURRIER
- Un contrat (syndic, maintenance, assurance, prestation) → CONTRAT
- Un règlement intérieur, règlement de copropriété → RCP
- Un rapport du conseil syndical → AUTRE
- Un guide pratique, liste de copropriétaires, annexe diverse → AUTRE
- Un devis, une facture, un budget → DEVIS, FACTURE, BUDGET respectivement
- Une annexe au contrat → CONTRAT

Règles pour le statut :
- Date d'échéance future → "actif"
- Date d'échéance passée → "expire"
- Mentions "résilié", "résiliation", "annulé" → "resilie"
- Mentions "clos", "clôturé", "indemnisé" → "cloture"
- Dossier en cours de traitement → "en_cours"
- Impossible à déterminer → null

Texte du document :
{texte}"""

bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

# Cache pour reprises
cache = {}
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    print(f"  → {len(cache)} documents en cache (reprise)")

stats = {"llm_calls": 0, "cache_hits": 0, "errors": 0, "too_short": 0}


def extract_metadata(source_file, doc_type, texte):
    """Appelle Haiku pour extraire les métadonnées. Utilise le cache si disponible."""
    if source_file in cache:
        stats["cache_hits"] += 1
        return cache[source_file]

    if len(texte.strip()) < 100:
        stats["too_short"] += 1
        result = {
            "date_document": None, "annee": None, "sous_type": None,
            "parties_concernees": [], "statut": None,
            "montant_principal": None, "resume_une_ligne": None
        }
        cache[source_file] = result
        return result

    prompt = METADATA_PROMPT.format(doc_type=doc_type, texte=texte[:3500])

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    })

    try:
        response = bedrock.invoke_model(
            modelId=LLM_MODEL, body=body,
            contentType="application/json", accept="application/json"
        )
        result_text = json.loads(response["body"].read())["content"][0]["text"].strip()

        # Nettoyage : enlever les éventuels ``` markdown
        result_text = re.sub(r"^```json?\s*", "", result_text)
        result_text = re.sub(r"\s*```$", "", result_text)

        metadata = json.loads(result_text)
        stats["llm_calls"] += 1
        cache[source_file] = metadata
        return metadata

    except json.JSONDecodeError as e:
        stats["errors"] += 1
        print(f"  ⚠️ JSON invalide {source_file}: {e}")
        fallback = {
            "date_document": None, "annee": None, "sous_type": None,
            "parties_concernees": [], "statut": None,
            "montant_principal": None, "resume_une_ligne": None
        }
        cache[source_file] = fallback
        return fallback

    except Exception as e:
        stats["errors"] += 1
        err_str = str(e)

        # Throttling → retry simple (1 fois)
        if "ThrottlingException" in err_str:
            import time
            time.sleep(2)
            try:
                response = bedrock.invoke_model(
                    modelId=LLM_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
                result_text = re.sub(r"^```json?\s*", "", result_text)
                result_text = re.sub(r"\s*```$", "", result_text)
                metadata = json.loads(result_text)
                stats["llm_calls"] += 1
                stats["errors"] -= 1  # annuler l'erreur comptée
                cache[source_file] = metadata
                return metadata
            except Exception:
                pass

        print(f"  ⚠️ Erreur {source_file}: {e}")
        fallback = {
            "date_document": None, "annee": None, "sous_type": None,
            "parties_concernees": [], "statut": None,
            "montant_principal": None, "resume_une_ligne": None
        }
        cache[source_file] = fallback
        return fallback


# =====================================================
# Exécution
# =====================================================
print(f"\nExtraction des métadonnées pour {len(docs)} documents...\n")

with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for source_file, doc_info in tqdm(docs.items(), desc="Metadata"):
        texte = build_extraction_window(doc_info["chunks"], doc_info["doc_type"])
        metadata = extract_metadata(source_file, doc_info["doc_type"], texte)

        record = {
            "source_file": source_file,
            "copropriete": doc_info["copropriete"],
            "nom_fichier": doc_info["nom_fichier"],
            "doc_type": doc_info["doc_type"],
            "total_chunks": doc_info["total_chunks"],
            "premier_texte": doc_info["chunks"][0]["text"][:500] if doc_info["chunks"] else "",
            **metadata
        }
        fout.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Sauvegarder le cache tous les 50 documents
        if (stats["llm_calls"] + stats["cache_hits"]) % 50 == 0:
            with open(CACHE_FILE, "w", encoding="utf-8") as fc:
                json.dump(cache, fc, ensure_ascii=False)

# Sauvegarde finale du cache
with open(CACHE_FILE, "w", encoding="utf-8") as fc:
    json.dump(cache, fc, ensure_ascii=False)


# =====================================================
# Rapport
# =====================================================
all_records = []
with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
    for line in f:
        all_records.append(json.loads(line))

with_date = sum(1 for r in all_records if r.get("date_document"))
with_sous_type = sum(1 for r in all_records if r.get("sous_type"))
with_statut = sum(1 for r in all_records if r.get("statut"))
with_montant = sum(1 for r in all_records if r.get("montant_principal"))
reclassified = sum(1 for r in all_records if r.get("doc_type_corrige") and r["doc_type_corrige"] != r["doc_type"])

print("\n" + "=" * 60)
print("RAPPORT D'EXTRACTION METADATA")
print("=" * 60)
print(f"\nDocuments traités : {len(all_records)}")
print(f"  Appels Haiku : {stats['llm_calls']}")
print(f"  Cache hits   : {stats['cache_hits']}")
print(f"  Trop courts  : {stats['too_short']}")
print(f"  Erreurs      : {stats['errors']}")
print(f"\nCouverture des champs :")
print(f"  date_document    : {with_date}/{len(all_records)} ({100*with_date/len(all_records):.0f}%)")
print(f"  sous_type        : {with_sous_type}/{len(all_records)} ({100*with_sous_type/len(all_records):.0f}%)")
print(f"  statut           : {with_statut}/{len(all_records)} ({100*with_statut/len(all_records):.0f}%)")
print(f"  montant_principal: {with_montant}/{len(all_records)} ({100*with_montant/len(all_records):.0f}%)")

# Reclassification doc_type
print(f"\n📊 Reclassification doc_type par Haiku :")
print(f"  {reclassified}/{len(all_records)} documents reclassifiés ({100*reclassified/len(all_records):.0f}%)")
reclass_details = {}
for r in all_records:
    orig = r["doc_type"]
    corr = r.get("doc_type_corrige") or orig
    if orig != corr:
        key = f"{orig} → {corr}"
        reclass_details[key] = reclass_details.get(key, 0) + 1
if reclass_details:
    for key, count in sorted(reclass_details.items(), key=lambda x: -x[1]):
        print(f"    {key:35s} : {count}")

# Répartition doc_type_corrige
corr_types = {}
for r in all_records:
    ct = r.get("doc_type_corrige") or r["doc_type"]
    corr_types[ct] = corr_types.get(ct, 0) + 1
print(f"\nRépartition doc_type_corrige (post-Haiku) :")
for ct, count in sorted(corr_types.items(), key=lambda x: -x[1]):
    print(f"  {ct:25s} : {count}")

# Répartition sous-types
sous_types = {}
for r in all_records:
    st_val = r.get("sous_type") or "null"
    sous_types[st_val] = sous_types.get(st_val, 0) + 1
print(f"\nRépartition sous-types :")
for st_val, count in sorted(sous_types.items(), key=lambda x: -x[1]):
    print(f"  {st_val:25s} : {count}")

# Répartition statuts
statuts = {}
for r in all_records:
    s = r.get("statut") or "null"
    statuts[s] = statuts.get(s, 0) + 1
print(f"\nRépartition statuts :")
for s, count in sorted(statuts.items(), key=lambda x: -x[1]):
    print(f"  {s:25s} : {count}")

print(f"\n📁 Métadonnées : {OUTPUT_FILE}")

# Coût estimé
cost = stats["llm_calls"] * 1000 * 0.80 / 1_000_000  # ~1000 tokens input, $0.80/MTok
print(f"💰 Coût estimé Haiku : ${cost:.2f}")
