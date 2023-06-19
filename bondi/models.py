from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from enum import Enum

from pydantic import AnyHttpUrl, BaseModel, Field


class OutputMode(str, Enum):
    human = "human"
    json = "json"


class LoginResult(BaseModel):
    user_id: str
    session_id: str
    is_admin: bool
    user_name: str
    display_name: str


class ICredentialsDisplay(metaclass=ABCMeta):
    @abstractmethod
    def display_dict(self, hide: bool = True):
        pass


class AuthConfig(BaseModel, ICredentialsDisplay):

    url: AnyHttpUrl
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)

    def display_dict(self, hide: bool = True):

        data = self.dict()

        if hide:
            data["username"] = "*" * 16 + data["username"][-4:]
            data["password"] = "*" * 16 + data["password"][-4:]

        return data


class Verbosity(str, Enum):
    none = "none"
    info = "info"
    warning = "warning"
    error = "error"
    debug = "debug"


class RevisionStatus(str, Enum):
    unverified = "Unverified"
    verifying = "Verifying"
    running = "Running"
    stopped = "Stopped"


class RevisionHealth(str, Enum):
    warn = "Warning"
    err = "Error"
    ok = "Ok"


class UpdateResponseModel(BaseModel):
    old: dict
    new: dict


class DeleteActions(str, Enum):
    delete = "Delete"
    restore = "Restore"
    erase = "Erase"


class FuzzingEngine(str, Enum):
    afl = "AFL"
    libfuzzer = "LibFuzzer"


class ImageStatus(str, Enum):
    not_pushed = "NotPushed"
    verifying = "Verifying"
    verify_error = "VerifyError"
    ready = "Ready"


class ImageType(str, Enum):
    custom = "Custom"
    builtin = "Built-in"


class FuzzerLang(str, Enum):
    go = "Go"  # libfuzzer
    cpp = "Cpp"  # afl, libfuzzer
    rust = "Rust"  # libfuzzer
    # java = "Java" # jqf
    python = "Python"  # libfuzzer
    # javascript = "JavaScript" # libfuzzer


class IntegrationStatus(str, Enum):

    in_progress = "InProgress"
    """ Verifying bug tracker connection, credentials, etc... """

    succeeded = "Succeeded"
    """ Integration succeeded. Notification delivery works well """

    failed = "Failed"
    """ Integration failed. Notifications will not be delivered """


class StatisticsGroupBy(str, Enum):
    day = "day"
    week = "week"
    month = "month"


class IntegrationType(str, Enum):
    jira = "Jira"
    # telegram = "Telegram"
    # mail = "Mail"


@dataclass
class AppContext:
    verbosity: Verbosity
    output_mode: OutputMode
    auto_approve: bool
    silent: bool
    prompt: bool
