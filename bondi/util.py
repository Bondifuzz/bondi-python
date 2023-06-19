import functools
import json
import os
import re
import textwrap
import traceback
from contextlib import suppress
from datetime import datetime
from typing import Optional

import typer

from bondi.constants import C_SHORTEN_DESC
from bondi.models import AuthConfig, LoginResult

from .errors import BondiError, ConfigLoadError, ConfigNotFoundError

APP_NAME = "bondi"
APP_DIR = typer.get_app_dir(APP_NAME)
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo


def load_json(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(filepath: str, data: object):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f)


def get_auth_config_path():
    return os.path.join(APP_DIR, "auth.json")


def get_login_result_path():
    return os.path.join(APP_DIR, "session.json")


def load_auth_config():

    try:
        json_data = load_json(get_auth_config_path())
        res = AuthConfig.parse_obj(json_data)

    except FileNotFoundError as e:
        raise ConfigNotFoundError() from e

    except ValueError as e:
        raise ConfigLoadError() from e

    return res


def load_login_result():

    try:
        json_data = load_json(get_login_result_path())
        res = LoginResult.parse_obj(json_data)

    except FileNotFoundError as e:
        raise ConfigNotFoundError() from e

    except ValueError as e:
        raise ConfigLoadError() from e

    return res


def save_auth_config(config: AuthConfig):
    save_json(get_auth_config_path(), config.dict())


def save_login_result(result: LoginResult):
    save_json(get_login_result_path(), result.dict())


def remove_login_result():
    with suppress(FileNotFoundError):
        os.remove(get_login_result_path())


def is_identifier(s: str):
    return re.match(r"^\d+$", s) is not None


def utc_to_local(val: Optional[datetime]):
    return val.astimezone(LOCAL_TIMEZONE) if val else val


def beautify(s: str):
    return s.capitalize().replace("_", " ")


def make_option(s: str):
    return "--" + s.replace("_", "-")


def shorten(s: str, n: int = C_SHORTEN_DESC):
    return textwrap.shorten(s, n)


def wrap_autocompletion_errors(func):
    @functools.wraps(func)
    def catch_exceptions(*args, **kwargs):

        res = []

        try:
            with suppress(BondiError):
                res = func(*args, **kwargs)
        except:
            with open("bondi.error.log", "w") as f:
                f.write(traceback.format_exc())

        return res

    return catch_exceptions


from click import Parameter


def get_cli_opts(ctx: typer.Context, param_name: str) -> str:

    # Find parameter by name
    def find(param: Parameter):
        return param.name == param_name

    try:
        param = next(filter(find, ctx.command.params))
    except StopIteration:
        msg = f"Failed to find parameter with name '{param_name}'"
        raise RuntimeError(msg)

    return " / ".join(map(lambda x: f"'{x}'", param.opts))
