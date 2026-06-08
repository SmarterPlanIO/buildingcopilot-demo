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
import argparse
import boto3
import time
import threading
import unicodedata
from collections import defaultdict, Counter
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

import pipeline_config as pcfg

# =====================================================
# CONFIGURATION
# =====================================================
AWS_REGION = "eu-west-1"
HAIKU_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_WORKERS = 10
MAX_RETRIES = 3

# Mode per-copro (--copro <code>) : lit/ecrit dans per_copro/<code>/ (shard, RAM
# ~constante via streaming). Sans --copro : mode legacy (monolithe global, retro-
# compatible). parse_known_args -> import-safe (pas d'erreur si importe).
_parser = argparse.ArgumentParser(description="Extraction entites sinistre + dossiers d'une copropriete.")
_parser.add_argument("--copro", help="Code NCG (ex: 8050). Absent = mode legacy global.")
_args, _ = _parser.parse_known_args()

if _args.copro:
    _p = pcfg.paths_for(_args.copro)
    _p["per_copro"].mkdir(parents=True, exist_ok=True)
    INPUT_FILE = str(_p["embeddings_sq_jsonl"])
    OUTPUT_DOSSIERS = str(_p["dossiers_jsonl"])
    print(f"📌 Mode per-copro : {_args.copro} ({_p['folder_name']})")
else:
    _RESULTS = r"G:\Mon Drive\Projet SmarterPlan\Sales\Prospects\NCG\202512 Mission Déploiement IA interne\Résultats bruts"
    INPUT_FILE = os.path.join(_RESULTS, "chunks_avec_embeddings_sq.jsonl")
    OUTPUT_DOSSIERS = os.path.join(_RESULTS, "dossiers.jsonl")

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


# ── A1 : detection du dossier sinistre depuis le chemin ───────────────────────
_SINISTRE_SEG = re.compile(r"^\d*\s*-?\s*SINISTRES?$", re.I)
# A1-bis : archive d'un syndic precedent, "...\Gestion\Dossiers\<DOSSIER>\..."
# (ex. 8050, archive Bellman sous "13 - DIVERS"). Le segment "Dossiers" joue la meme
# racine que "5 - SINISTRES". Convention specifique mais sans risque ailleurs : elle
# ne se declenche QUE si aucun folder "SINISTRES" n'est present dans le chemin.
_DOSSIERS_SEG = re.compile(r"^DOSSIERS?$", re.I)
# Niveaux "categorie" : un regroupement, pas un dossier -> descendre d'un cran, ou
# retomber sur le nom s'il n'y a pas de sous-dossier. (ex: "DOMMAGE OUVRAGE/DO LIM",
# "Sinistre degats des eaux/DGE ROUDAUT", "2019/...", "Recensement des sinistres...",
# "DO Privatives declarees individuellement" = agregats, pas des dossiers uniques.)
_CATEGORY_SEG = re.compile(
    r"^(DOMMAGES?\s+OUVRAGE"
    r"|SINISTRES?\s+D[EÉ]G[AÂ]TS?\s+DES\s+EAUX"
    r"|RECENSEMENT\s+DES\s+SINISTRES.*"
    r"|(DO\s+)?PRIVATIVES?\s+D[EÉ]CLAR.*"
    r"|\d{4})$",
    re.I,
)


def _folder_after(segs, seg_re):
    """Folder de dossier juste apres le 1er segment matchant seg_re.

    Saute un eventuel niveau categorie (_CATEGORY_SEG). Retourne None si le segment
    racine est absent ; "" si racine presente mais pas de folder stable (fichier
    direct, ou categorie sans sous-dossier) -> le groupage retombera sur lese_nom.
    On ne prend JAMAIS le nom de fichier comme folder (sur-segmentation).
    """
    idx = next((i for i, s in enumerate(segs) if seg_re.match(s.strip())), None)
    if idx is None:
        return None
    after = segs[idx + 1:-1]  # dossiers entre la racine et le fichier final
    if not after:
        return ""
    folder = after[0]
    if _CATEGORY_SEG.match(folder.strip()):
        return after[1] if len(after) >= 2 else ""  # categorie sans sous-dossier -> fallback
    return folder


