"""
ÉTAPE 4 — Extraction métadonnées document-level via Haiku
Lit chunks_copro.jsonl (sortie de l'étape 3), agrège par source_file, extrait metadata via LLM.
Fenêtre de lecture adaptative : en-tête seul ou tête+queue selon le doc_type.
Sortie : documents_metadata.jsonl (1 ligne JSON par document source)
Lance : python 04_metadata_documents.py
"""
import json
import os
import re
import time
import unicodedata
import boto3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_copro.jsonl"     # ← MODIFIER
OUTPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\documents_metadata.jsonl"  # ← MODIFIER
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRIPT_DIR, "metadata_cache.json")
AWS_REGION = "eu-west-1"
LLM_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

# Parallélisme
MAX_WORKERS = 10        # Workers parallèles — baisser à 5 si beaucoup de ThrottlingException
MAX_RETRIES = 3         # Retries par document avant abandon

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
ATTENTION : si doc_type=RCP et le document est un ACTE NOTARIÉ relatif au règlement de copropriété (état descriptif de division, acte modificatif/rectificatif du RCP), le type RCP est CORRECT — ne le changer en MUTATION que s'il s'agit réellement d'une vente ou mutation de lot.

Réponds UNIQUEMENT par un objet JSON valide, sans commentaire ni markdown :
{{
  "doc_type_corrige": "Le vrai type basé sur le CONTENU du document, parmi : RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, MUTATION, PLAN, AUTRE. Exemples : une convocation → COURRIER, un contrat syndic → CONTRAT, un règlement intérieur → RCP, un ordre du jour seul → COURRIER, une liste de copropriétaires → AUTRE, un guide résidence → AUTRE, un état daté → MUTATION, un plan technique → PLAN",
  "date_document": "YYYY-MM-DD. Si seuls l'année et le mois sont trouvés → YYYY-MM-01. Si seule l'année est trouvée → YYYY-01-01. Si l'année est introuvable ou incertaine → null. Ne JAMAIS inventer une date ou un jour absent du document.",
  "annee": 2024,
  "sous_type": "Catégorie précise en UN MOT-CLÉ canonique, ou null — voir règles ci-dessous",
  "parties_concernees": ["nom entreprise", "assureur", "expert"] ou [],
  "statut": "actif|expire|resilie|cloture|en_cours|null",
  "montant_principal": 12500.00,
  "dossier_lie": "SINISTRE|TRAVAUX|CONTENTIEUX|null — le dossier transversal auquel ce document est rattaché, même si le doc_type est différent (ex: une FACTURE liée à un SINISTRE, un DEVIS lié à des TRAVAUX, un COURRIER lié à un CONTENTIEUX)",
  "resume_une_ligne": "Description courte du document"
}}

Règles pour dossier_lie :
- Un document peut avoir un doc_type (FACTURE, DEVIS, COURRIER, COMPTABILITE...) tout en étant lié à un dossier transversal
- SINISTRE : le texte mentionne un sinistre, dégât des eaux, constat, expertise, indemnisation, dégorgement, fuite
- TRAVAUX : le texte concerne des travaux votés en AG, un chantier, une réfection, un ravalement
- CONTENTIEUX : le texte concerne un impayé, une mise en demeure, une procédure judiciaire, un recouvrement
- Si le doc_type_corrige est déjà SINISTRE, ENTRETIEN ou DIAGNOSTIC → dossier_lie = null (redondant)
- Si aucun dossier transversal identifiable → null

