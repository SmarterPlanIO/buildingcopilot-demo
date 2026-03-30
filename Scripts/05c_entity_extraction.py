"""
ETAPE 5c - Extraction d'entites sinistre via Haiku (Module Gestion de Dossiers)
Lance : python 05c_entity_extraction.py

Extrait les entites structurees des documents SINISTRE (lese, expert, assureur, etc.)
et cree des dossiers automatiquement en les groupant par (copropriete, type, lese_nom).

Input  : chunks_avec_embeddings_sq.jsonl (sortie de 05b)
Outputs : dossiers.jsonl + chunks enrichis avec dossier_id

Cout estime : ~100 documents x $0.0001 = ~$0.02
"""
import os
import json
import re
import boto3
import time
import threading
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# =====================================================
# CONFIGURATION
# =====================================================
INPUT_FILE = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings_sq.jsonl"
OUTPUT_DOSSIERS = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\dossiers.jsonl"
OUTPUT_CHUNKS = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts\chunks_avec_embeddings_sq.jsonl"
AWS_REGION = "eu-west-1"

HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

MAX_WORKERS = 10
MAX_RETRIES = 3

# =====================================================
# Workflow template SINISTRE_DDE
# =====================================================
WORKFLOW_SINISTRE_DDE = {
    "etapes": [
        {"nom": "Constat amiable DDE", "delai_j": 0, "statut": "A_FAIRE"},
        {"nom": "Declaration assureur immeuble", "delai_j": 5, "statut": "A_FAIRE"},
        {"nom": "Constitution dossier expert", "delai_j": 15, "statut": "A_FAIRE"},
        {"nom": "Convocation expertise", "delai_j": 30, "statut": "A_FAIRE"},
        {"nom": "Reunion expertise sur site", "delai_j": 45, "statut": "A_FAIRE"},
        {"nom": "Rapport expertise et chiffrage", "delai_j": 60, "statut": "A_FAIRE"},
        {"nom": "Devis travaux reparation", "delai_j": 75, "statut": "A_FAIRE"},
        {"nom": "Execution travaux", "delai_j": 120, "statut": "A_FAIRE"},
        {"nom": "Reglement indemnites", "delai_j": 150, "statut": "A_FAIRE"},
        {"nom": "Cloture dossier", "delai_j": 180, "statut": "A_FAIRE"},
    ],
    "pieces_requises": [
        "Constat amiable signe",
        "Coordonnees assurance immeuble",
        "Identite et assureur du voisin",
        "Etat des pertes chiffre",
        "Justificatif suppression cause",
        "Factures recherche de fuite",
    ],
}

# =====================================================
# Prompt d'extraction d'entites
# =====================================================
EXTRACTION_PROMPT = """Tu es un gestionnaire de copropriete expert en sinistres.
Analyse ce document de sinistre et extrais les entites structurees.

Document (nom du fichier) : {filename}
Dossier parent : {folder_name}

Texte :
{text}

Reponds UNIQUEMENT par un objet JSON valide :
{{
  "type_sinistre": "DDE|MRI|INCENDIE|AUTRE",
  "lese_nom": "nom du coproprietaire lese ou null",
  "lese_lot": "numero de lot ou etage ou null",
  "responsable_nom": "nom du responsable du sinistre ou null",
  "expert_nom": "nom de l'expert mandate ou null",
  "expert_cabinet": "nom du cabinet d'expertise ou null",
  "assureur": "nom de l'assureur ou null",
  "num_police": "numero de police ou null",
  "num_sinistre": "numero de sinistre ou null",
  "date_sinistre": "YYYY-MM-DD ou null",
  "montant": null,
  "etape_detectee": "CONSTAT|DECLARATION|EXPERTISE|DEVIS|TRAVAUX|CLOTURE|null",
  "pieces_identifiees": []
}}

Regles :
- Ne remplis que les champs dont tu es CERTAIN a partir du texte
- date au format ISO (YYYY-MM-DD)
- montant en nombre sans symbole
- pieces_identifiees : liste des types de pieces presents (ex: "constat amiable", "convocation expertise", "rapport expert", "devis", "facture")
- Si le document est un constat amiable, etape_detectee = "CONSTAT"
- Si c'est une convocation expertise, etape_detectee = "EXPERTISE"
- Si c'est un devis/facture, etape_detectee = "DEVIS" ou "TRAVAUX"
- Tout champ incertain -> null"""

# =====================================================
# Client Bedrock thread-safe
# =====================================================
_thread_local = threading.local()