def extract_dossier_folder(source_file):
    """Cle de groupage dossier depuis le chemin (A1).

    Cherche le folder dossier sous "5 - SINISTRES" (convention NCG courante), sinon
    sous "...\\Dossiers\\" (archive d'un syndic precedent, A1-bis). Renvoie "" si
    aucune racine ni folder stable -> le groupage retombe sur lese_nom (CAS 2/3).
    On ne prend JAMAIS le nom de fichier comme folder (sur-segmentation).
    """
    segs = [s for s in re.split(r"[\\/]", source_file or "") if s]
    f = _folder_after(segs, _SINISTRE_SEG)
    if f is not None:
        return f
    f = _folder_after(segs, _DOSSIERS_SEG)
    if f is not None:
        return f
    return ""


# ── A3 : type de dossier d'un groupe ──────────────────────────────────────────
# DDE et INCENDIE sont deux perils NOMMES distincts : aucun n'est "plus specifique"
# que l'autre. _TYPE_PRIORITY ne sert donc QUE de tie-break deterministe (stabilite),
# pas d'ordre de gravite. Le vrai signal de type, c'est (1) le nom du folder syndic,
# (2) a defaut, le vote majoritaire des documents.
_TYPE_PRIORITY = {"DDE": 0, "INCENDIE": 1, "MRI": 2, "AUTRE": 3}
_GENERIC = {"MRI", "AUTRE"}  # garanties/catch-all : jamais au-dessus d'un peril nomme

# A3-bis : le nom du folder syndic fait autorite sur le type quand il est parlant.
_FOLDER_TYPE_PATTERNS = [
    (re.compile(r"INCENDIE", re.I), "INCENDIE"),
    (re.compile(r"\bDDE\b|\bDGE\b|D[EÉ]G[AÂ]TS?\s+DES\s+EAUX|D[EÉ]G[AÂ]T\s+EAUX", re.I), "DDE"),
    (re.compile(r"\bMRI\b|MULTIRISQUE", re.I), "MRI"),
]


def type_from_folder(folder):
    """Type deduit du nom de folder syndic (signal terrain le plus fiable).
    Renvoie None si le folder n'encode aucun peril reconnu -> fallback vote Haiku."""
    if not folder:
        return None
    for rx, t in _FOLDER_TYPE_PATTERNS:
        if rx.search(folder):
            return t
    return None


