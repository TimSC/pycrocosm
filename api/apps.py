from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        # Registers pycrocosm's own system checks
        import pycrocosm.checks  # noqa: F401
