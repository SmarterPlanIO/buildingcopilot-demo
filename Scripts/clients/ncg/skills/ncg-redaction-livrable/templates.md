# Gabarits de livrables — Assistant Copro NCG

Squelettes à adapter. Les champs entre crochets `[…]` sont à remplir à partir de
contenu **sourcé** uniquement. Ne jamais inventer pour compléter un champ ; si une
donnée manque, retourne aux tools PALIM ou marque la limite.

---

## note_interne (note interne structurée — gestionnaire NCG)
Destinataire interne. Peut contenir références techniques et `[À VÉRIFIER]`.

```
NOTE INTERNE — [objet]
Copropriété : [nom] (code [code_ncg])    Date : [date]

1. Contexte
[2-4 lignes : la question/le besoin du gestionnaire.]

2. Constat (sourcé)
- [Fait 1]. Source : [document, date].
- [Fait 2]. Source : [document, date].
- [Point incertain]. [À VÉRIFIER] — [comment le lever].

3. Analyse
[Lecture critique : ce que disent les documents, ce qui est inféré (signalé).]

4. Recommandation / prochaines étapes
1. [Action]
2. [Action]

Compteur de cohérence : [N] assertions, dont [X] sourcées, [Y] à vérifier.
[Si Y/N > 20 % : « ALERTE : note non exploitable en l'état, trop d'éléments non sourcés. »]
```

---

## courrier_externe (courrier aux copropriétaires)
Destinataire externe. Zéro jargon interne. Aucune assertion non sourcée.

```
[En-tête : logo `logo NCG.png` (fichier du projet) + coordonnées du syndic NCG]

Objet : [objet clair]
Référence : [résidence / adresse]
[Lieu], le [date]

Madame, Monsieur,

[Corps : information factuelle, une idée par paragraphe. Citer les décisions d'AG
au plus près (« lors de l'assemblée générale du [date], la résolution n°[..] a
décidé … »). Aucune donnée non sourcée.]

[Le cas échéant : prochaine étape, date, ce qui est attendu des copropriétaires.]

Nous restons à votre disposition pour toute information complémentaire.

Veuillez agréer, Madame, Monsieur, l'expression de nos salutations distinguées.

[Le syndic — NCG]
```

---

## note_conseil_syndical (note au conseil syndical)
Destinataire externe (CS). Ton : factuel, orienté aide à la décision. Sources citées sobrement.

```
[En-tête : logo `logo NCG.png` (fichier du projet)]

NOTE AU CONSEIL SYNDICAL
Copropriété : [nom]    Objet : [objet]    Date : [date]

1. Objet
[1-2 lignes.]

2. Éléments factuels
- [Élément]. (Source : [document, date].)
- [Élément]. (Source : [document, date].)

3. Points d'attention / options
[Présentation neutre des options ; ne pas trancher à la place du CS/syndic.]

4. Proposition
[Recommandation, en rappelant ce qui relève d'une décision d'AG.]
```

---

## email_prestataire (email à un prestataire)
Destinataire externe. Bref, précis, traçable.

```
Objet : [objet — réf. contrat/dossier si connue et sourcée]

Bonjour,

Concernant la copropriété [nom/adresse], [demande précise : intervention,
devis, relance, pièce manquante].

[Le cas échéant : référence au contrat / au sinistre, date, échéance souhaitée.]

Merci de nous revenir [délai]. Bien cordialement,
[Gestionnaire — NCG]
```
