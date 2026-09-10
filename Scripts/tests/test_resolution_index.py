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


# ── Correctifs 01/09 : cas REELS de la revue sur pieces (non-regression) ──

def test_c1_formulaire_vierge_8050():
    """A1 reel : gabarit a trous 'ADOPTEE /REJETEE A L UNANIMITE / LA MAJORITE'."""
    text = """7. AUTORISATION DONNEE AU SYNDIC DE SIGNER UNE CONVENTION AVEC VINCI
Le conseil syndical validera le montant définitif de l'indemnité.
POUR :  CONTRE :  ABSTENTIONS :
CETTE RESOLUTION EST ADOPTEE /REJETEE A L'UNANIMITE / LA MAJORITE"""
    r = index_resolution(text)
    assert r["resultat"] == "indetermine", r
    assert "formulaire_vierge" in r["flags"], r
    r2 = index_resolution("""06 - QUITUS
POUR .. COPROPRITAIRE(S) TOTALISANT ./.. TANTIMES. CONTRE .. SABSTIENNENT ..
RSOLUTION ADOPTE/REJETE LA MAJORIT/LUNANIMIT DES VOIX""")
    assert r2["resultat"] == "indetermine" and "formulaire_vierge" in r2["flags"], r2
    print("OK c1 formulaire vierge (2 variantes reelles) -> indetermine + flag")


def test_c2_revote_5548():
    """A2 reel : 'Resolution revotee a l article 25.1 ci-apres' = pas de resultat ici."""
    text = """08 - DESIGNATION DU SYNDIC
Majorité requise: article 25
Pour: 61 copropriétaire(s) totalisant 45757/100000 tantièmes
Contre :  Néant  Abstention :  Néant
RESOLUTION REVOTEE A L'ARTICLE 25.1 CI-APRES."""
    r = index_resolution(text)
    assert r["resultat"] == "indetermine", r
    assert "revote_25_1" in r["flags"], r
    print("OK c2 revote 25-1 -> indetermine + flag (jamais adoptee via revotEE)")


def test_c3_faux_numero_exclu():
    from resolution_index import _extract_numero, _norm
    assert _extract_numero(_norm("61 copropriétaire(s) totalisant 45757 tantièmes")) is None
    assert _extract_numero(_norm("11 MEMBRES TITULAIRES  TYPE DE VOTE")) is None
    assert _extract_numero(_norm("17-1 SONDAGE ET PURGE DES ELEMENTS")) == "17-1"
    assert _extract_numero(_norm("11 - DESIGNATION DU SCRUTATEUR")) == "11"
    print("OK c3 faux numeros exclus, vrais numeros conserves")


def test_c45_sonde_C1_5499():
    """C1 reel (etait REJETEE 0/7 haute confiance !) : adoption art. 25 proclamee."""
    text = """13- MONTANT DES MARCHES DE TRAVAUX A PARTIR DUQUEL UNE MISE EN
CONCURRENCE EST RENDUE OBLIGATOIRE
L'assemblée générale, statuant dans les conditions de majorité de l'article 25,
fixe à 1500 euros H.T. le montant à partir duquel la mise en concurrence est obligatoire.
Votent pour : 7 copropriétaires présents ou représentés totalisant 713 tantièmes.
Vote contre : 0 copropriétaire présent ou représenté totalisant 0 tantième.
S'abstient : 0 copropriétaire présent ou représenté totalisant 0 tantième.
Absents : 3 copropriétaires totalisant 287 tantièmes.
En vertu de quoi, cette résolution est adoptée dans les conditions de majorité de l'article 25."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee", r
    assert r["decompte"]["pour"] == 713 and r["decompte"]["contre"] == 0, r
    print("OK sonde C1 (5499) : ex-faux rejet -> ADOPTEE, decompte 713/0 (totalisant)")


def test_c45_sonde_C2_5548():
    """C2 reel (etait REJETEE 0/3907) : appels de fonds pourcents AVANT le decompte."""
    text = """52 - APPELS DE PROVISIONS
