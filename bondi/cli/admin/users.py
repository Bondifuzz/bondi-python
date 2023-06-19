from datetime import datetime
from typing import List, Optional

import typer
from pydantic import BaseModel, validator

from bondi import output, validators
from bondi.client import AutologinClient
from bondi.constants import C_NO_PARAMS_SET, C_WARN_UNRECOVERABLE
from bondi.defaults import load_default_user, remove_default_user, save_default_user
from bondi.errors import InternalError, ServerSideValidationError
from bondi.helper import paginate, parse_response, parse_response_no_model
from bondi.models import AppContext, DeleteActions, UpdateResponseModel
from bondi.util import (
    is_identifier,
    make_option,
    utc_to_local,
    wrap_autocompletion_errors,
)

########################################
# App
########################################

app = typer.Typer(name="users", help="Manage users")

########################################
# Endpoints
########################################

URL_USERS = "/api/v1/admin/users"

########################################
# Models
########################################


class CreateUserResponseModel(BaseModel):
    id: str
    name: str
    is_admin: bool
    is_confirmed: bool
    is_disabled: bool


class GetUserResponseModel(BaseModel):
    id: str
    name: str
    display_name: str
    email: str
    is_confirmed: bool
    is_disabled: bool
    is_admin: bool
    is_system: bool
    erasure_date: Optional[datetime]

    @validator("erasure_date")
    def utc_to_local(date: datetime):
        return utc_to_local(date)

    def display_dict(self):
        data = self.dict(exclude={"erasure_date"})
        data["deleted"] = self.erasure_date is not None
        return data


########################################
# Utils
########################################


def get_user_id(
    user: str,
    client: AutologinClient,
):
    if is_identifier(user):
        return user

    url = f"{URL_USERS}/lookup"
    ResponseModel = GetUserResponseModel
    response = client.get(url, params={"name": user})

    try:
        data: ResponseModel = parse_response(response, ResponseModel)
    except ServerSideValidationError as e:
        raise InternalError() from e

    return data.id


def send_list_users(client: AutologinClient):

    data = []
    ResponseModel = GetUserResponseModel

    user: ResponseModel
    for user in paginate(client, URL_USERS, ResponseModel):
        data.append(user.dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_user_name(username: str):

    with AutologinClient() as client:
        users = send_list_users(client)

    user_names: List[str] = [user["name"] for user in users]
    return list(filter(lambda u: u.startswith(username), user_names))


########################################
# Create user
########################################


@app.command(
    name="create",
    help="Create new user account",
)
def create_user(
    ctx: typer.Context,
    username: str = typer.Option(
        ...,
        "-n",
        "--name",
        "--username",
        prompt=True,
        prompt_required=False,
        callback=validators.string,
        help="Account username",
    ),
    password: str = typer.Option(
        ...,
        "-p",
        "--passwd",
        "--password",
        prompt=True,
        confirmation_prompt=True,
        callback=validators.string,
        help="Account password",
        hide_input=True,
    ),
    is_admin: bool = typer.Option(
        False,
        "--admin",
        is_flag=True,
        prompt=True,
        prompt_required=False,
        help="User will have admin rights",
    ),
    display_name: str = typer.Option(
        ...,
        "-d",
        "--display-name",
        prompt=True,
        prompt_required=False,
        callback=validators.string,
        help="User's display name",
    ),
    email: str = typer.Option(
        ...,
        "-m",
        "--mail",
        "--email",
        prompt=True,
        prompt_required=False,
        callback=validators.email,
        help="User's email",
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = CreateUserResponseModel

    json_data = {
        "name": username,
        "password": password,
        "is_admin": is_admin,
        "display_name": display_name,
        "email": email,
    }

    with AutologinClient() as client:
        response = client.post(URL_USERS, json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Username"),
    ]

    output.dict_data(data.dict(), columns, output_mode)


########################################
# Get user
########################################


@app.command(
    name="get",
    help="Get user account information by name or id",
    short_help="Get user by name or id",
)
def get_user(
    ctx: typer.Context,
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="User's name or id",
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = GetUserResponseModel

    with AutologinClient() as client:
        user_id = get_user_id(user, client)
        response = client.get(f"{URL_USERS}/{user_id}")
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Username"),
        ("display_name", "Display name"),
        ("email", "Email"),
        ("is_confirmed", "Confirmed"),
        ("is_disabled", "Disabled"),
        ("is_admin", "Admin"),
        ("is_system", "System"),
        ("deleted", "Deleted"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List users
########################################


@app.command(
    name="list",
    help="List user accounts",
)
def list_users(ctx: typer.Context):

    columns = [
        ("id", "ID", 0.1),
        ("name", "Username", 0.2),
        ("display_name", "Display name", 0.3),
        ("email", "Email", 0.3),
        ("is_admin", "Admin", 0.1),
    ]

    with AutologinClient() as client:
        data = send_list_users(client)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)


########################################
# Update user
########################################


@app.command(
    name="update",
    help="Update user account information",
)
def update_user(
    ctx: typer.Context,
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to update",
    ),
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        "--username",
        prompt_required=False,
        callback=validators.string,
        help="New username",
    ),
    display_name: Optional[str] = typer.Option(
        None,
        "-d",
        "--display-name",
        prompt_required=False,
        callback=validators.string,
        help="New display name",
    ),
    email: Optional[str] = typer.Option(
        None,
        "-m",
        "--mail",
        "--email",
        prompt_required=False,
        callback=validators.email,
        help="New email",
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = UpdateResponseModel

    json_data = {
        "name": name,
        "display_name": display_name,
        "email": email,
    }

    if all(v is None for v in json_data.values()):
        param_names = "|".join(map(make_option, json_data.keys()))
        output.error(f"{C_NO_PARAMS_SET}: [{param_names}]")
        raise typer.Exit(code=1)

    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.patch(url, json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "name": "Username",
        "display_name": "Display name",
        "email": "Email",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Enable/disable/confirm/discard user
########################################


@app.command(
    name="enable",
    help="Make user account enabled (remove from ban list)",
)
def enable_user(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to enable",
    ),
):
    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.patch(url, json={"is_disabled": False})
        parse_response(response, UpdateResponseModel)

    output.success(f"User enabled")


@app.command(
    name="disable",
    help="Make user account disabled (add to ban list)",
)
def disable_user(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to disable",
    ),
):
    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.patch(url, json={"is_disabled": True})
        parse_response(response, UpdateResponseModel)

    output.success(f"User disabled")


@app.command(
    name="confirm",
    help="Activate account without confirmation by email, phone, etc",
    short_help="Activate user account manually",
)
def confirm_user(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to confirm",
    ),
):
    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.patch(url, json={"is_confirmed": True})
        parse_response(response, UpdateResponseModel)

    output.success("Account confirmed")


@app.command(
    name="discard",
    help="Deactivate account and require confirmation by email, phone, etc",
    short_help="Deactivate user account manually",
)
def discard_user(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to discard",
    ),
):
    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.patch(url, json={"is_confirmed": False})
        parse_response(response, UpdateResponseModel)

    output.success("Account confirmation discarded")


########################################
# Change user's password
########################################


@app.command(
    name="passwd",
    help="Change account password",
)
def change_user_password(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name of id of user to update",
    ),
    password: str = typer.Option(
        ...,
        prompt=True,
        confirmation_prompt=True,
        callback=validators.string,
        help="New password",
        hide_input=True,
    ),
):
    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.patch(url, json={"password": password})
        parse_response(response, UpdateResponseModel)

    output.success("Account password is changed")


