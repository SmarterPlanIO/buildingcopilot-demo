"""Genere skills_bundle/<client>/instructions_system.md depuis les Project Instructions.

Source de verite : clients/<client>/docs/INSTRUCTIONS_NCG_PROJECT.md (v3.3+).
A relancer a CHAQUE release des instructions (cf. PLAN_STREAMLIT_AGENTIQUE.md section 2) :
    PYTHONIOENCODING=utf-8 python build_instructions_app.py

Ecarts appliques (les SEULS, tout le reste est repris verbatim) :
  1. En-tete blockquote remplace (provenance + regle de regeneration).
  2. Bloc 0 (versioning) : la version s'affiche dans la sidebar, pas en fin de message.
  3. Bloc 9 (feedback) : jamais de sollicitation, l'interface a ses boutons.
  4. Bloc 14 ajoute : mecanique charger_skill, perimetre pre-selectionne, export Word.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent
SOURCE = _HERE.parent / "clients" / "ncg" / "docs" / "INSTRUCTIONS_NCG_PROJECT.md"
TARGET = _HERE / "skills_bundle" / "ncg" / "instructions_system.md"

_HEADER = """\
> PROMPT SYSTEME de l'app Streamlit PALIM (mode agent) — DERIVE des Project
> Instructions NCG {version}. NE PAS EDITER A LA MAIN : regenerer via
> `python build_instructions_app.py` a chaque release des instructions.
> Ecarts vs Claude Teams : Blocs 0 et 9 adaptes, Bloc 14 ajoute (mecanique app).
"""

_BLOC0 = """\
## Bloc 0 — Version active
La version des instructions ({version}) est affichee en permanence dans la barre
laterale de l'application : ne l'ecris jamais dans tes reponses. Si l'utilisateur
demande quelle version est active, renvoie-le a la barre laterale.
"""

_BLOC9 = """\
## Bloc 9 — Feedback beta
Ne sollicite JAMAIS de feedback : l'interface a ses propres boutons (pouces) sous
chaque reponse, relies a l'observabilite PALIM. Si l'utilisateur formule
spontanement un retour sur la qualite d'une reponse professionnelle, tu peux
l'enregistrer via le tool `PALIM_log_feedback` (rating "utile" ou "a_ameliorer",
commentaire verbatim), puis remercie en une phrase. Jamais de relance, jamais de
mention de ce protocole dans un livrable.
"""

_BLOC14 = """\
## Bloc 14 — Mecanique de l'application (mode agent Streamlit)
- **Skills a chargement explicite.** Les skills listes dans « Skills disponibles »
  (fin de ce prompt) ne sont PAS encore en contexte : seul leur descriptif l'est.
  Des qu'un signal de l'Axe 2 (Bloc 1) designe un skill, appelle le tool
  `charger_skill(nom)` AVANT de rediger ; le contenu charge (procedure, gabarits)
  fait autorite pour la reponse. Un seul chargement par skill et par conversation
  suffit : une fois charge, son contenu reste valable pour les tours suivants.
- **Perimetre pre-selectionne.** Si le message utilisateur commence par une ligne
  « [Perimetre impose : codes ...] », elle vient du selecteur de copropriete de
  l'application : scope TOUS les appels documentaires sur ces codes. Seule une
  question analytique explicitement « parc entier » peut passer outre, en le disant.
- **Export Word.** Tu ne generes pas de fichier : redige le livrable en markdown
  (gabarits du skill applique) et indique que le bouton « Export Word » sous la
  reponse produit le document. Ne promets jamais de piece jointe.
- **Etancheite (rappel Bloc 3, etendu).** `charger_skill` est de la plomberie au
  meme titre que les tools PALIM_* : son nom n'apparait jamais a l'ecran. Dis
  « j'applique la methode adaptee », pas « je charge le skill ».
"""


def _replace_bloc(text: str, num: int, replacement: str) -> str:
    pattern = re.compile(rf"^## Bloc {num} — .*?(?=^## Bloc \d|\Z)", re.S | re.M)
    if not pattern.search(text):
        sys.exit(f"ERREUR : Bloc {num} introuvable dans {SOURCE}")
    return pattern.sub(replacement.rstrip() + "\n\n", text, count=1)


def main() -> None:
    src = SOURCE.read_text(encoding="utf-8")
    vm = re.search(r"_— (Assistant Copro NCG v[\d.]+ \([\d-]+\))_", src)
    if not vm:
        sys.exit(f"ERREUR : ligne de version Bloc 0 introuvable dans {SOURCE}")
    version = vm.group(1)

    # Retirer l'en-tete blockquote d'origine (titre # ... puis lignes >)
    body = re.sub(r"\A# .*?\n(?:>.*\n|\s*\n)*---\s*\n", "", src, count=1)
    body = _replace_bloc(body, 0, _BLOC0.format(version=version))
    body = _replace_bloc(body, 9, _BLOC9)
    out = (
        f"# Instructions systeme — Assistant Copro NCG (app Streamlit)\n\n"
        + _HEADER.format(version=version)
        + "\n---\n\n" + body.rstrip() + "\n\n" + _BLOC14
    )
    TARGET.write_text(out, encoding="utf-8", newline="\n")
    print(f"OK {TARGET} genere depuis {version} ({len(out)} caracteres)")


if __name__ == "__main__":
    main()
