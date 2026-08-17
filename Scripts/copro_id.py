"""copro_id.py — Identité copro : canonicalisation des codes (produit, multi-client).

Deux régimes d'identifiants coexistent (cf. PLAN_IMMATRICULATION_RNIC.md) :
- Immatriculation RNIC (Registre national, ANAH) : format canonique AA0000000
  (2 lettres + 7 chiffres). C'est le régime standard pour les nouveaux clients
  (Delacour+). Les humains l'écrivent avec tirets/espaces ("AE3-410-578",
  "ae3 410 578") : TOUTE entrée doit passer par canon() avant usage (SQL,
  Airtable, paths, comparaison).
- Codes internes numériques courts (NCG "8050", Lobby "0200") : conservés tels
  quels (canon() est neutre pour eux), la validation stricte RNIC ne s'applique
  qu'aux codes contenant des lettres.

Module partagé pipeline + MCP (vendorisé côté mcp_server/ au build, même
mécanique que rerank.py). Zéro dépendance.
"""
import re

_IMMAT_RE = re.compile(r"^[A-Z]{2}\d{7}$")


def canon(code) -> str:
    """Forme canonique : majuscules, alphanumérique seulement.

    "ae3-410-578" / "AE3 410 578" / "AE3410578" -> "AE3410578" ; "8050" -> "8050".
    """
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def is_immatriculation(code) -> bool:
    """True si le code (toute graphie) est une immatriculation RNIC valide."""
    return bool(_IMMAT_RE.fullmatch(canon(code)))


def display(code) -> str:
    """Format d'affichage humain : AA0-000-000 pour les immatriculations,
    forme canonique inchangée sinon."""
    c = canon(code)
    if _IMMAT_RE.fullmatch(c):
        return f"{c[:3]}-{c[3:6]}-{c[6:]}"
    return c