Règles pour sous_type — CONVENTIONS DE NOMMAGE :
- Format : MAJUSCULE, un seul terme, underscores si besoin, PAS d'accents
- TOUJOURS AU SINGULIER : BALCON (pas BALCONS), ASCENSEUR (pas ASCENSEURS), EXTINCTEUR (pas EXTINCTEURS)
- UN SEUL sous_type par document (le principal, celui du titre ou de l'objet). Jamais de liste séparée par virgules.
- Utiliser le terme MÉTIER LE PLUS COURT et courant du domaine syndic/copro.
- REGROUPER sous le terme générique le plus simple. Exemples :
  ✅ DDE (pas DEGAT_DES_EAUX, DÉGÂT_DES_EAUX, DOMMAGES_DES_EAUX, FUITE, INFILTRATIONS)
  ✅ MRI (pas MULTIRISQUE_IMMEUBLE)
  ✅ SYNDIC (pas CONTRAT_DE_SYNDIC)
  ✅ MUTATION (pas PRE_ETAT_DATE, QUESTIONNAIRE_ACQUISITION, AVIS_DE_MUTATION, VENTE_IMMOBILIERE, ACTE_NOTARIE)
  ✅ CHAUFFAGE (pas CHAUDIERE, CALORIFIQUE, CVC, CLIMATISATION)
  ✅ DIGICODE (pas CONTROLE_ACCES, INTERPHONE, SERRURERIE, VIGIK, BADGE_VIGIK, CYLINDRE)
  ✅ DERATISATION (pas RONGEURS, NUISIBLES)
  ✅ VMC (pas EXTRACTION, DESENFUMAGE, EXTRACTEUR, VENTILATION)
  ✅ PARKING (pas STATIONNEMENT, VELOS, BORNES_RECHARGE, BORNE_RECHARGE, IRVE)
  ✅ COMPTE_GESTION (pas COMPTE_DE_GESTION, COMPTES_ANNUELS)
  ✅ FONDS_TRAVAUX (pas FONDS_ALUR, FDS_ALUR, FONDS_DE_TRAVAUX)
  ✅ CHARGES (pas DECOMPTE_CHARGES, SOLDES_COPROPRIÉTAIRES, CHARGES_COMMUNES, APPEL_FONDS)
  ✅ ESPACES_VERTS (pas ELAGAGE, ABATTAGE, ENGAZONNEMENT, ARBORICULTURE, PAYSAGER)
  ✅ FERME_PORTE (pas FERMEPORTE, GROOM)
  ✅ PORTE_AUTOMATIQUE (pas PORTES_AUTOMATIQUES, PORTES, FERMETURE, FERMETURES)
  ✅ RELEVAGE (pas POMPES_RELEVAGE, RELEVAGE_EAUX, POMPE_RELEVAGE)
  ✅ COMPTEUR (pas COMPTEURS, COMPTAGE, COMPTAGE_EAU, RELEVE_COMPTEURS)
  ✅ SECURITE_INCENDIE (pas BAES, SSI, COLONNE_SECHE, DESENFUMAGE, EXTINCTEUR)
  ✅ ETANCHEITE (pas INFILTRATIONS, COUVERTURE, TOITURE quand c'est un problème d'étanchéité)
- Si le document porte sur un sujet non couvert par les exemples ci-dessus, crée un terme court suivant les mêmes conventions
- Si impossible à déterminer → null

Règles pour doc_type_corrige — UNIQUEMENT une de ces valeurs exactes :
  RCP, PV_AG, CONTRAT, DEVIS, FACTURE, BUDGET, DIAGNOSTIC, COURRIER, SINISTRE, COMPTABILITE, ENTRETIEN, ASSURANCE, MUTATION, PLAN, AUTRE
- PV_AG = procès-verbal d'assemblée générale UNIQUEMENT. Un PV_AG contient obligatoirement les RÉSULTATS de votes (tantièmes pour/contre/abstention, "résolution adoptée/rejetée"). Sans résultats de votes → ce n'est PAS un PV_AG.
- COURRIER = convocation à l'AG, ordre du jour, feuille de présence, procuration, projet de résolutions. ATTENTION : un ORDRE DU JOUR n'est JAMAIS un PV_AG même s'il liste des projets de résolutions — un OJ contient "il sera proposé de voter..." ou liste les résolutions SANS résultats de vote.
- Un brouillon ou projet de PV ("projet PV", "baze PV") reste PV_AG s'il contient des résultats de votes, sinon → COURRIER.
- Un rapport du conseil syndical → AUTRE (pas PV_AG)
- Un compte-rendu rédigé avec les résultats des votes → PV_AG
- Un contrat (syndic, maintenance, assurance, prestation) → CONTRAT
- Un règlement intérieur, règlement de copropriété → RCP
- Un acte notarié qui ÉTABLIT ou MODIFIE un règlement de copropriété (état descriptif de division, acte modificatif, acte rectificatif) → RCP (PAS MUTATION)
- Un état daté, pré-état daté, questionnaire acquisition, notification de vente → MUTATION
- Un plan d'architecte, plan technique, DOE, schéma d'implantation → PLAN
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



# Client Bedrock thread-safe (un par thread)
_thread_local = threading.local()

def _get_bedrock():
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


def extract_json(text):
    """Extrait le premier objet JSON valide d'un texte (ignore le bavardage Haiku après le JSON)."""
    text = text.strip()
    text = re.sub(r"^```json?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Trouver le premier '{' et son '}' fermant correspondant
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i+1])
    raise json.JSONDecodeError("Unterminated JSON object", text, start)

