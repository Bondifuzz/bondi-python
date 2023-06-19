from typing import List, Optional

import typer
from pydantic import BaseModel

from bondi import output, validators
from bondi.callback import DefaultProjectCallback, DefaultUserCallback, StringCallback
from bondi.cli.admin.users import complete_user_name
from bondi.client import AutologinClient
from bondi.constants import C_NO_PARAMS_SET, C_WARN_UNRECOVERABLE
from bondi.defaults import (
    load_default_fuzzer,
    load_default_project,
    load_default_user,
    remove_default_fuzzer,
    save_default_fuzzer,
)
from bondi.errors import InternalError, ServerSideValidationError
from bondi.helper import (
    download_file,
    paginate,
    parse_response,
    parse_response_no_model,
)
from bondi.meta import list_fuzzer_configurations
from bondi.models import (
    AppContext,
    DeleteActions,
    FuzzerLang,
    FuzzingEngine,
    UpdateResponseModel,
)
from bondi.util import is_identifier, make_option, shorten, wrap_autocompletion_errors

from .projects import complete_project_name, get_ids_for_project_url

########################################
# App
########################################

app = typer.Typer(name="fuzzers", help="User's fuzzers")

########################################
# Endpoints
########################################


def url_fuzzers(project_id, user_id: str):
    return f"/api/v1/users/{user_id}/projects/{project_id}/fuzzers"


def url_fuzzer(fuzzer_id: str, project_id, user_id: str):
    return f"{url_fuzzers(project_id, user_id)}/{fuzzer_id}"


def url_fuzzer_corpus(fuzzer_id: str, project_id, user_id: str):
    return f"{url_fuzzer(fuzzer_id, project_id, user_id)}/files/corpus"


########################################
# Models
########################################


class CreateFuzzerResponseModel(BaseModel):
    id: str
    name: str


class GetFuzzerResponseModel(BaseModel):
    id: str
    name: str
    description: str
    engine: FuzzingEngine
    lang: FuzzerLang
    ci_integration: bool

    def display_dict(self):
        data = self.dict()
        data["description"] = shorten(self.description)
        data["engine"] = self.engine.value
        data["lang"] = self.lang.value
        return data


########################################
# Utils
########################################


def get_fuzzer_id(
    fuzzer: str,
    project_id: str,
    user_id: str,
    client: AutologinClient,
):
    if is_identifier(fuzzer):
        return fuzzer

    url = f"{url_fuzzers(project_id, user_id)}/lookup"
    response = client.get(url, params={"name": fuzzer})
    ResponseModel = GetFuzzerResponseModel

    try:
        data: ResponseModel = parse_response(response, ResponseModel)
    except ServerSideValidationError as e:
        raise InternalError() from e

    return data.id


def get_ids_for_fuzzer_url(
    fuzzer: str,
    project: str,
    user: Optional[str],
    client: AutologinClient,
):
    ids = get_ids_for_project_url(project, user, client)
    fuzzer_id = get_fuzzer_id(fuzzer=fuzzer, **ids, client=client)
    return {"fuzzer_id": fuzzer_id, **ids}


def send_get_fuzzer(
    fuzzer: str,
    project: str,
    user: Optional[str],
    client: AutologinClient,
):

    ids = get_ids_for_fuzzer_url(
        user=user,
        fuzzer=fuzzer,
        project=project,
        client=client,
    )

    ResponseModel = GetFuzzerResponseModel
    response = client.get(url_fuzzer(**ids))
    data: ResponseModel = parse_response(response, ResponseModel)

    return data


