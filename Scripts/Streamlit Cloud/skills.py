"""Chargeur de skills embarques pour le mode agent Streamlit (plan P0, option A).

Reproduit la semantique Claude Teams : la DESCRIPTION de chaque skill est
toujours visible du modele (section "skills disponibles" du prompt systeme) ;
le CORPS (SKILL.md + fichiers annexes .md) n'entre en contexte que quand le
modele appelle le pseudo-tool charger_skill(nom).

Source des skills : skills_bundle/<client>/<skill>/SKILL.md (copies embarquees,
provenance clients/<client>/skills et mcp_server/skills — cf. plan section 7).
"""
from __future__ import annotations

import re
from pathlib import Path

_BUNDLE_ROOT = Path(__file__).parent / "skills_bundle"


def _parse_frontmatter(text: str) -> dict:
    """Parse le frontmatter YAML minimal (name, description avec folded scalar >-)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    meta, key, buf = {}, None, []
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km:
            if key:
                meta[key] = " ".join(buf).strip()
            key = km.group(1)
            val = km.group(2).strip()
            buf = [] if val in (">-", ">", "|", "|-") else [val]
        elif key and line.startswith((" ", "\t")):
            buf.append(line.strip())
    if key:
        meta[key] = " ".join(buf).strip()
    return meta


class SkillsBundle:
    def __init__(self, client: str = "ncg"):
        self.root = _BUNDLE_ROOT / client
        if not self.root.is_dir():
            raise FileNotFoundError(f"bundle de skills introuvable : {self.root}")
        self._skills: dict[str, dict] = {}
        for skill_md in sorted(self.root.glob("*/SKILL.md")):
            meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
            name = meta.get("name") or skill_md.parent.name
            self._skills[name] = {
                "dir": skill_md.parent,
                "description": meta.get("description", ""),
            }

    @property
    def names(self) -> list[str]:
        return list(self._skills)

    def description(self, name: str) -> str:
        return self._skills[name]["description"]

    def catalog_prompt(self) -> str:
        """Section 'skills disponibles' a injecter dans le prompt systeme."""
        lines = ["## Skills disponibles (charger via le tool charger_skill AVANT d'appliquer)"]
        for name, s in self._skills.items():
            lines.append(f"- **{name}** : {s['description']}")
        return "\n".join(lines)

    def load(self, name: str) -> str:
        """Corps complet du skill : SKILL.md puis chaque annexe .md (gabarits, references)."""
        if name not in self._skills:
            known = ", ".join(self._skills)
            return f"SKILL_INCONNU : « {name} » n'existe pas. Skills disponibles : {known}."
        d = self._skills[name]["dir"]
        parts = [(d / "SKILL.md").read_text(encoding="utf-8")]
        for annex in sorted(d.rglob("*.md")):
            if annex.name == "SKILL.md":
                continue
            rel = annex.relative_to(d)
            parts.append(f"\n\n--- Annexe du skill {name} : {rel} ---\n\n" + annex.read_text(encoding="utf-8"))
        return "".join(parts)