# Cache pour reprises (thread-safe)
cache = {}
_cache_lock = threading.Lock()
if os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    print(f"  → {len(cache)} documents en cache (reprise)")

stats = {"llm_calls": 0, "cache_hits": 0, "errors": 0, "too_short": 0}
_stats_lock = threading.Lock()

FALLBACK_RESULT = {
    "date_document": None, "annee": None, "sous_type": None,
    "parties_concernees": [], "statut": None,
    "montant_principal": None, "resume_une_ligne": None
}


def normalize_sous_type(st):
    """Normalisation mécanique du sous_type — règles de forme, pas de sémantique.
    Corrige les problèmes récurrents que le prompt ne suffit pas à éviter."""
    if not st or st == "null":
        return None
    # Majuscule + strip
    st = st.strip().upper()
    # Accents → ASCII
    st = unicodedata.normalize("NFD", st)
    st = "".join(c for c in st if unicodedata.category(c) != "Mn")
    # Espaces et tirets → underscores
    st = st.replace(" ", "_").replace("-", "_")
    # Supprimer underscores multiples
    st = re.sub(r"_+", "_", st).strip("_")
    # Composite "X, Y, Z" → premier seulement
    if "," in st:
        st = st.split(",")[0].strip().strip("_")
    return st or None


def extract_metadata(source_file, doc_type, texte):
    """Appelle Haiku pour extraire les métadonnées. Thread-safe via locks."""
    with _cache_lock:
        if source_file in cache:
            with _stats_lock:
                stats["cache_hits"] += 1
            return cache[source_file]

    if len(texte.strip()) < 100:
        with _stats_lock:
            stats["too_short"] += 1
        with _cache_lock:
            cache[source_file] = FALLBACK_RESULT
        return FALLBACK_RESULT

    prompt = METADATA_PROMPT.format(doc_type=doc_type, texte=texte[:3500])
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}]
    })

    bedrock_client = _get_bedrock()

    for attempt in range(MAX_RETRIES):
        try:
            response = bedrock_client.invoke_model(
                modelId=LLM_MODEL, body=body,
                contentType="application/json", accept="application/json"
            )
            result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
            metadata = extract_json(result_text)
            # Normalisation mécanique du sous_type
            if metadata.get("sous_type"):
                metadata["sous_type"] = normalize_sous_type(metadata["sous_type"])
            with _stats_lock:
                stats["llm_calls"] += 1
            with _cache_lock:
                cache[source_file] = metadata
            return metadata

        except json.JSONDecodeError as e:
            # JSON invalide — pas de retry, le modèle redonnerait la même chose
            tqdm.write(f"  ⚠️ JSON invalide {os.path.basename(source_file)}: {e}")
            break

        except Exception as e:
            err_str = str(e)
            if "ThrottlingException" in err_str:
                wait = min(2 ** attempt, 15)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue
            tqdm.write(f"  ⚠️ Erreur {os.path.basename(source_file)}: {e}")
            break

    with _stats_lock:
        stats["errors"] += 1
    with _cache_lock:
        cache[source_file] = FALLBACK_RESULT
    return FALLBACK_RESULT