def send_list_fuzzers(
    project: str,
    user: Optional[str],
    client: AutologinClient,
):
    data = []
    ResponseModel = GetFuzzerResponseModel

    url = url_fuzzers(
        **get_ids_for_project_url(project, user, client),
    )

    fuzzer: ResponseModel
    for fuzzer in paginate(client, url, ResponseModel):
        data.append(fuzzer.display_dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_fuzzer_name(ctx: typer.Context, incomplete: str):

    user = ctx.params.get("user") or load_default_user()
    project = ctx.params.get("project") or load_default_project()

    with AutologinClient() as client:
        fuzzers = send_list_fuzzers(project, user, client)

    fuzzer_names: List[str] = [fuzzer["name"] for fuzzer in fuzzers]
    return list(filter(lambda u: u.startswith(incomplete), fuzzer_names))


########################################
# Show possible configurations
########################################


@app.command(
    name="show-configurations",
    help="Show fuzzing configurations: <Programming language, Fuzzing engine>",
    short_help="Show fuzzing configurations <Lang, Engine>",
)
def show_configurations(ctx: typer.Context):

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode

    with AutologinClient() as client:
        data = list_fuzzer_configurations(client)

    columns = [
        ("cpp", "C++"),
        ("go", "Go"),
        ("rust", "Rust"),
        ("python", "Python"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Create fuzzer
########################################


@app.command(
    name="create",
    help="Create new fuzzer",
)
def create_fuzzer(
    ctx: typer.Context,
    name: str = typer.Option(
        None,
        "-n",
        "--name",
        callback=StringCallback(),
        help="Fuzzer name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        callback=validators.string,
        help="Fuzzer description",
    ),
    engine: FuzzingEngine = typer.Option(
        None,
        "-e",
        "--engine",
        callback=StringCallback(),
        autocompletion=lambda: [e.value for e in FuzzingEngine],
        metavar=f"[{'|'.join([e.value for e in FuzzingEngine])}]",
        help="Engine, for which the fuzzer is designed",
    ),
    lang: FuzzerLang = typer.Option(
        None,
        "-l",
        "--lang",
        callback=StringCallback(),
        autocompletion=lambda: [e.value for e in FuzzerLang],
        metavar=f"[{'|'.join([e.value for e in FuzzerLang])}]",
        help="Programming language, for which the fuzzer is designed",
    ),
    ci_integration: bool = typer.Option(
        False,
        "--ci",
        "--ci-integration",
        help="Whether fuzzer has integration with CI/CD",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer will be created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = CreateFuzzerResponseModel

    json_data = {
        "name": name,
        "description": description or "No description",
        "ci_integration": ci_integration,
        "engine": engine,
        "lang": lang,
    }

    with AutologinClient() as client:
        ids = get_ids_for_project_url(project, user, client)
        response = client.post(url_fuzzers(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Fuzzer name"),
    ]

    output.dict_data(data.dict(), columns, output_mode)


########################################
# Get fuzzer
########################################


@app.command(
    name="get",
    help="Get fuzzer information by name or id",
)
def get_fuzzer(
    ctx: typer.Context,
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode

    columns = [
        ("id", "ID"),
        ("name", "Fuzzer name"),
        ("description", "Description"),
        ("engine", "Fuzzing engine"),
        ("lang", "Lang"),
        ("ci_integration", "CI/CD"),
    ]

    with AutologinClient() as client:
        data = send_get_fuzzer(fuzzer, project, user, client)

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List fuzzers
########################################


@app.command(
    name="list",
    help="List fuzzers in project",
)
def list_fuzzers(
    ctx: typer.Context,
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):

    columns = [
        ("id", "ID", 0.1),
        ("name", "Fuzzer name", 0.2),
        ("description", "Description", 0.35),
        ("engine", "Fuzzing engine", 0.15),
        ("lang", "Lang", 0.1),
        ("ci_integration", "CI/CD", 0.1),
    ]

    with AutologinClient() as client:
        data = send_list_fuzzers(project, user, client)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)


########################################
# Update fuzzer
########################################


@app.command(
    name="update",
    help="Update fuzzer",
)
def update_fuzzer(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        prompt_required=False,
        callback=validators.string,
        help="New fuzzer name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        prompt_required=False,
        callback=validators.string,
        help="New fuzzer description",
    ),
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = UpdateResponseModel

    json_data = {
        "name": name,
        "description": description,
    }

    if all(v is None for v in json_data.values()):
        param_names = "|".join(map(make_option, json_data.keys()))
        output.error(f"{C_NO_PARAMS_SET}: [{param_names}]")
        raise typer.Exit(code=1)

    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            fuzzer=fuzzer,
            project=project,
            client=client,
        )

        response = client.patch(url_fuzzer(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "name": "Project name",
        "description": "Description",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Download corpus
########################################


@app.command(
    name="download-corpus",
    help="Download fuzzer corpus",
)
def download_fuzzer_corpus(
    ctx: typer.Context,
    output_file: Optional[str] = typer.Option(
        None,
        "-o",
        "--output-file",
        callback=validators.string,
        help="Where to save fuzzer corpus",
    ),
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode

    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(fuzzer, project, user, client)
        filepath = output_file or f"{ids['fuzzer_id']}.corpus.tar.gz"
        url = url_fuzzer_corpus(**ids)

        download_file("corpus", url, filepath, client, output_mode)
        output.success(f"Saved to {filepath}")


########################################
# Delete/restore/erase fuzzer
########################################


@app.command(
    name="delete",
    help="Delete fuzzer (move to trash bin)",
)
def delete_fuzzer(
    ctx: typer.Context,
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to delete this fuzzer?"
        typer.confirm(msg, abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            client=client,
        )

        url = url_fuzzer(**ids)
        query = {"action": DeleteActions.delete.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Fuzzer deleted successfully")


@app.command(
    name="restore",
    help="Restore deleted fuzzer (move out of trash bin)",
)
def restore_fuzzer(
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            client=client,
        )

        url = url_fuzzer(**ids)
        query = {"action": DeleteActions.restore.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Fuzzer restored successfully")


@app.command(
    name="erase",
    help="Delete fuzzer without recovery possibility",
)
def erase_fuzzer(
    ctx: typer.Context,
    backup: bool = typer.Option(
        True,
        help="Whether to make a backup of erased fuzzer or not",
    ),
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to erase this fuzzer?"
        typer.confirm(f"{C_WARN_UNRECOVERABLE}\n{msg}", abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            client=client,
        )

        query = {
            "action": DeleteActions.erase.value,
            "no_backup": not backup,
        }

        url = url_fuzzer(**ids)
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Fuzzer erased successfully")


########################################
# Set/unset/get default fuzzer
########################################


@app.command(
    name="set-default",
    help="Enable auto substitution of '--fuzzer' option with selected one",
    short_help="Enable auto substitution of '--fuzzer' option",
)
def set_default_fuzzer(
    fuzzer: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name, in which the fuzzer was created",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            client=client,
        )

        if is_identifier(fuzzer):
            response = client.get(url_fuzzer(**ids))
            parse_response(response, GetFuzzerResponseModel)

    save_default_fuzzer(ids["fuzzer_id"])
    output.success("Default fuzzer set successfully")


@app.command(
    name="unset-default",
    help="Disable auto substitution of '--fuzzer' option",
)
def unset_default_fuzzer():

    if load_default_fuzzer():
        remove_default_fuzzer()
        output.success("Default fuzzer unset successfully")
    else:
        output.error("Default fuzzer not set")


@app.command(
    name="get-default",
    help="Get id of fuzzer selected for auto substitution",
)
def get_default_fuzzer():

    fuzzer = load_default_fuzzer()

    if fuzzer is not None:
        output.result(fuzzer)
    else:
        output.error("Default fuzzer not set")
