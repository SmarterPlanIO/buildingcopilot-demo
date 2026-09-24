# Avenant de sous-traitance (RGPD art. 28) — PALIM

> **Projet du 24/09/2026, à faire relire par un juriste avant envoi.** Rédigé à partir de
> l'architecture réellement déployée pour Delacour Patrimoine ; les points marqués
> **[À CONFIRMER]** demandent une vérification factuelle avant signature.
> Objet : donner au cabinet une réponse écrite, vérifiable, à la question « où sont nos
> documents et qui peut les voir ».

---

## 1. Parties et objet

**Responsable de traitement** : le cabinet de syndic (ci-après « le Client »), qui détermine
les finalités et les moyens du traitement de ses documents de copropriété.

**Sous-traitant** : SmarterPlan (ci-après « le Prestataire »), qui traite ces documents pour
le seul compte du Client, sur instruction documentée, aux fins de fournir l'assistant PALIM.

Le présent avenant complète le contrat de prestation et prévaut sur lui pour tout ce qui
concerne la protection des données à caractère personnel.

## 2. Nature et finalité du traitement

| | |
|---|---|
| **Finalité** | permettre au Client d'interroger en langage naturel les documents de son portefeuille (procès-verbaux d'assemblée, règlements de copropriété, contrats, courriers, pièces comptables et de sinistre) |
| **Opérations** | lecture des documents depuis l'espace de stockage du Client, extraction de texte, découpage, calcul d'index sémantique, conservation de l'index, restitution d'extraits en réponse aux questions du Client |
| **Personnes concernées** | copropriétaires, membres du conseil syndical, locataires, salariés du cabinet, prestataires et intervenants cités dans les documents |
| **Catégories de données** | identité, coordonnées, données de logement (lot, tantièmes), données financières (charges, impayés, quotes-parts), correspondance, données relatives à des sinistres et à des procédures |
| **Données sensibles** | le Prestataire ne recherche ni ne traite délibérément de catégories particulières de données (art. 9). Des données de santé ou relatives à des infractions peuvent figurer incidemment dans des dossiers de sinistre ou de contentieux déposés par le Client ; elles suivent le même régime de confidentialité et de sécurité que le reste |
| **Durée** | durée du contrat, puis suppression selon l'article 8 |

## 3. Instructions et périmètre

Le Prestataire ne traite les données que sur instruction documentée du Client. Constituent
des instructions documentées : le présent avenant, le contrat, et le périmètre des
copropriétés déclaré dans la configuration du service.

Le Prestataire **n'utilise les données du Client à aucune autre fin** : ni amélioration d'un
produit, ni statistiques commerciales, ni entraînement de modèles, ni mise en commun avec
d'autres clients. L'isolation entre clients est **technique** : une base de données dédiée
par cabinet, des identifiants distincts, et un contrôle de périmètre côté serveur qui refuse
toute requête ne désignant pas explicitement une copropriété du cabinet.

## 4. Localisation des données

**Toutes les données traitées restent dans l'Union européenne**, région AWS Irlande
(eu-west-1) :

| Composant | Rôle | Localisation |
|---|---|---|
| Espace de stockage documentaire du Client | source des documents, **propriété du Client** | son propre espace (Google Drive partagé), accès **lecture seule** accordé au Prestataire, révocable à tout moment |
| Base de données PostgreSQL | index : texte des extraits + représentations vectorielles | AWS Irlande, **instance dédiée au Client**, chiffrée au repos |
| Service d'extraction de texte (OCR) | lecture des documents scannés | AWS Irlande, traitement transitoire, aucun stockage durable |
| Service de modèles d'IA | calcul de l'index et classement des documents | AWS Irlande |
| Serveur applicatif | expose l'assistant | AWS Irlande, sans stockage |

**Aucun transfert hors UE** n'est nécessaire au fonctionnement du service. [À CONFIRMER : la
plateforme d'observabilité, cf. article 6, et le contrat que le Client a souscrit pour son
assistant conversationnel, qui relève de sa propre relation contractuelle.]

## 5. Ce que le Prestataire conserve — et ce qu'il ne conserve pas

Point central, et engagement de résultat :