# =====================================================
# Exécution parallèle
# =====================================================
print(f"\nExtraction des métadonnées pour {len(docs)} documents ({MAX_WORKERS} workers)...\n")

start_time = time.time()
results = {}  # source_file → record

def process_one(source_file, doc_info):
    texte = build_extraction_window(doc_info["chunks"], doc_info["doc_type"])
    metadata = extract_metadata(source_file, doc_info["doc_type"], texte)
    return source_file, {
        "source_file": source_file,
        "copropriete": doc_info["copropriete"],
        "nom_fichier": doc_info["nom_fichier"],
        "doc_type": doc_info["doc_type"],
        "total_chunks": doc_info["total_chunks"],
        "premier_texte": doc_info["chunks"][0]["text"][:500] if doc_info["chunks"] else "",
        **metadata
    }

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
    futures = {
        executor.submit(process_one, sf, di): sf
        for sf, di in docs.items()
    }

    pbar = tqdm(total=len(futures), desc="Metadata")
    cache_save_counter = 0

    for future in as_completed(futures):
        sf, record = future.result()
        results[sf] = record
        pbar.update(1)

        # Sauvegarder le cache tous les 50 documents
        cache_save_counter += 1
        if cache_save_counter % 50 == 0:
            with _cache_lock:
                with open(CACHE_FILE, "w", encoding="utf-8") as fc:
                    json.dump(cache, fc, ensure_ascii=False)

    pbar.close()

# =====================================================
# Consolidation des sous_types par Haiku
# =====================================================
# Collecter tous les sous_types uniques avec leur fréquence
sous_type_counts = {}
for r in results.values():
    st = r.get("sous_type")
    if st:
        sous_type_counts[st] = sous_type_counts.get(st, 0) + 1

if sous_type_counts:
    print(f"\n⏳ Consolidation des {len(sous_type_counts)} sous_types uniques via Haiku...")

    consolidation_prompt = f"""Tu es un expert en gestion de copropriété.
Voici une liste de sous-types de documents extraits automatiquement, avec leur fréquence d'apparition.
Certains sont des variantes, synonymes ou formes singulier/pluriel du même concept.

Produis un mapping JSON qui regroupe les variantes sous la forme canonique la plus courte et standard.
Règles :
- Garder la forme la PLUS COURTE et la plus standard du domaine syndic/copro
- TOUJOURS au singulier sauf si le pluriel est la forme standard (CHARGES, ESPACES_VERTS, FONDS_TRAVAUX)
- Ne mapper que les vrais synonymes/variantes. Ne PAS fusionner des concepts différents.
- Les termes déjà canoniques → se mapper vers eux-mêmes
- Format : un objet JSON plat {{"variante": "canonique", ...}}

Sous-types à consolider :
{json.dumps(sous_type_counts, indent=2, ensure_ascii=False)}"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": consolidation_prompt}]
    })

    try:
        bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        # Retry pour throttling et erreurs réseau
        for attempt in range(3):
            try:
                response = bedrock_client.invoke_model(
                    modelId=LLM_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
                mapping = extract_json(result_text)
                break
            except json.JSONDecodeError:
                raise
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

        # Appliquer le mapping
        remapped = 0
        for sf, record in results.items():
            st = record.get("sous_type")
            if st and st in mapping and mapping[st] != st:
                record["sous_type"] = mapping[st]
                remapped += 1

        # Rapport
        merges = {}
        for old, new in mapping.items():
            if old != new:
                merges.setdefault(new, []).append(old)
        if merges:
            print(f"  ✅ {remapped} documents remappés, {len(merges)} fusions :")
            for canonical, variants in sorted(merges.items()):
                print(f"    {canonical:25s} ← {', '.join(variants)}")
        else:
            print(f"  ✅ Aucune fusion nécessaire")

    except Exception as e:
        print(f"  ⚠️ Consolidation échouée ({e}) — sous_types non fusionnés")

# =====================================================
# Déduplication : groupement par document logique
# =====================================================
print(f"\n⏳ Déduplication des documents par groupe logique...")

# Grouper par (copropriete, doc_type_corrige, annee)
groups = {}
for sf, record in results.items():
    dt = record.get("doc_type_corrige") or record.get("doc_type", "AUTRE")
    annee = record.get("annee")
    copro = record.get("copropriete", "")
    if annee:
        key = (copro, dt, annee)
        groups.setdefault(key, []).append(sf)

# Groupes avec doublons potentiels (>1 fichier)
multi_groups = {k: v for k, v in groups.items() if len(v) > 1}
print(f"  {len(multi_groups)} groupes avec >1 fichier (total {sum(len(v) for v in multi_groups.values())} fichiers)")

# Initialiser tous les documents comme référence unique (groupe = eux-mêmes)
for sf, record in results.items():
    dt = record.get("doc_type_corrige") or record.get("doc_type", "AUTRE")
    annee = record.get("annee")
    copro = record.get("copropriete", "")
    if annee:
        record["groupe_doc"] = f"{dt}_{annee}_{copro}"
    else:
        record["groupe_doc"] = f"{dt}_NODATE_{copro}"
    record["est_reference"] = True

# Pour chaque groupe multi-fichiers, demander à Haiku de trier
DEDUP_PROMPT = """Tu es un expert en gestion documentaire de copropriété.
Voici un groupe de fichiers du même type, même année, même copropriété.
Identifie les VRAIS doublons (copies du même document) et les documents DISTINCTS (sujets différents).

