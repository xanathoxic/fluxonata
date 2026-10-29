# Intégrations prêtes à l'emploi

Ce fichier est **généré** : `cd backend && ../.venv/bin/python manage.py integrations_doc` (ne pas l'éditer à la main ;
la source est `backend/catalog/integrations/catalog.py`).

Chaque intégration est une description (type de connexion, opérations, paramètres) utilisée par l'élément *Requête HTTP* :
dans l'éditeur, ouvrez « Intégrations » dans la palette, choisissez un service puis une opération, renseignez les paramètres
(valeurs fixes ou `{{ expressions }}`). Le **mode test** est actif par défaut (sortie d'exemple, rien n'est envoyé).

**État de vérification** : ces descriptions sont écrites d'après la documentation publique des services et validées contre un
faux serveur (assemblage de la requête, authentification, pagination, détection d'erreur). Elles n'ont **pas été exécutées
contre les vrais services** : testez chaque opération avec un compte de recette avant de l'utiliser en production.
Hors périmètre actuel (pas de requête HTTP) : bases SQL, LDAP, SFTP, S3 (signature SigV4).


26 intégrations, 69 opérations.


## Agents et outils

### Serveur MCP externe

Appelle les outils d'un serveur MCP (Model Context Protocol) accessible en HTTP. Limite : serveurs sans session (chaque appel est indépendant).  
Connexion : `http_bearer` — `https://mcp.exemple.org/mcp`. Jeton porteur du serveur MCP (si le serveur en exige un).

- **Lister les outils** (`POST /`) — Demande la liste des outils du serveur. Obligatoire : —.
- **Appeler un outil** (`POST /`) — Appelle un outil par son nom avec ses arguments. Obligatoire : Nom de l'outil.


## Astreinte

### Opsgenie

Créer, lister et clore des alertes Opsgenie.  
Connexion : `api_key` — `https://api.opsgenie.com/v2`. Clé d'API d'une intégration Opsgenie (« API »).

- **Créer une alerte** (`POST /alerts`) — Crée une alerte. Obligatoire : Message.
- **Lister les alertes** (`GET /alerts`) — Liste les alertes. Obligatoire : —.
- **Clore une alerte** (`POST /alerts/{identifier}/close`) — Ferme une alerte par identifiant. Obligatoire : Identifiant.

### PagerDuty

Lister et créer des incidents PagerDuty (API REST v2).  
Connexion : `api_key` — `https://api.pagerduty.com`. Clé d'API REST PagerDuty (en lecture seule pour lister).

- **Lister les incidents** (`GET /incidents`) — Incidents sur une période. Obligatoire : —.
- **Créer un incident** (`POST /incidents`) — Ouvre un incident sur un service. Obligatoire : Adresse e-mail de l'utilisateur, Incident.


## Code

### GitHub

Tickets, commentaires et déclenchement de workflows GitHub Actions.  
Connexion : `http_bearer` — `https://api.github.com`. Jeton d'accès personnel à portée limitée (repo, workflow).

- **Créer un ticket** (`POST /repos/{owner}/{repo}/issues`) — Crée un ticket dans un dépôt. Obligatoire : Propriétaire, Dépôt, Titre.
- **Lire un ticket** (`GET /repos/{owner}/{repo}/issues/{number}`) — Lit un ticket ou une demande de fusion. Obligatoire : Propriétaire, Dépôt, Numéro.
- **Lister les tickets** (`GET /repos/{owner}/{repo}/issues`) — Liste les tickets d'un dépôt (toutes les pages, 5 au plus). Obligatoire : Propriétaire, Dépôt.
- **Commenter** (`POST /repos/{owner}/{repo}/issues/{number}/comments`) — Ajoute un commentaire à un ticket. Obligatoire : Propriétaire, Dépôt, Numéro, Commentaire.
- **Lancer un workflow Actions** (`POST /repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches`) — Déclenche un workflow (workflow_dispatch). Obligatoire : Propriétaire, Dépôt, Fichier du workflow, Branche.

### GitLab

Tickets et pipelines GitLab (gitlab.com ou auto-hébergé).  
Connexion : `api_key` — `https://gitlab.com/api/v4`. Jeton d'accès (portée api) ; en-tête PRIVATE-TOKEN.

- **Créer un ticket** (`POST /projects/{project}/issues`) — Crée un ticket dans un projet. Obligatoire : Projet (identifiant ou chemin), Titre.
- **Lister les tickets** (`GET /projects/{project}/issues`) — Liste les tickets d'un projet. Obligatoire : Projet.
- **Lancer un pipeline** (`POST /projects/{project}/pipeline`) — Crée un pipeline sur une branche. Obligatoire : Projet, Branche.
- **Lire un pipeline** (`GET /projects/{project}/pipelines/{pipeline_id}`) — Retourne l'état d'un pipeline. Obligatoire : Projet, Identifiant.


## Documentation

### Confluence (Cloud)

Lire, chercher et créer des pages Confluence.  
Connexion : `basic` — `https://VOTRE-SITE.atlassian.net/wiki/rest/api`. Identifiant : votre adresse e-mail ; secret : jeton d'API Atlassian.

- **Lire une page** (`GET /content/{id}`) — Lit une page avec son contenu. Obligatoire : Identifiant.
- **Rechercher (CQL)** (`GET /search`) — Recherche par requête CQL. Obligatoire : Requête CQL.
- **Créer une page** (`POST /content`) — Crée une page (objet de l'API). Obligatoire : Contenu.

### Notion

Créer et lire des pages et bases Notion.  
Connexion : `http_bearer` — `https://api.notion.com/v1`. Intégration interne Notion ; partagez les pages avec elle.

- **Lire une page** (`GET /pages/{page_id}`) — Retourne une page. Obligatoire : Identifiant.
- **Créer une page** (`POST /pages`) — Crée une page dans une page ou une base. Obligatoire : Parent, Propriétés.
- **Interroger une base** (`POST /databases/{database_id}/query`) — Liste les lignes d'une base. Obligatoire : Base.


## Données

### Airtable

Lire et ajouter des enregistrements Airtable.  
Connexion : `http_bearer` — `https://api.airtable.com/v0`. Jeton d'accès personnel Airtable (data.records:read/write).

- **Lister les enregistrements** (`GET /{base_id}/{table}`) — Toutes les lignes d'une table (pagination automatique). Obligatoire : Base, Table.
- **Ajouter des enregistrements** (`POST /{base_id}/{table}`) — Ajoute jusqu'à 10 lignes. Obligatoire : Base, Table, Enregistrements.

### Elasticsearch

Rechercher, lire et indexer des documents.  
Connexion : `api_key` — `https://VOTRE-ELASTIC:9200`. Clé d'API Elasticsearch encodée en base 64 (id:clé).

- **Rechercher** (`POST /{index}/_search`) — Requête de recherche sur un index. Obligatoire : Index.
- **Indexer un document** (`POST /{index}/_doc`) — Ajoute un document. Obligatoire : Index, Document.
- **Lire un document** (`GET /{index}/_doc/{id}`) — Lit un document par identifiant. Obligatoire : Index, Identifiant.

### HubSpot

Créer et rechercher des contacts HubSpot.  
Connexion : `http_bearer` — `https://api.hubapi.com`. Jeton d'une application privée HubSpot (droits crm.objects.contacts).

- **Créer un contact** (`POST /crm/v3/objects/contacts`) — Crée un contact. Obligatoire : Propriétés.
- **Rechercher des contacts** (`POST /crm/v3/objects/contacts/search`) — Recherche texte. Obligatoire : Texte.
- **Lire un contact** (`GET /crm/v3/objects/contacts/{contact_id}`) — Lit un contact. Obligatoire : Identifiant.

### InfluxDB

Écrire des points et interroger InfluxDB 2.  
Connexion : `api_key` — `https://VOTRE-INFLUX:8086`. Jeton d'API InfluxDB (lecture/écriture sur le bucket).

- **Écrire des points** (`POST /api/v2/write`) — Écrit en protocole ligne. Obligatoire : Organisation, Bucket, Lignes.
- **Interroger (Flux)** (`POST /api/v2/query`) — Exécute une requête Flux (résultat CSV). Obligatoire : Organisation, Requête Flux.


## Intégration continue

### Jenkins

Lancer des jobs Jenkins et lire leur dernier résultat.  
Connexion : `basic` — `https://VOTRE-JENKINS`. Identifiant : utilisateur Jenkins ; secret : jeton d'API de cet utilisateur (évite le jeton anti-CSRF).

- **Lancer un job** (`POST /job/{job}/build`) — Déclenche un job sans paramètre. Obligatoire : Job.
- **Lancer un job paramétré** (`POST /job/{job}/buildWithParameters`) — Déclenche un job avec ses paramètres. Obligatoire : Job.
- **Dernier résultat** (`GET /job/{job}/lastBuild/api/json`) — Informations sur la dernière exécution. Obligatoire : Job.


## Messagerie

### Mailgun

Envoyer des e-mails par Mailgun.  
Connexion : `basic` — `https://api.eu.mailgun.net/v3`. Identifiant « api » ; secret : clé d'API privée (adaptez la région : api.mailgun.net pour les États-Unis).

- **Envoyer un e-mail** (`POST /{domain}/messages`) — Envoie un message. Obligatoire : Domaine d'envoi, Expéditeur, Destinataire, Objet, Texte.

### Microsoft 365 (Graph)

Utilisateurs et envoi d'e-mails par Microsoft Graph (droits d'application).  
Connexion : `oauth2_client` — `https://graph.microsoft.com/v1.0`. Inscription d'application avec secret client et droits d'application (User.Read.All, Mail.Send).

- **Lister les utilisateurs** (`GET /users`) — Annuaire Entra ID. Obligatoire : —.
- **Lire un utilisateur** (`GET /users/{user}`) — Par identifiant ou nom principal. Obligatoire : Utilisateur.
- **Envoyer un e-mail** (`POST /users/{user}/sendMail`) — Envoie un message au nom d'un utilisateur. Obligatoire : Expéditeur (nom principal), Message.

### SendGrid

Envoyer des e-mails par SendGrid.  
Connexion : `http_bearer` — `https://api.sendgrid.com/v3`. Clé d'API SendGrid avec la permission Mail Send.

- **Envoyer un e-mail** (`POST /mail/send`) — Envoie un message (API v3). Obligatoire : Destinataires, Expéditeur, Objet, Contenu.

### Slack

Envoyer des messages et lister les canaux d'un espace Slack.  
Connexion : `http_bearer` — `https://slack.com/api`. Créez une application Slack, ajoutez les droits chat:write et channels:read, collez le jeton « Bot User OAuth Token ».

- **Envoyer un message** (`POST /chat.postMessage`) — Publie un message dans un canal. Obligatoire : Canal (identifiant ou nom), Texte.
- **Lister les canaux** (`GET /conversations.list`) — Liste les canaux publics. Obligatoire : —.

### Twilio

Envoyer et lister des SMS.  
Connexion : `basic` — `https://api.twilio.com/2010-04-01`. Identifiant : Account SID ; secret : Auth Token. Messages facturés par Twilio.

- **Envoyer un SMS** (`POST /Accounts/{account_sid}/Messages.json`) — Envoie un SMS. Obligatoire : Account SID, Destinataire (+33…), Expéditeur, Texte.
- **Lister les messages** (`GET /Accounts/{account_sid}/Messages.json`) — Derniers messages. Obligatoire : Account SID.


## Stockage

### Stockage S3 (AWS, MinIO, Scaleway…)

Lister, lire, écrire et supprimer des objets d'un stockage compatible S3 (accès par chemin : /bucket/clé).  
Connexion : `aws_sigv4` — `https://s3.eu-west-3.amazonaws.com`. Identifiant : l'Access Key ID est saisi dans la configuration (visible) ; le secret est la Secret Access Key (chiffrée). Adaptez l'adresse et la région.

- **Lister les objets** (`GET /{bucket}`) — Liste les objets d'un bucket (version 2). Obligatoire : Bucket.
- **Lire un objet** (`GET /{bucket}/{key}`) — Télécharge le contenu d'un objet texte (réponse tronquée à 256 Ko). Obligatoire : Bucket, Clé de l'objet.
- **Écrire un objet** (`PUT /{bucket}/{key}`) — Dépose un objet texte (remplace s'il existe). Obligatoire : Bucket, Clé de l'objet, Contenu.
- **Supprimer un objet** (`DELETE /{bucket}/{key}`) — Supprime un objet. Obligatoire : Bucket, Clé de l'objet.


## Supervision

### Datadog

Publier des événements et des métriques dans Datadog.  
Connexion : `api_key` — `https://api.datadoghq.com/api`. Clé d'API Datadog ; adaptez le site (datadoghq.eu pour l'Europe).

- **Publier un événement** (`POST /v1/events`) — Ajoute un événement au flux. Obligatoire : Titre, Texte.
- **Envoyer des métriques** (`POST /v2/series`) — Envoie des séries de métriques (API v2). Obligatoire : Séries.

### Grafana

Annotations et recherche de tableaux de bord Grafana.  
Connexion : `http_bearer` — `https://VOTRE-GRAFANA/api`. Jeton de compte de service Grafana (rôle Éditeur pour les annotations).

- **Créer une annotation** (`POST /annotations`) — Marque un événement sur les graphiques. Obligatoire : Texte.
- **Rechercher** (`GET /search`) — Cherche des tableaux de bord et dossiers. Obligatoire : —.

### Sentry

Lister et résoudre des problèmes Sentry.  
Connexion : `http_bearer` — `https://sentry.io/api/0`. Jeton d'authentification d'une intégration interne (project:read, event:admin).

- **Lister les problèmes** (`GET /projects/{organization}/{project}/issues/`) — Problèmes d'un projet. Obligatoire : Organisation, Projet.
- **Résoudre un problème** (`PUT /issues/{issue_id}/`) — Passe un problème à « résolu ». Obligatoire : Identifiant.

### Zabbix

Interroger et piloter Zabbix par son API JSON-RPC (6.4 ou plus récent).  
Connexion : `http_bearer` — `https://VOTRE-ZABBIX`. Jeton d'API Zabbix (Administration → Jetons API), utilisé en en-tête Bearer.

- **Appel d'API** (`POST /api_jsonrpc.php`) — Appelle une méthode de l'API Zabbix. Obligatoire : Méthode, Paramètres.
- **Problèmes en cours** (`POST /api_jsonrpc.php`) — Liste les problèmes récents. Obligatoire : —.


## Tickets

### Jira (Cloud)

Créer, lire, commenter, rechercher et faire avancer des tickets Jira.  
Connexion : `basic` — `https://VOTRE-SITE.atlassian.net/rest/api/2`. Identifiant : votre adresse e-mail ; secret : un jeton d'API Atlassian.

- **Lire un ticket** (`GET /issue/{key}`) — Retourne un ticket par sa clé. Obligatoire : Clé du ticket.
- **Créer un ticket** (`POST /issue`) — Crée un ticket (objet « fields » de l'API Jira). Obligatoire : Champs.
- **Commenter un ticket** (`POST /issue/{key}/comment`) — Ajoute un commentaire. Obligatoire : Clé du ticket, Commentaire.
- **Rechercher (JQL)** (`GET /search`) — Recherche de tickets par requête JQL. Obligatoire : Requête JQL.
- **Changer l'état** (`POST /issue/{key}/transitions`) — Applique une transition à un ticket. Obligatoire : Clé du ticket, Transition ({"id": "31"}).

### Linear

Requêtes GraphQL vers Linear.  
Connexion : `api_key` — `https://api.linear.app`. Clé d'API personnelle Linear (utilisée telle quelle, sans « Bearer »).

- **Requête GraphQL** (`POST /graphql`) — Exécute une requête ou une mutation. Obligatoire : Requête.

### ServiceNow

Lire, créer et mettre à jour des enregistrements (incidents, demandes…).  
Connexion : `basic` — `https://VOTRE-INSTANCE.service-now.com/api/now`. Compte de service avec droits sur la table API (rôle rest_api_explorer ou équivalent).

- **Lister des enregistrements** (`GET /table/{table}`) — Liste une table avec une requête. Obligatoire : Table.
- **Lire un enregistrement** (`GET /table/{table}/{sys_id}`) — Lit un enregistrement par son sys_id. Obligatoire : Table, sys_id.
- **Créer un enregistrement** (`POST /table/{table}`) — Crée un enregistrement (champs de la table). Obligatoire : Table, Champs.
- **Mettre à jour un enregistrement** (`PATCH /table/{table}/{sys_id}`) — Modifie des champs. Obligatoire : Table, sys_id, Champs.

### Zendesk

Créer, lire et rechercher des tickets Zendesk.  
Connexion : `basic` — `https://VOTRE-SOUS-DOMAINE.zendesk.com/api/v2`. Identifiant : « adresse/token » ; secret : jeton d'API Zendesk.

- **Créer un ticket** (`POST /tickets`) — Crée un ticket. Obligatoire : Ticket.
- **Lire un ticket** (`GET /tickets/{id}`) — Lit un ticket. Obligatoire : Identifiant.
- **Rechercher** (`GET /search.json`) — Recherche texte. Obligatoire : Requête.

## Icônes

Chaque intégration affiche le logo du service ciblé (palette, étape sur le schéma, panneau de propriétés, liste des connexions, assistant de connexion). Les logos viennent de Simple Icons (CC0-1.0) via `scripts/brand-icons.py` ; un service sans logo disponible reçoit un monogramme coloré. Pour ajouter un service, ajoutez-le à la table `MAP` du script puis relancez-le.
