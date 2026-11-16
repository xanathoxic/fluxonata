"""Classe de journalisation de gunicorn qui masque les jetons d'adresse (`--logger-class core.gunicorn_logging.RedactingLogger`).

Le filtre est posé sur les gestionnaires de gunicorn (journal d'accès ET journal d'erreurs, par exemple « Error handling request <adresse> ») :
le message est formaté puis réécrit par `core.urlredact`. Ni l'adresse reçue par l'application ni son comportement ne changent."""

from gunicorn.glogging import Logger

from .logging import RedactUrlTokensFilter


class RedactingLogger(Logger):
    def setup(self, cfg):
        super().setup(cfg)
        for log in (self.error_log, self.access_log):
            for handler in log.handlers:
                if getattr(handler, "_gunicorn", False) and not any(
                    isinstance(f, RedactUrlTokensFilter) for f in handler.filters
                ):
                    handler.addFilter(RedactUrlTokensFilter())