ATTENTION : dans un même type/année, il y a souvent PLUSIEURS documents DIFFÉRENTS.
Par exemple :
- 3 ventes à 3 copropriétaires différents (VENTE JURADO ≠ VENTE KNEITZ) → DISTINCTS
- 3 contrats pour 3 équipements différents (ascenseur ≠ portes ≠ VMC) → DISTINCTS
- 3 états datés pour 3 ventes différentes (Hoche ≠ Suzan ≠ Foucher) → DISTINCTS
- 3 factures de fournisseurs différents → DISTINCTS
- 3 plans d'étages différents (N02, N04, N05) → DISTINCTS
- 2 constats de sinistres différents (ADICEBM ≠ CRUET) → DISTINCTS
- 6 devis numérotés différemment (WY752, WY753...) pour des emplacements différents → DISTINCTS

Sont des COPIES/DOUBLONS uniquement quand :
- Même document en PDF + DOCX (ex: "Contrat syndic 2023.pdf" et "Contrat syndic 2023.docx")
- Version brouillon + version finale (ex: "BAZ PV AG.docx" et "PV AG 2014.pdf")
- Versions numérotées du même document (ex: "V1.docx" et "V2.docx")

Groupe : {doc_type} {annee} — {n} fichiers :
{file_list}

Réponds UNIQUEMENT par un objet JSON valide :
{{
  "reference": "nom_du_fichier_reference (parmi les copies/brouillons, celui qui est le plus complet)",
  "copies": ["copie1", "copie2"],
  "brouillons": ["projet_ou_baze"],
  "distincts": ["fichier_sur_un_sujet_different"]
}}