########################################
# Delete/restore/erase user
########################################


@app.command(
    name="delete",
    help="Delete user (move to trash bin)",
)
def delete_user(
    ctx: typer.Context,
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to delete",
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to delete this user?"
        typer.confirm(msg, abort=True)

    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        query = {"action": DeleteActions.delete.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("User deleted successfully")


@app.command(
    name="restore",
    help="Restore user (move out of trash bin)",
)
def restore_user(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to restore",
    ),
):
    with AutologinClient() as client:
        url = f"{URL_USERS}/{get_user_id(user, client)}"
        query = {"action": DeleteActions.restore.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("User restored successfully")


@app.command(
    name="erase",
    help="Delete user without recovery possibility",
)
def erase_user(
    ctx: typer.Context,
    backup: bool = typer.Option(
        True,
        help="Whether to create a backup of erased user data",
    ),
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to erase",
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to erase this user?"
        typer.confirm(f"{C_WARN_UNRECOVERABLE}\n{msg}", abort=True)

    with AutologinClient() as client:

        query = {
            "action": DeleteActions.erase.value,
            "no_backup": not backup,
        }

        url = f"{URL_USERS}/{get_user_id(user, client)}"
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("User erased successfully")


########################################
# Set/unset/get default user
########################################


@app.command(
    name="set-default",
    help="Enable auto substitution of '--user' option with selected one",
    short_help="Enable auto substitution of '--user' option",
)
def set_default_user(
    user: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_user_name,
        help="Name or id of user to set default",
    ),
):
    with AutologinClient() as client:

        if not is_identifier(user):
            user_id = get_user_id(user, client)
        else:
            response = client.get(f"{URL_USERS}/{user}")
            parse_response(response, GetUserResponseModel)
            user_id = user

    save_default_user(user_id)
    output.success("Default user set successfully")


@app.command(
    name="unset-default",
    help="Disable auto substitution of '--user' option",
)
def unset_default_user():

    if load_default_user():
        remove_default_user()
        output.success("Default user unset successfully")
    else:
        output.error("Default user not set")


@app.command(
    name="get-default",
    help="Get id of user selected for auto substitution",
)
def get_default_user():

    user = load_default_user()

    if user is not None:
        output.result(user)
    else:
        output.error("Default user not set")
