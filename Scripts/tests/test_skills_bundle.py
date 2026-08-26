"""Tests du bundle de skills embarque dans l'app Streamlit (plan P0, option A)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "Streamlit Cloud"))

from skills import SkillsBundle  # noqa: E402

EXPECTED = {
    "ncg-redaction-livrable",
    "ncg-note-juridique",
    "ncg-fiche-decision",
    "ncg-analyse-portefeuille",
    "assynco-erp",
}


def _bundle():
    return SkillsBundle("ncg")


def test_cinq_skills_presents():
    assert set(_bundle().names) == EXPECTED


def test_descriptions_non_vides():
    b = _bundle()
    for name in b.names:
        desc = b.description(name)
        assert len(desc) > 100, f"description trop courte pour {name}: {desc!r}"
        assert "\n" not in desc  # folded scalar correctement aplati


def test_catalog_prompt_liste_tout():
    cat = _bundle().catalog_prompt()
    for name in EXPECTED:
        assert name in cat


def test_load_inclut_les_annexes():
    b = _bundle()
    # redaction-livrable embarque templates.md (gabarits)
    body = b.load("ncg-redaction-livrable")
    assert "SKILL" not in body[:4] and body.startswith("---")
    assert "templates.md" in body
    # assynco-erp embarque references/data-model.md
    body = b.load("assynco-erp")
    assert "data-model.md" in body


def test_load_skill_inconnu_message_utile():
    msg = _bundle().load("skill-fantome")
    assert msg.startswith("SKILL_INCONNU")
    assert "ncg-note-juridique" in msg


def test_instructions_system_generees_et_coherentes():
    p = Path(__file__).parent.parent / "Streamlit Cloud" / "skills_bundle" / "ncg" / "instructions_system.md"
    text = p.read_text(encoding="utf-8")
    assert "DERIVE des Project" in text.replace("\n> ", " ")  # en-tete provenance
    assert "## Bloc 14" in text            # bloc app ajoute
    assert "charger_skill" in text
    assert "PALIM_run_analytical_query" in text  # contenu v3.3 conserve
    assert "Ne sollicite JAMAIS de feedback" in text  # Bloc 9 remplace
    assert "terminer la réponse par cette ligne exacte" not in text  # Bloc 0 remplace