Critères pour la référence (uniquement parmi les copies du MÊME document) :
- PDF signé ("SIGNE", "FINAL") prime toujours
- Sinon le fichier le plus long (plus de chunks)
- PDF prime sur DOCX
- Si TOUS les fichiers sont distincts, choisis le premier comme reference et mets tous les autres dans distincts"""

bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
dedup_ok = 0
dedup_err = 0

for (copro, dt, annee), source_files in multi_groups.items():
    # Construire la liste pour Haiku — nom + chunks + résumé pour distinguer les sujets
    file_lines = []
    for sf in source_files:
        rec = results[sf]
        resume = rec.get("resume_une_ligne") or ""
        if resume:
            file_lines.append(f"  - {rec['nom_fichier']} ({rec.get('total_chunks', '?')} chunks) → {resume[:100]}")
        else:
            file_lines.append(f"  - {rec['nom_fichier']} ({rec.get('total_chunks', '?')} chunks)")

    prompt = DEDUP_PROMPT.format(
        doc_type=dt, annee=annee, n=len(source_files),
        file_list="\n".join(file_lines)
    )

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": prompt}]
    })

    try:
        # Retry pour throttling et erreurs réseau
        for attempt in range(3):
            try:
                response = bedrock_client.invoke_model(
                    modelId=LLM_MODEL, body=body,
                    contentType="application/json", accept="application/json"
                )
                result_text = json.loads(response["body"].read())["content"][0]["text"].strip()
                dedup_result = extract_json(result_text)
                break
            except json.JSONDecodeError:
                raise  # pas de retry sur JSON invalide
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise

        ref_name = dedup_result.get("reference", "")
        copies = set(dedup_result.get("copies", []))
        brouillons = set(dedup_result.get("brouillons", []))
        distincts = set(dedup_result.get("distincts", []))
        groupe_id = f"{dt}_{annee}_{copro}"

        for sf in source_files:
            rec = results[sf]
            nom = rec["nom_fichier"]
            rec["groupe_doc"] = groupe_id

            if nom in distincts:
                # Document distinct → son propre groupe
                rec["groupe_doc"] = f"{dt}_{annee}_{copro}_{nom[:20]}"
                rec["est_reference"] = True
            elif nom == ref_name:
                rec["est_reference"] = True
            else:
                rec["est_reference"] = False

        dedup_ok += 1

    except Exception as e:
        dedup_err += 1
        tqdm.write(f"  ⚠️ Dédup échoué {dt} {annee}: {e}")

non_ref = sum(1 for r in results.values() if not r.get("est_reference", True))
print(f"  ✅ {dedup_ok} groupes traités, {dedup_err} erreurs")
print(f"  📊 {non_ref} documents marqués comme copies/brouillons")

# Écriture finale (ordre stable par source_file)
with open(OUTPUT_FILE, "w", encoding="utf-8") as fout:
    for sf in docs:
        if sf in results:
            fout.write(json.dumps(results[sf], ensure_ascii=False) + "\n")

# Sauvegarde finale du cache
with _cache_lock:
    with open(CACHE_FILE, "w", encoding="utf-8") as fc:
        json.dump(cache, fc, ensure_ascii=False)

elapsed = time.time() - start_time


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
print(f"\nTerminé en {elapsed:.0f}s ({elapsed/60:.1f} min) — {MAX_WORKERS} workers")
print(f"Documents traités : {len(all_records)}")
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

# Déduplication
refs = sum(1 for r in all_records if r.get("est_reference", True))
copies = sum(1 for r in all_records if not r.get("est_reference", True))
groupes = len(set(r.get("groupe_doc", r["source_file"]) for r in all_records))
print(f"\n📋 Déduplication :")
print(f"  Documents référence    : {refs}")
print(f"  Copies/brouillons      : {copies}")
print(f"  Groupes logiques       : {groupes}")

# Détail des groupes avec copies
groups_with_copies = {}
for r in all_records:
    g = r.get("groupe_doc", "")
    groups_with_copies.setdefault(g, []).append((r["nom_fichier"], r.get("est_reference", True)))
for g, members in sorted(groups_with_copies.items()):
    if len(members) > 1 and any(not ref for _, ref in members):
        print(f"\n  Groupe: {g}")
        for nom, is_ref in members:
            tag = "✅ REF" if is_ref else "  copie"
            print(f"    {tag}  {nom}")

print(f"\n📁 Métadonnées : {OUTPUT_FILE}")

# Coût estimé
cost = stats["llm_calls"] * 1000 * 0.80 / 1_000_000  # ~1000 tokens input, $0.80/MTok
print(f"💰 Coût estimé Haiku : ${cost:.2f}")