def vote_type(types):
    """Type majoritaire des documents d'un groupe (A3), renvoie (type, ambigu).
    - peril nomme (DDE/INCENDIE) prioritaire sur generique (MRI/AUTRE)
    - vote majoritaire sur les comptes ; _TYPE_PRIORITY = tie-break deterministe
    - egalite STRICTE entre deux perils nommes -> ('AUTRE', True) : on signale
      l'ambiguite plutot que d'affirmer un peril faux (souvent = fusion a 2 sinistres).
    """
    valid = [t for t in types if t]
    if not valid:
        return "AUTRE", False
    counts = Counter(valid)
    specific = {t: c for t, c in counts.items() if t not in _GENERIC}
    pool = specific or counts
    ranked = sorted(pool.items(), key=lambda kv: (-kv[1], _TYPE_PRIORITY.get(kv[0], 99)))
    top = ranked[0]
    if specific and len(ranked) > 1 and ranked[1][1] == top[1]:
        return "AUTRE", True  # egalite stricte entre perils nommes
    return top[0], False


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

    # Streaming : on ne retient QUE les chunks SINISTRE (RAM ~constante, ne charge
    # jamais tout le corpus en memoire). Les autres chunks sont lus puis jetes.
    if not os.path.exists(INPUT_FILE):
        print(f"❌ Fichier introuvable : {INPUT_FILE}")
        exit(1)
    print(f"\nLecture en streaming de {INPUT_FILE}...")
    sinistre_docs = defaultdict(list)
    n_chunks = 0
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            chunk = json.loads(line)
            n_chunks += 1
            if chunk.get("doc_type") == "SINISTRE":
                sinistre_docs[chunk["source_file"]].append(chunk)

    print(f"  {n_chunks} chunks lus, {len(sinistre_docs)} documents SINISTRE trouves")

    if not sinistre_docs:
        print("Aucun document sinistre. Rien a extraire.")
        exit(0)

    # Preparer les taches d'extraction (1 par document, pas par chunk)
    tasks = []
    for source_file, chunks in sinistre_docs.items():
        # Concatener les textes des chunks (premiers 3000 chars)
        full_text = "\n---\n".join(c["text"] for c in sorted(chunks, key=lambda x: x.get("chunk_index", 0)))
        filename = chunks[0].get("nom_fichier", os.path.basename(source_file))
        # A1 : dossier parent depuis le chemin (detection corrigee + skip categories)
        folder_name = extract_dossier_folder(source_file)
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
        folder = entities.get("_folder_name", "").strip()  # A1
        if not folder:
            # Fallback sur lese_nom si pas de folder stable (CAS 2/3)
            folder = entities.get("lese_nom") or "INCONNU"
        key = (copro, slugify(folder))  # A3 : type retire de la cle de groupage
        dossier_groups[key].append(entities)

    print(f"  {len(dossier_groups)} dossiers identifies")

    # Construire les dossiers
    dossiers = []
    type_audit = []  # dossiers tombes en AUTRE par egalite stricte (a inspecter au run)

    for (copro, lese_slug), docs_entities in dossier_groups.items():
        # A3-bis : le folder syndic fait autorite ; sinon vote majoritaire Haiku ; sinon AUTRE.
        folder_type = next(
            (t for t in (type_from_folder(e.get("_folder_name")) for e in docs_entities) if t),
            None,
        )
        if folder_type:
            type_sin = folder_type
        else:
            type_sin, ambigu = vote_type([e.get("type_sinistre") for e in docs_entities])
            if ambigu:
                votes = Counter(t for t in (e.get("type_sinistre") for e in docs_entities) if t)
                type_audit.append((copro, lese_slug, dict(votes)))
        type_dossier = f"SINISTRE_{type_sin}"
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
        best_num_police = None

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
            if not best_num_police and ent.get("num_police"):
                best_num_police = ent["num_police"]
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
            "num_sinistre": best_num_sinistre,
            "num_police": best_num_police,
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

    # Ecrire dossiers.jsonl
    print(f"\nEcriture de {OUTPUT_DOSSIERS}...")
    with open(OUTPUT_DOSSIERS, "w", encoding="utf-8") as f:
        for d in dossiers:
            f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
    print(f"  {len(dossiers)} dossiers ecrits")

    # A3-ter : audit des types ambigus (egalite stricte entre perils nommes -> AUTRE).
    # Souvent symptome d'une fusion a 2 sinistres, pas d'un vrai doute de type.
    if type_audit:
        print(f"\n  ⚠ {len(type_audit)} dossier(s) etiquete(s) AUTRE par egalite stricte de type :")
        for copro, slug, votes in type_audit:
            print(f"      {copro} / {slug} : {votes}")
    else:
        print("\n  ✓ Aucun type ambigu (egalite stricte) detecte")

    # NB : on ne reecrit PLUS le fichier chunks. Le champ dossier_id sur les chunks
    # est donnee morte en aval (aucun retrieval ne le lit) ; l'eviter supprime une
    # reecriture de ~145 Mo/copro (et du monolithe 3,9 Go en legacy), gain disque/IO
    # cle du scale. Cf. PLAN_SCALE_150_COPROS.md.

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

    print(f"\nTotal : {len(dossiers)} dossiers")
    print(f"Fichier : {OUTPUT_DOSSIERS}")