def get_bedrock_client():
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _thread_local.client


def extract_entities(text, filename, folder_name):
    """Extrait les entites d'un document sinistre via Haiku. Retourne un dict ou None."""
    text_excerpt = text[:3000].strip()
    if len(text_excerpt) < 50:
        return None

    prompt = EXTRACTION_PROMPT.format(
        text=text_excerpt,
        filename=filename,
        folder_name=folder_name,
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}]
    })

    bedrock = get_bedrock_client()

    for attempt in range(MAX_RETRIES):
        try:
            response = bedrock.invoke_model(
                modelId=HAIKU_MODEL, body=body,
                contentType="application/json", accept="application/json"
            )
            result = json.loads(response["body"].read())
            answer = result["content"][0]["text"].strip()

            # Nettoyer le JSON (retirer les ```json``` eventuels)
            answer = re.sub(r"^```json?\s*", "", answer)
            answer = re.sub(r"\s*```$", "", answer)

            parsed = json.loads(answer)
            return parsed

        except json.JSONDecodeError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue
            return None
        except Exception as e:
            err_str = str(e)
            if "ThrottlingException" in err_str:
                wait = min(2 ** attempt, 15)
                time.sleep(wait)
                continue
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
                continue
            return None

    return None


def slugify(text):
    """Convertit un texte en slug ASCII pour dossier_id."""
    if not text:
        return "INCONNU"
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    return text[:50] if text else "INCONNU"


def detect_etape_status(etape_nom, pieces_ids, etape_detectee):
    """Determine le statut d'une etape basee sur les pieces et l'etape detectee."""
    mapping = {
        "Constat amiable DDE": ("CONSTAT", ["constat amiable"]),
        "Declaration assureur immeuble": ("DECLARATION", ["declaration sinistre", "formulaire airtable"]),
        "Constitution dossier expert": ("EXPERTISE", ["demande documents", "coordonnees"]),
        "Convocation expertise": ("EXPERTISE", ["convocation expertise"]),
        "Reunion expertise sur site": ("EXPERTISE", ["rapport expertise", "rapport expert"]),
        "Rapport expertise et chiffrage": ("EXPERTISE", ["rapport expertise", "chiffrage"]),
        "Devis travaux reparation": ("DEVIS", ["devis"]),
        "Execution travaux": ("TRAVAUX", ["facture travaux", "ordre de service"]),
        "Reglement indemnites": ("CLOTURE", ["reglement", "indemnite"]),
        "Cloture dossier": ("CLOTURE", []),
    }
    if etape_nom not in mapping:
        return "A_FAIRE"

    etape_code, piece_keywords = mapping[etape_nom]
    # Si l'etape detectee est posterieure ou egale, marquer comme FAIT
    etape_order = ["CONSTAT", "DECLARATION", "EXPERTISE", "DEVIS", "TRAVAUX", "CLOTURE"]
    if etape_detectee and etape_detectee in etape_order and etape_code in etape_order:
        if etape_order.index(etape_detectee) >= etape_order.index(etape_code):
            return "FAIT"
    # Sinon, verifier les pieces
    pieces_lower = [p.lower() for p in pieces_ids]
    for kw in piece_keywords:
        if any(kw in p for p in pieces_lower):
            return "FAIT"
    return "A_FAIRE"


