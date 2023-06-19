class BondiError(Exception):
    """Base exception for all errors of bondi CLI"""


class InternalError(BondiError):
    def __init__(self) -> None:
        super().__init__(
            "Internal error occurred. Possibly, bug in client or API server\n"
            "Please, contact support service to resolve the issue"
        )


class ConfigLoadError(BondiError):
    def __init__(self) -> None:
        super().__init__(
            "Config file is corrupted. Unable to continue\n"
            "Please, run 'bondi config init' to resolve the issue"
        )


class ConfigNotFoundError(BondiError):
    def __init__(self) -> None:
        super().__init__(
            "Unable to find configuration. The first time run?\n"
            "Please, run 'bondi config init' first"
        )


class ConnectionError(BondiError):
    def __init__(self, url: str) -> None:
        super().__init__(
            f"Network failure occurred during request to '{url}'\n"
            "Please, ensure API server is available and try again"
        )


class AuthError(BondiError):
    def __init__(self) -> None:

        commands = "".join(
            [
                "\n - bondi config show",
                "\n - bondi config show --no-hide",
                "\n - bondi config init",
            ]
        )

        super().__init__(
            "Login failure. Ensure, you've entered valid credentials\n"
            f"Commands will help: {commands}"
        )


class APIError(BondiError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")


class BadParameterError(BondiError):
    def __init__(self, ctx, msg: str, *params: str) -> None:
        super().__init__(msg)
        self.params = params
        self.ctx = ctx


class BondiValidationError(BondiError):
    def __init__(self, msg: str, errors: list) -> None:
        super().__init__(msg)
        self.errors = errors


class ClientSideValidationError(BondiValidationError):
    def __init__(self, errors: list) -> None:
        super().__init__("Input data is invalid", errors)


class ServerSideValidationError(BondiValidationError):
    def __init__(self, errors: list) -> None:
        super().__init__("Server rejected client data", errors)
