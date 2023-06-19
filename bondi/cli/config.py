import typer
from pydantic import ValidationError

from bondi import output, validators
from bondi.defaults import remove_default_user
from bondi.errors import ClientSideValidationError
from bondi.models import AppContext, AuthConfig
from bondi.util import load_auth_config, remove_login_result, save_auth_config

########################################
# App
########################################

app = typer.Typer(name="config", help="Manage configuration")


########################################
# Autocompletion
########################################


def config_keys():
    return ["url", "username", "password"]


########################################
# Commands
########################################


@app.command(
    name="init",
    help="Do initialization to work with Bondifuzz",
)
def init_config(
    server_url: str = typer.Option(
        ...,
        prompt=True,
        callback=validators.url,
        help="Bondifuzz API server URL",
    ),
    username: str = typer.Option(
        ...,
        prompt=True,
        callback=validators.string,
        help="Username of Bondifuzz account",
    ),
    password: str = typer.Option(
        ...,
        prompt=True,
        hide_input=True,
        callback=validators.string,
        help="Password of Bondifuzz account",
    ),
):
    data = {
        "url": server_url,
        "username": username,
        "password": password,
    }

    save_auth_config(AuthConfig(**data))
    remove_login_result()  # Remove cookies
    remove_default_user()  # Remove defaults

    output.success("Initialization successful")


@app.command(
    name="get",
    help="Get config field value by name",
)
def get_config_field(
    field_name: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=config_keys,
        help="Name of field to show: url, username, password",
    ),
):
    config = load_auth_config().dict()

    if field_name not in config:
        output.error(f"Unknown field name '{field_name}'")
        raise typer.Exit(code=1)

    output.result(config[field_name])


@app.command(
    name="set",
    help="Set config field value by name",
)
def set_config_field(
    field_name: str = typer.Argument(
        ...,
        autocompletion=config_keys,
        callback=validators.string,
        help="Name of field to change: url, username, password",
    ),
    field_value: str = typer.Argument(
        ...,
        callback=validators.string,
        help="Value will be set to that field",
    ),
):

    config = load_auth_config().dict()

    if field_name not in config:
        output.error(f"Unknown field name '{field_name}'")
        raise typer.Exit(code=1)

    try:
        config.update({field_name: field_value})
        save_auth_config(AuthConfig(**config))

    except ValidationError as e:
        raise ClientSideValidationError(e.errors())

    remove_login_result()  # Remove cookies
    remove_default_user()  # Remove defaults
    output.success("Config updated successfully")


@app.command(
    name="show",
    help="Show all config field names and values",
)
def show_config(
    ctx: typer.Context,
    hide: bool = typer.Option(
        True, help="If set, show all config values without hiding"
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    config = load_auth_config()

    columns = [
        ("url", "URL"),
        ("username", "Username"),
        ("password", "Password"),
    ]

    output.dict_data(config.display_dict(hide), columns, output_mode)