# =====================================================
# Execution
# =====================================================
if __name__ == "__main__":
    print("=" * 50)
    print("EXTRACTION D'ENTITES SINISTRE (Module Dossiers)")
    print("=" * 50)

    # Charger tous les chunks
    print(f"\nChargement de {INPUT_FILE}...")
    all_chunks = []
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            all_chunks.append(json.loads(line))
    print(f"  {len(all_chunks)} chunks charges")

    # Grouper les chunks SINISTRE par source_file
    sinistre_docs = defaultdict(list)
    for chunk in all_chunks:
        if chunk.get("doc_type") == "SINISTRE":
            sinistre_docs[chunk["source_file"]].append(chunk)

    print(f"  {len(sinistre_docs)} documents SINISTRE trouves")

    if not sinistre_docs:
        print("Aucun document sinistre. Rien a extraire.")
        exit(0)

    # Preparer les taches d'extraction (1 par document, pas par chunk)
    tasks = []
    for source_file, chunks in sinistre_docs.items():
        # Concatener les textes des chunks (premiers 3000 chars)
        full_text = "\n---\n".join(c["text"] for c in sorted(chunks, key=lambda x: x.get("chunk_index", 0)))
        filename = chunks[0].get("nom_fichier", os.path.basename(source_file))
        # Extraire le nom du dossier parent (ex: "DDE MARROUNI")
        parts = source_file.replace("\\", "/").split("/")
        folder_name = ""
        for i, part in enumerate(parts):
            if part.upper() == "SINISTRE" and i + 1 < len(parts):
                folder_name = parts[i + 1]
                break
        tasks.append((source_file, full_text, filename, folder_name, [c["chunk_id"] for c in chunks]))

    print(f"  {len(tasks)} documents a traiter")
    print(f"  Cout estime : ~${len(tasks) * 0.0001:.4f}")

    # Extraction parallele
    extraction_results = {}  # source_file -> dict entites

    print(f"\nExtraction en cours ({MAX_WORKERS} workers)...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for source_file, text, filename, folder, chunk_ids in tasks:
            future = executor.submit(extract_entities, text, filename, folder)
            futures[future] = (source_file, chunk_ids, folder)

        for future in tqdm(as_completed(futures), total=len(futures), desc="Extraction entites"):
            source_file, chunk_ids, folder = futures[future]
            entities = future.result()
            if entities:
                entities["_source_file"] = source_file
                entities["_chunk_ids"] = chunk_ids
                entities["_folder_name"] = folder
                extraction_results[source_file] = entities

    print(f"\n  {len(extraction_results)} documents extraits avec succes")

    # Grouper par dossier — utiliser le folder_name (sous-dossier SINISTRE) comme cle primaire
    # car il est stable (ex: "DDE MARROUNI") alors que le lese_nom extrait par Haiku varie
    dossier_groups = defaultdict(list)
    for source_file, entities in extraction_results.items():
        copro = sinistre_docs[source_file][0].get("copropriete", "INCONNU")
        folder = entities.get("_folder_name", "").strip()
        if not folder:
            # Fallback sur lese_nom si pas de folder
            folder = entities.get("lese_nom") or "INCONNU"
        type_sin = entities.get("type_sinistre", "DDE") or "DDE"
        key = (copro, f"SINISTRE_{type_sin}", slugify(folder))
        dossier_groups[key].append(entities)

    print(f"  {len(dossier_groups)} dossiers identifies")

    # Construire les dossiers
    dossiers = []
    chunk_to_dossier = {}  # chunk_id -> dossier_id

    for (copro, type_dossier, lese_slug), docs_entities in dossier_groups.items():
        # Determiner l'annee (premiere date trouvee)
        annee = None
        for ent in docs_entities:
            d = ent.get("date_sinistre")
            if d and len(d) >= 4:
                annee = d[:4]
                break
        if not annee:
            annee = "2024"  # fallback

        dossier_id = f"{slugify(copro)}_{type_dossier}_{lese_slug}_{annee}"

        # Agreger les entites de tous les documents du dossier
        all_pieces = set()
        all_source_files = []
        all_chunk_ids = []
        best_lese_nom = None
        best_lese_lot = None
        best_expert = None
        best_assureur = None
        best_date = None
        best_montant = None
        best_etape = None
        best_num_sinistre = None

        for ent in docs_entities:
            # Collecter les pieces
            for p in ent.get("pieces_identifiees", []):
                all_pieces.add(p)
            all_source_files.append(ent["_source_file"])
            all_chunk_ids.extend(ent["_chunk_ids"])
            # Prendre la premiere valeur non-null pour chaque champ
            if not best_lese_nom and ent.get("lese_nom"):
                best_lese_nom = ent["lese_nom"]
            if not best_lese_lot and ent.get("lese_lot"):
                best_lese_lot = ent["lese_lot"]
            if not best_expert and ent.get("expert_nom"):
                best_expert = ent["expert_nom"]
                if ent.get("expert_cabinet"):
                    best_expert += f" ({ent['expert_cabinet']})"
            if not best_assureur and ent.get("assureur"):
                best_assureur = ent["assureur"]
            if not best_date and ent.get("date_sinistre"):
                best_date = ent["date_sinistre"]
            if not best_montant and ent.get("montant"):
                best_montant = ent["montant"]
            if not best_num_sinistre and ent.get("num_sinistre"):
                best_num_sinistre = ent["num_sinistre"]
            # Garder l'etape la plus avancee
            etape_order = ["CONSTAT", "DECLARATION", "EXPERTISE", "DEVIS", "TRAVAUX", "CLOTURE"]
            det = ent.get("etape_detectee")
            if det and det in etape_order:
                if best_etape is None or etape_order.index(det) > etape_order.index(best_etape):
                    best_etape = det

        # Nom lisible du dossier (depuis le dossier parent ou le lese)
        folder_names = [e.get("_folder_name", "") for e in docs_entities if e.get("_folder_name")]
        nom_dossier = folder_names[0] if folder_names else f"{type_dossier} {best_lese_nom or lese_slug}"

        # Construire les etapes avec statut auto-detecte
        template = WORKFLOW_SINISTRE_DDE
        etapes = []
        for etape_tmpl in template["etapes"]:
            statut = detect_etape_status(etape_tmpl["nom"], list(all_pieces), best_etape)
            etapes.append({
                "nom": etape_tmpl["nom"],
                "delai_j": etape_tmpl["delai_j"],
                "statut": statut,
            })

        # Determiner les pieces fournies
        pieces_requises = template["pieces_requises"]
        pieces_fournies = []
        for pr in pieces_requises:
            pr_lower = pr.lower()
            for p in all_pieces:
                if any(kw in p.lower() for kw in pr_lower.split()[:2]):
                    pieces_fournies.append(pr)
                    break

        # Determiner le statut global
        etapes_faites = sum(1 for e in etapes if e["statut"] == "FAIT")
        if etapes_faites == len(etapes):
            statut_global = "CLOTURE"
        elif etapes_faites > 0:
            statut_global = "EN_COURS"
        else:
            statut_global = "EN_ATTENTE"

        dossier = {
            "dossier_id": dossier_id,
            "copropriete": copro,
            "type_dossier": type_dossier,
            "nom_dossier": nom_dossier,
            "statut": statut_global,
            "date_ouverture": best_date,
            "date_cloture": None,
            "lese_nom": best_lese_nom,
            "lese_lot": best_lese_lot,
            "responsable_nom": None,
            "responsable_lot": None,
            "expert_nom": best_expert,
            "assureur": best_assureur,
            "etapes": etapes,
            "pieces_requises": pieces_requises,
            "pieces_fournies": pieces_fournies,
            "montant_estime": best_montant,
            "montant_reel": None,
            "documents_lies": all_source_files,
            "resume_ia": f"Sinistre {nom_dossier} - {best_lese_nom or 'lese inconnu'} - "
                         f"{len(all_source_files)} documents - "
                         f"{'Expert: ' + best_expert if best_expert else 'Pas d expert'} - "
                         f"{'Ref: ' + best_num_sinistre if best_num_sinistre else ''}",
        }
        dossiers.append(dossier)

        # Mapper les chunks au dossier
        for cid in all_chunk_ids:
            chunk_to_dossier[cid] = dossier_id

    # Ecrire dossiers.jsonl
    print(f"\nEcriture de {OUTPUT_DOSSIERS}...")
    with open(OUTPUT_DOSSIERS, "w", encoding="utf-8") as f:
        for d in dossiers:
            f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
    print(f"  {len(dossiers)} dossiers ecrits")

    # Enrichir les chunks avec dossier_id et reecrire le fichier
    enriched_count = 0
    print(f"\nEnrichissement des chunks avec dossier_id...")
    enriched_chunks = []
    for chunk in all_chunks:
        cid = chunk.get("chunk_id")
        if cid in chunk_to_dossier:
            chunk["dossier_id"] = chunk_to_dossier[cid]
            enriched_count += 1
        enriched_chunks.append(chunk)

    print(f"  {enriched_count} chunks enrichis avec dossier_id")

    # Reecrire le fichier chunks
    print(f"Ecriture de {OUTPUT_CHUNKS}...")
    with open(OUTPUT_CHUNKS, "w", encoding="utf-8") as f:
        for chunk in enriched_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False, default=str) + "\n")

    # Resume
    print("\n" + "=" * 50)
    print("RESUME")
    print("=" * 50)
    for d in dossiers:
        etapes_faites = sum(1 for e in d["etapes"] if e["statut"] == "FAIT")
        pieces_ok = len(d["pieces_fournies"])
        pieces_total = len(d["pieces_requises"])
        print(f"  {d['statut']:12s} | {d['nom_dossier']:30s} | "
              f"{etapes_faites}/{len(d['etapes'])} etapes | "
              f"{pieces_ok}/{pieces_total} pieces | "
              f"{len(d['documents_lies'])} docs")

    print(f"\nTotal : {len(dossiers)} dossiers, {enriched_count} chunks lies")
    print(f"Fichiers : {OUTPUT_DOSSIERS}")
    print(f"           {OUTPUT_CHUNKS}")
