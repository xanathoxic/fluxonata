"""Extensions (ADR 0030) : des modules additionnels que l'on branche, ou non, sur la plateforme sans toucher à son code.

Une extension est une application Django installée par l'administrateur (jamais téléversée depuis l'interface) et nommée dans `ORCH_EXTENSIONS`.
Elle décrit ce qu'elle apporte dans un manifeste (`<paquet>.plugin.MANIFEST`) : entrées de menu, page d'interface, routes d'API.
Le cœur ne dépend d'aucune extension ; une extension absente ou refusée ne gêne jamais le démarrage.
"""

EXTENSION_API = (
    1  # contrat entre le cœur et les extensions : un manifeste déclare celui qu'il vise, un autre est refusé
)
