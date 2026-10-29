# T01 — Réception d'un e-mail, analyse, décision

**Objectif** : un message arrive, le workflow l'analyse, le classe et propose une réponse ; une réclamation urgente est signalée.
**Impact hors plate-forme** : aucun. L'e-mail est **simulé** par un webhook signé ; la notification est l'action simulée
(« Envoyer une notification (simulé) »), qui n'envoie rien.

## Prérequis
- Espace de recette, assistant IA activé (voir le [plan](../01-plan-de-tests.md)).
- Un webhook entrant sur le workflow publié (Déclencheurs → Webhooks entrants), dont le secret est exporté dans `FLUXONATA_WEBHOOK_SECRET`.

## Prompt à coller dans « Générer avec l'IA »
```text
Crée un workflow « Analyse d'un e-mail entrant ».
Informations demandées au lancement : « expediteur » (adresse de l'expéditeur), « objet » (objet du message), « corps » (texte du message).
Ce qu'il doit faire, dans l'ordre :
1. Analyser le texte du message avec l'action « Statistiques de texte » (nombre de mots, mot le plus long).
2. Classer le message dans une catégorie « reclamation », « question » ou « autre » avec une tâche script :
   « reclamation » si l'objet ou le corps contient « panne », « urgent » ou « inacceptable » ; « question » si le corps contient un « ? » ; sinon « autre ».
3. Si la catégorie est « reclamation », envoyer une notification « Réclamation urgente » à « equipe-support@exemple.test » avec l'action « Envoyer une notification (simulé) ».
   Sinon, ne rien notifier.
4. Dans tous les cas, produire un rapport avec l'action « Créer un rapport (artefact) » intitulé « Courriel analysé » contenant l'expéditeur, l'objet, la catégorie et le nombre de mots.
Sorties du workflow : « categorie » et « notifie » (vrai si une notification a été envoyée).
Ne contacte aucun service externe.
```

## Plan attendu (à relire avant « Valider et générer »)
- Entrées : `expediteur`, `objet`, `corps` ; sorties : `categorie`, `notifie`.
- Étapes : analyse du texte → script de classement → **décision** « reclamation ? » → (oui) notification simulée → rapport.
- Réutilise les actions **existantes** du catalogue (aucune action inventée).
- Hypothèses déclarées par l'assistant sur la casse des mots-clés (insensible à la casse est préférable : le corriger dans le plan sinon).

## Données d'essai (publier, puis lancer avec chacune)
Envoyées par webhook : `python3 ../outils/envoyer_webhook.py <url-du-webhook> @fichier.json`, ou saisies à la main au lancement.

| Cas | `expediteur` | `objet` | `corps` | Attendu |
|---|---|---|---|---|
| A — réclamation | `lea.martin@exemple.test` | `Panne urgente du portail` | `Le portail est en panne depuis ce matin, c'est inacceptable.` | `categorie = reclamation`, `notifie = true` |
| B — question | `lea.martin@exemple.test` | `Horaires` | `Quels sont vos horaires d'ouverture ?` | `categorie = question`, `notifie = false` |
| C — autre | `lea.martin@exemple.test` | `Merci` | `Merci pour votre aide.` | `categorie = autre`, `notifie = false` |
| D — vide | `lea.martin@exemple.test` | `Test` | *(corps vide)* | refus à la saisie (entrée obligatoire) ou catégorie `autre` : **pas de plantage** |

## Vérifications
- [ ] Le brouillon est **valide** (aucune erreur de validation) et le script de classement est marqué « non relu » jusqu'à relecture.
- [ ] Cas A : l'exécution montre **une** notification, avec le destinataire demandé ; cas B et C : aucune.
- [ ] Un rapport Markdown est téléchargeable (onglet Artefacts) dans les trois cas, avec la catégorie et le nombre de mots.
- [ ] Rejouer le cas A avec la même livraison (`X-Fluxonata-Delivery` identique) : réponse `duplicate`, **pas** de seconde exécution.
- [ ] Aucune étape n'a contacté un service réel (les sorties « simulé » le disent).

## Variante : boîte e-mail réelle de recette (facultatif)
Remplace le webhook par un déclencheur **Interrogation → boîte e-mail (IMAP)** sur une **boîte dédiée aux tests** (jamais une boîte
de production ni personnelle). IMAP exige TLS avec un certificat valide. Utiliser « Essayer » (montre ce qui serait lancé **sans rien lancer**) avant d'activer ;
le premier relevé ne mémorise que l'existant. Le message de test est envoyé à la main depuis un autre compte de recette. Les messages sont lus sans être modifiés.

## Nettoyage
Désactiver puis supprimer le webhook ; déprécier le workflow `Analyse d'un e-mail entrant`.
