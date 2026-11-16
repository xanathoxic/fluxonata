from django.apps import AppConfig


class RunsConfig(AppConfig):
    name = "runs"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # `vault` validates the settings of a connection but knows nothing of the steps that use it: the SMTP and SSH steps
        # give it their own checks.
        from vault import registry

        from . import smtp_node, ssh_node

        def smtp_check(config: dict) -> None:
            try:
                smtp_node.check_connection_config(config)
            except smtp_node.SmtpStepError as exc:
                raise registry.ConfigRefused(exc.message) from None

        def ssh_check(config: dict) -> None:
            try:
                ssh_node.check_connection_config(config)
            except ssh_node.SshStepError as exc:
                raise registry.ConfigRefused(exc.message) from None

        registry.register_config_check("smtp", smtp_check)
        registry.register_config_check("ssh", ssh_check)