Le 1er janvier 2021 pour 40 % Le 1er avril 2021 pour 30 % Le 1er juillet 2021 pour 30%
Pour le Bâtiment Messine 15% Pour Bätiment Hoche 35% Pour escalier Hoche A 50%
POUR : 36874 sur 40781 tantièmes. CONTRE : 3907 sur 40781 tantièmes. RAMASSAMY
FRANCOIS (969), FRANCILLARD EMMANUEL (909) ABSTENTIONS : 2768 tantièmes.
Majorité de l'article 24 : cette résolution est adoptée."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee", r
    assert r["decompte"]["pour"] == 36874 and r["decompte"]["contre"] == 3907, r
    print("OK sonde C2 (5548) : dernier-gagne -> ADOPTEE 36874/3907 (pas les 40 pourcents)")


def test_c5_sonde_B1_5427():
    """B1 reel (contradictoire fantome) : Votent pour 33 coproprietaires...605 tantiemes."""
    text = """9. ANNULATION DE LA RESOLUTION N°11 DE L'A.G. DU 8 AVRIL 2010
L'assemblée générale, décide d'annuler la résolution n°11.
Votent pour 33 copropriétaires présents ou représentés totalisant 605 tantièmes.
Vote contre : 4 copropriétaires présent ou représenté totalisant 70 tantièmes.
S'abstient : 1 copropriétaire présent ou représenté totalisant 13 tantièmes.
En vertu de quoi, cette résolution est adoptée dans les conditions de majorité de l'article 25."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee", r
    assert r["decompte"] == {"pour": 605, "contre": 70, "abstention": 13}, r
    print("OK sonde B1 (5427) : tantiemes preferes au nb de coproprietaires -> ADOPTEE")


def test_c7_sonde_B3_zone_bornee():
    """B3 reel : la proclamation de la resolution SUIVANTE ne contamine plus la res.11."""
    seule = """11 REALISATION DES TRAVAUX DE REPARATION DES PORTAILS
Votent pour : 2 copropriétaires présents ou représentés totalisant 100000 tantièmes.
Vote contre : 0 copropriétaire présent ou représenté totalisant 0 tantième.
S'abstient : 0 copropriétaire présent ou représenté totalisant 0 tantième.
En vertu de quoi, cette résolution est adoptée."""
    r11 = index_resolution(seule)
    assert r11["resultat"] == "adoptee", r11
    assert r11["decompte"]["pour"] == 100000 and r11["decompte"]["contre"] == 0, r11
    print("OK sonde B3 (5752) : res.11 -> ADOPTEE 100000/0 (plus de fantome)")


def test_c89_neant_et_desaccentue_5548():
    """D reel : OCR sans accents — Contre: Nant, Rsolution adopte la majorit."""
    text = """06 - Approbation des comptes arrts du 01/01/2012 au 31/12/2012
Majorit requise: article 24
L'assemble gnrale approuve les comptes de l'exercice pour un montant de 198.934,53.
Mise aux voix, cette rsolution a donn lieu au vote suivant:
Pour: 70 copropritaire(s) totalisant 55664 / 55730 tantimes
Contre: Nant
Abstention: KHELIFI (66), soit 1 copropritaire totalisant 66 / 55730 tantimes
Rsolution adopte la majorit des copropritaires prsents et reprsents."""
    r = index_resolution(text)
    assert r["resultat"] == "adoptee", r
    assert r["decompte"]["contre"] == 0, r
    assert r["decompte"]["pour"] == 55664, r
    assert r["source_resultat"] == "decompte+proclamation", r
    print("OK sonde D (5548-2013) : Nant=0 + Rsolution adopte desaccentue -> ADOPTEE haute")


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
    test_c1_formulaire_vierge_8050()
    test_c2_revote_5548()
    test_c3_faux_numero_exclu()
    test_c45_sonde_C1_5499()
    test_c45_sonde_C2_5548()
    test_c5_sonde_B1_5427()
    test_c7_sonde_B3_zone_bornee()
    test_c89_neant_et_desaccentue_5548()
    print("\nTous les tests C1 resolution_index passent.")
