"""Tests C1 — resolution_index (détecteur de résultat de résolution, 2 canaux).

Cas du plan (PLAN_FIABILITE_SYNTHESE.md) : PV de l'incident (rés. 3 rejetée /
rés. 4 adoptée), unanimité sans chiffres, tableau illisible + conclusion nette,
proclamation ACTIVE post-décompte, discordance, résolution tronquée, retirée,
subtilité art. 25 (adoption non calculable sans total).

Exécution (pas besoin de pytest, aucune connexion DB) :
    cd Scripts && PYTHONIOENCODING=utf-8 python tests/test_resolution_index.py
"""
import os
import sys

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

from resolution_index import index_chunks, index_resolution  # noqa: E402


def test_incident_res3_rejetee():
    """Le PV de l'incident : dispositif « approuve », décompte de REJET, proclamation.
    C'est LE cas qui a piégé le narratif Haiku."""
    text = """TROISIÈME RÉSOLUTION — Approbation des comptes de l'exercice 2023
L'assemblée générale approuve sans réserve les comptes de l'exercice 2023, pour un
total de répartition de 170 215,78 euros et un solde débiteur de 15 213,21 euros.
Votants : ont voté pour : 2 606 tantièmes (4 copropriétaires : CDC HABITAT, DANCOINE,
indivision PACREAU, SEINE OUEST AMENAGEMENT)
ont voté contre : 4 867 tantièmes (35 copropriétaires)
abstention : 58 tantièmes
La résolution est rejetée à la majorité de l'article 24."""
    r = index_resolution(text)
    assert r["resultat"] == "rejetee", r
    assert r["source_resultat"] == "decompte+proclamation", r
    assert r["confiance"] == "haute", r
    assert r["decompte"]["pour"] == 2606 and r["decompte"]["contre"] == 4867, r
    assert r["article_majorite"] == "24", r
    print("OK incident res.3 : dispositif 'approuve' + decompte rejet -> REJETEE (haute)")


