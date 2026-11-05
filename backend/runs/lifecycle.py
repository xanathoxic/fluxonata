"""Arrêt propre d'un process (AR2.6) : SIGTERM / SIGINT demandent l'arrêt, ils ne tuent pas au milieu d'un travail.

Premier signal : le process finit CE qu'il fait (une action en cours, un tour d'orchestrateur), n'en commence pas d'autre, s'efface du registre de santé et sort.
Second signal : arrêt immédiat (comportement habituel). Si l'orchestrateur de conteneurs tue quand même le process (délai `stop_grace_period` dépassé),
la reprise sur bail existante rend le travail à un autre process : rien n'est perdu, c'est seulement plus lent.
"""

import logging
import signal
import threading


def install(stop: threading.Event, log: logging.Logger) -> None:
    """À appeler depuis le fil principal (les gestionnaires de signaux de Python l'exigent)."""

    def handler(signum, frame):
        if stop.is_set():  # deuxième demande : on ne retient plus personne
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)
            return
        log.info(
            "arrêt demandé (signal %s) : le travail en cours se termine, rien de nouveau ne démarre",
            signal.Signals(signum).name,
        )
        stop.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