- **Le Prestataire ne conserve aucune copie des documents du Client.** Les fichiers sont lus
  dans l'espace du Client, traités, puis effacés des systèmes du Prestataire à l'issue du
  traitement. [À CONFIRMER : effectif après la mise en œuvre prévue au 4e trimestre 2026 ;
  d'ici là, une copie de travail peut subsister entre deux traitements.]
- **Le Prestataire conserve l'index** : le texte des extraits, leurs métadonnées et leurs
  représentations vectorielles. C'est ce qui permet de répondre aux questions et de citer
  les sources. Cet index contient donc du texte issu des documents, y compris des données
  personnelles.
- Le Prestataire conserve des **journaux techniques** (horodatage, identifiant de copropriété,
  durée, code d'erreur) pendant 90 jours, sans contenu documentaire. [À CONFIRMER : durée.]

Le Client est informé que **supprimer un document de son espace de stockage entraîne son
retrait de l'index** au traitement suivant : la suppression se propage.

## 6. Sous-traitants ultérieurs

Le Client autorise les sous-traitants ultérieurs suivants. Le Prestataire l'informe de tout
ajout ou remplacement avec un préavis raisonnable, le Client pouvant s'y opposer pour un
motif légitime.

| Sous-traitant | Rôle | Localisation | Engagement notable |
|---|---|---|---|
| Amazon Web Services (AWS) | hébergement, base de données, OCR, exécution des modèles d'IA | Irlande (eu-west-1) | les données transmises aux modèles ne sont ni conservées ni utilisées pour les entraîner |
| Anthropic | fournisseur des modèles utilisés via AWS | modèles exécutés dans l'infrastructure AWS Irlande | idem |
| Google (espace de stockage du Client) | stockage des documents source | selon le contrat du Client | accès du Prestataire en lecture seule |
| Airtable | base de données du courtier en assurance du Client, interrogée en lecture pour les polices et sinistres | [À CONFIRMER] | relation contractuelle entre le Client et son courtier |
| Langfuse | observabilité du service | [À CONFIRMER : région de l'offre souscrite] | reçoit **les questions posées** et des compteurs, **pas le contenu des documents** ; vérifié dans le code le 24/09/2026 |

Les questions posées par les utilisateurs peuvent contenir des données personnelles
(« où en est le dossier de M. X »). C'est la raison pour laquelle l'observabilité figure
dans ce tableau.

## 7. Sécurité (art. 32)

- Chiffrement en transit (TLS) et au repos (base de données, stockage).
- Base de données dédiée par client, non exposée à Internet, accès par identifiants nominatifs
  distincts pour la lecture et l'administration ; secrets conservés dans un coffre géré.
- Accès aux données du Client limité aux personnes du Prestataire qui en ont besoin, soumises
  à une obligation de confidentialité.
- Les réponses de l'assistant sont **fondées sur des extraits cités** : l'utilisateur peut
  remonter à la source, ce qui limite le risque d'affirmation erronée sur une personne.
- Traçabilité des appels au service (art. 5.2).
- [À CONFIRMER : politique de sauvegarde et de restauration, durée de rétention des
  sauvegardes, procédure de gestion des accès du personnel.]

## 8. Sort des données en fin de contrat

À la demande du Client, à tout moment, et en tout état de cause dans les **30 jours** suivant
la fin du contrat, le Prestataire **supprime l'intégralité des données du Client** : index,
base de données dédiée, copies de travail, sauvegardes. Une attestation de suppression est
remise au Client. Aucune copie n'est conservée, sous réserve des obligations légales de
conservation, qui ne portent pas sur les documents de copropriété.

Le Client peut aussi révoquer unilatéralement l'accès en lecture à son espace de stockage :
le service cesse alors de se mettre à jour, sans autre effet.

## 9. Droits des personnes

Le Prestataire assiste le Client dans sa réponse aux demandes d'exercice de droits. En
pratique :

- **accès / rectification** : les documents faisant foi sont ceux de l'espace du Client ;
  l'index en est le reflet ;
- **effacement** : le Client retire le document de son espace ; l'index se met à jour au
  traitement suivant. Un effacement immédiat de l'index peut être demandé au Prestataire ;
- **opposition, limitation** : le Prestataire peut exclure une copropriété ou un ensemble de
  documents du périmètre indexé.

Délai d'assistance : **5 jours ouvrés** à compter de la demande du Client. [À CONFIRMER.]

## 10. Violation de données

Le Prestataire notifie le Client **sans délai injustifié et au plus tard 48 heures** après en
avoir pris connaissance, avec la nature de la violation, les catégories et le volume
approximatif de données concernées, les conséquences probables et les mesures prises. Il
assiste le Client dans sa notification éventuelle à la CNIL et aux personnes concernées.

## 11. Audit

Le Prestataire met à disposition du Client toute information nécessaire pour démontrer le
respect de l'article 28, et permet la réalisation d'audits, y compris d'inspections, par le
Client ou un auditeur qu'il mandate, dans la limite d'**un audit par an** hors incident, sur
préavis raisonnable et sans perturber l'exploitation.

---

## Points à trancher avant envoi (interne SmarterPlan)

1. **La copie de travail (art. 5)** : l'engagement « aucune copie conservée » n'est
   pleinement tenable qu'une fois la purge automatique en place. Deux options : annoncer
   l'engagement avec la date de mise en œuvre (transparent, engageant), ou décrire l'état
   actuel (copie de travail conservée entre deux traitements, chiffrée, sur un serveur
   maîtrisé). **Ne pas écrire ce qui n'est pas encore vrai.**
2. **Langfuse** : confirmer la région de l'offre souscrite. Si elle est hors UE, deux
   sorties : basculer sur la région européenne, ou désactiver l'observabilité pour ce
   client (variable d'environnement, sans effet sur le service).
3. **Lieu d'exécution** : l'ingestion tourne aujourd'hui sur un serveur de bureau. Un
   environnement AWS dédié serait plus simple à défendre en audit, et cohérent avec une
   mise à jour nocturne automatique.
4. **Durées** : journaux techniques, sauvegardes, délai d'assistance aux droits — à fixer.
5. **Le contrat de l'assistant conversationnel** (Claude) est souscrit par le Client : le
   dire explicitement pour ne pas endosser une responsabilité qui n'est pas la nôtre.
6. Cet avenant est **générique** : il servira pour les prospects. Le faire relire une fois,
   sérieusement, vaut mieux que de l'adapter cabinet par cabinet.