def test_incident_res4_adoptee():
    text = """QUATRIÈME RÉSOLUTION — Approbation des comptes de l'exercice 2025
L'assemblée générale approuve les comptes de l'exercice 2025 (total de répartition
184 133,21 euros, solde débiteur 29 129,73 euros). Le conseil syndical fait consigner
une remarque sur une facture d'eau anormalement élevée signalée à Homeland.
Ont voté pour : 7 473 tantièmes. Ont voté contre : 0. Abstention : 58 tantièmes.
Cette résolution est adoptée à la majorité de l'article 24."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee" and r["confiance"] == "haute", r
    assert r["decompte"] == {"pour": 7473, "contre": 0, "abstention": 58}, r
    print("OK incident res.4 : comptes 2025 -> ADOPTEE (haute)")


def test_proclamation_active_post_decompte():
    """Correction Thai : la proclamation peut être ACTIVE (« l'assemblée approuve »)
    APRÈS le décompte — le positionnel prime sur la grammaire."""
    text = """CINQUIÈME RÉSOLUTION — Travaux de ravalement
Le syndic propose d'engager les travaux de ravalement pour 250 000 euros.
Pour : 6 200 tantièmes. Contre : 1 800 tantièmes. Abstentions : 0.
En conséquence, l'assemblée générale approuve les travaux (article 24)."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee", r
    assert r["source_resultat"] == "decompte+proclamation", r
    print("OK proclamation ACTIVE apres decompte -> constat retenu (positionnel)")


def test_tableau_illisible_conclusion_nette():
    """Cas Thai : tableau massacré par le scan (nombres morts) mais conclusion claire.
    L'ancre illisible localise ; le canal B tranche."""
    text = """SIXIÈME RÉSOLUTION — Remplacement de la chaudière
L'assemblée générale décide le remplacement de la chaudière collective.
Ont voté pour : |||— tantièmes ($$) Ont voté contre : ~~ // abstention : —
La résolution est adoptée à la majorité de l'article 25."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee", r
    assert r["source_resultat"] == "proclamation", r
    assert r["confiance"] == "moyenne", r
    assert "decompte_illisible" in r["flags"], r
    print("OK tableau illisible + conclusion nette -> ADOPTEE via proclamation (flag)")


def test_unanimite_sans_chiffres():
    text = """SEPTIÈME RÉSOLUTION — Quitus au syndic
L'assemblée générale donne quitus au syndic pour sa gestion.
Cette résolution est adoptée à l'unanimité des présents et représentés."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee" and r["source_resultat"] == "proclamation", r
    print("OK unanimite sans chiffres -> ADOPTEE via proclamation")


def test_discordance_jamais_tranchee():
    text = """HUITIÈME RÉSOLUTION — Budget prévisionnel
L'assemblée approuve le budget prévisionnel 2027.
Ont voté pour : 1 200 tantièmes. Ont voté contre : 5 900 tantièmes. Abstention : 0.
La résolution est adoptée à la majorité de l'article 24."""
    r = index_resolution(text)
    assert r["resultat"] == "contradictoire", r
    assert "decompte_et_proclamation_discordants" in r["flags"], r
    assert r["confiance"] == "basse", r
    print("OK discordance decompte/proclamation -> CONTRADICTOIRE (jamais tranche)")


def test_article_25_adoption_non_calculable():
    """Art. 25 : pour > contre ne prouve PAS l'adoption (majorité absolue requise).
    Sans proclamation -> indetermine ; le rejet, lui, reste calculable."""
    gagne_sans_proc = """NEUVIÈME RÉSOLUTION — Autorisation de travaux privatifs (article 25)
Pour : 4 100 tantièmes. Contre : 2 300 tantièmes. Abstention : 600 tantièmes."""
    r = index_resolution(gagne_sans_proc)
    assert r["resultat"] == "indetermine", r
    assert "majorite_absolue_requise" in r["flags"], r
    rejet = """DIXIÈME RÉSOLUTION (article 25)
Pour : 900 tantièmes. Contre : 5 000 tantièmes. Abstention : 0."""
    r2 = index_resolution(rejet)
    assert r2["resultat"] == "rejetee" and r2["source_resultat"] == "decompte", r2
    print("OK art.25 : adoption non calculable sans total ; rejet calculable")


def test_dispositif_seul_jamais_utilise():
    """Un chunk qui n'a QUE le dispositif (troncature amont) : jamais de résultat."""
    text = """ONZIÈME RÉSOLUTION — Approbation des comptes
L'assemblée générale approuve sans réserve les comptes de l'exercice, pour un total
de répartition de 170 215,78 euros."""
    r = index_resolution(text)
    assert r["resultat"] == "indetermine", r
    print("OK dispositif seul (verbe 'approuve' en debut) -> INDETERMINE, pas de piege")


def test_retiree_et_tronquee():
    r = index_resolution("""DOUZIÈME RÉSOLUTION — Vente du logement de gardien
Cette résolution est retirée de l'ordre du jour à la demande du conseil syndical.""")
    assert r["resultat"] == "retiree" and r["confiance"] == "haute", r
    r2 = index_resolution("""TREIZIÈME RÉSOLUTION — Contrat d'entretien
L'assemblée décide de renouveler le contrat. Ont voté pour : 3 100""")
    assert r2["resultat"] == "indetermine", r2
    print("OK retiree (haute) + texte mourant sur l'ancre -> indetermine")


def test_index_chunks_metadonnees():
    rows = [("c1", "PV 2026.pdf", "2026-03-30",
             "Pour : 10 tantièmes. Contre : 90 tantièmes. La résolution est rejetée.")]
    out = index_chunks(rows)
    assert out[0]["chunk_id"] == "c1" and out[0]["source_file"] == "PV 2026.pdf", out
    assert out[0]["resultat"] == "rejetee", out
    print("OK index_chunks : metadonnees propagees")


def test_groupement_suite_resolution():
    """C2 : les fragments '[Suite resolution N]' se rattachent au chunk precedent ;
    le decompte arrivant dans le DERNIER fragment etablit le resultat du GROUPE
    (le cas des 'contradictoires' du smoke par chunk isole)."""
    from resolution_index import group_chunks, index_document
    doc = [
        ("c1", 1, """DIX-NEUVIÈME RÉSOLUTION — TRAVAUX DE REMPLACEMENT COLONNE MONTANTE RDC
L'assemblée générale décide les travaux de remplacement de la colonne montante,
pour un montant de 48 000 euros TTC, financés par appel de fonds spécial."""),
        ("c2", 2, """[Suite résolution 19- TRAVAUX DE REMPLACEMENT COLONNE MONTANTE RDC]
Modalités de financement : trois appels de fonds aux 1er janvier, 1er avril, 1er juillet."""),
        ("c3", 3, """[Suite résolution 19- TRAVAUX DE REMPLACEMENT COLONNE MONTANTE RDC]
Ont voté pour : 5 900 tantièmes. Ont voté contre : 800 tantièmes. Abstention : 0.
La résolution est adoptée à la majorité de l'article 24."""),
        ("c4", 4, """VINGTIÈME RÉSOLUTION — Questions diverses
Aucune décision n'est prise sur ce point."""),
    ]
    groups = group_chunks(doc)
    assert [len(g) for g in groups] == [3, 1], groups
    out = index_document(doc)
    assert len(out) == 2, out
    r19 = out[0]
    assert r19["chunk_ids"] == ["c1", "c2", "c3"], r19
    assert r19["resultat"] == "adoptee" and r19["confiance"] == "haute", r19
    assert r19["numero"] == "19", r19
    assert "COLONNE MONTANTE" in r19["objet_court"].upper(), r19
    assert out[1]["resultat"] == "indetermine", out[1]
    print("OK groupement suites : decompte du dernier fragment -> resultat du GROUPE")


def test_groupe_orphelin_et_numero_ordinal():
    from resolution_index import index_document
    doc = [("c9", 5, """[Suite résolution 7- RAVALEMENT]
La résolution est adoptée à la majorité de l'article 24.""")]
    out = index_document(doc)
    assert "groupe_orphelin" in out[0]["flags"], out[0]
    doc2 = [("c1", 1, """TROISIÈME RÉSOLUTION — Approbation des comptes
Pour : 100 tantièmes. Contre : 900 tantièmes. Rejetée.""")]
    out2 = index_document(doc2)
    assert out2[0]["numero"] == "3", out2[0]
    assert out2[0]["resultat"] == "rejetee", out2[0]
    print("OK groupe orphelin flague + numero ordinal extrait")


if __name__ == "__main__":
    test_incident_res3_rejetee()
    test_incident_res4_adoptee()
    test_proclamation_active_post_decompte()
    test_tableau_illisible_conclusion_nette()
    test_unanimite_sans_chiffres()
    test_discordance_jamais_tranchee()
    test_article_25_adoption_non_calculable()
    test_dispositif_seul_jamais_utilise()
    test_retiree_et_tronquee()
    test_index_chunks_metadonnees()
    test_groupement_suite_resolution()
    test_groupe_orphelin_et_numero_ordinal()
    print("\nTous les tests C1 resolution_index passent.")
