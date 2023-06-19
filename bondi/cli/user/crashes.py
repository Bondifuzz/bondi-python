from datetime import datetime
from typing import Optional

import typer
from pydantic import BaseModel, validator

from bondi import output, validators
from bondi.callback import (
    DefaultFuzzerCallback,
    DefaultProjectCallback,
    DefaultUserCallback,
)
from bondi.cli.admin.users import complete_user_name
from bondi.cli.user.fuzzers import (
    complete_fuzzer_name,
    get_ids_for_fuzzer_url,
    url_fuzzer,
)
from bondi.cli.user.projects import complete_project_name
from bondi.cli.user.revisions import (
    complete_revision_name,
    download_file,
    get_ids_for_revision_url,
    url_revision,
)
from bondi.client import AutologinClient
from bondi.defaults import (
    load_default_fuzzer,
    load_default_project,
    load_default_revision,
    load_default_user,
)
from bondi.errors import BondiError
from bondi.helper import paginate, parse_response
from bondi.models import AppContext
from bondi.util import utc_to_local, wrap_autocompletion_errors

app = typer.Typer(name="crashes", help="Found crashes")

########################################
# Endpoints
########################################


def url_crashes_fuzz(fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_fuzzer(fuzzer_id, project_id, user_id)}/crashes"


def url_crashes_rev(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revision(revision_id, fuzzer_id, project_id, user_id)}/crashes"


def url_crashes(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revision(revision_id, fuzzer_id, project_id, user_id)}/crashes"


def url_crash(crash_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_crashes_fuzz(fuzzer_id, project_id, user_id)}/{crash_id}"


def url_crash_raw(crash_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_crash(crash_id, fuzzer_id, project_id, user_id)}/raw"


def url_crashes(
    client: AutologinClient,
    revision: Optional[str],
    fuzzer: str,
    project: str,
    user: str,
):
    if revision:
        return url_crashes_rev(
            **get_ids_for_revision_url(
                revision,
                fuzzer,
                project,
                user,
                client,
            ),
        )
    else:
        return url_crashes_fuzz(
            **get_ids_for_fuzzer_url(
                fuzzer,
                project,
                user,
                client,
            ),
        )


########################################
# Models
########################################


class GetCrashResponseModel(BaseModel):

    id: str
    """ Unique record id """

    created: datetime
    """ Date when crash was retrived """

    preview: str
    """ Chunk of crash input to preview (base64-encoded) """

    type: str
    """ Type of crash: crash, oom, timeout, leak, etc.. """

    brief: str
    """ Short description for crash """

    details: Optional[str]
    """ Crash details (long multiline text) """

    reproduced: bool
    """ True if crash was reproduced, else otherwise """

    duplicate_count: int
    """ Count of similar crashes found """

    @validator("created")
    def utc_to_local(date: datetime):
        return utc_to_local(date)

    def display_dict(self):
        data = self.dict()
        data["created"] = self.created.strftime("%c")
        return data


########################################
# Utils
########################################


def get_ids_for_crash_url(
    crash_id: str,
    revision: str,
    fuzzer: str,
    project: str,
    user: str,
    client: AutologinClient,
):
    ids = get_ids_for_revision_url(revision, fuzzer, project, user, client)
    return {"crash_id": crash_id, **ids}


def send_list_crashes(
    client: AutologinClient,
    revision: Optional[str],
    fuzzer: str,
    project: str,
    user: str,
):
    data = []
    ResponseModel = GetCrashResponseModel

    url = url_crashes(
        client,
        revision,
        fuzzer,
        project,
        user,
    )

    crash: ResponseModel
    for crash in paginate(client, url, ResponseModel):
        data.append(crash.display_dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_crash_id(ctx: typer.Context, incomplete: str):

    revision = ctx.params.get("revision") or load_default_revision()
    fuzzer = ctx.params.get("fuzzer") or load_default_fuzzer()
    project = ctx.params.get("project") or load_default_project()
    user = ctx.params.get("user") or load_default_user()

    if not (fuzzer and project):
        raise BondiError(f"Required parameters not set. Unable to continue")

    def list_crashes_wrapped():
        return send_list_crashes(
            client,
            revision,
            fuzzer,
            project,
            user,
        )

    with AutologinClient() as client:
        for crash in list_crashes_wrapped():
            crash_id: str = crash["id"]
            if crash_id.startswith(incomplete):
                crash_details = f"{crash['brief']} [at {crash['created']}]"
                yield crash_id, crash_details


########################################
# Get crash
########################################


@app.command(
    name="get",
    help="Get crash by id",
)
def get_crash(
    ctx: typer.Context,
    crash_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_crash_id,
        help="Crash id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name",
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
    ResponseModel = GetCrashResponseModel

    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            client=client,
        )

        response = client.get(url_crash(**ids, crash_id=crash_id))
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("brief", "Brief"),
        ("type", "Type"),
        ("reproduced", "Reproduced"),
        ("duplicate_count", "Duplicates"),
        ("created", "Created"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Get details
########################################


@app.command(
    name="get-details",
    help="Get crash details (stacktrace)",
)
def get_details(
    crash_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_crash_id,
        help="Crash id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name",
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

        ResponseModel = GetCrashResponseModel
        response = client.get(url_crash(**ids, crash_id=crash_id))
        data: ResponseModel = parse_response(response, ResponseModel)

    output.result("\n" + data.details)


########################################
# List crashes
########################################


@app.command(
    name="list",
    help="List crashes found by fuzzer",
)
def list_fuzzer_crashes(
    ctx: typer.Context,
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    revision: Optional[str] = typer.Option(
        None,
        "-r",
        "--revision",
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision id or name (in case if revision crashes are needed)",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name",
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
        ("brief", "Brief", 0.4),
        ("type", "Type", 0.1),
        ("reproduced", "Reproduced", 0.1),
        ("duplicate_count", "Duplicates", 0.1),
        ("created", "Created", 0.2),
    ]

    with AutologinClient() as client:
        data = send_list_crashes(
            client,
            revision,
            fuzzer,
            project,
            user,
        )

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode

    if revision:
        msg = f"Crashes for <fuzzer={fuzzer}, revision={revision}>"
    else:
        msg = f"Crashes for <fuzzer={fuzzer}>"

    output.message(msg, output_mode)
    output.list_data(data, columns, output_mode)


########################################
# Download crash
########################################


@app.command(
    name="download",
    help="Download crash sample (input bytes)",
)
def download_crash_sample(
    ctx: typer.Context,
    output_file: Optional[str] = typer.Option(
        None,
        "-o",
        "--output-file",
        callback=validators.string,
        help="Where to save crash sample",
    ),
    crash_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_crash_id,
        help="Crash id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name",
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

        url = url_crash_raw(
            **get_ids_for_fuzzer_url(fuzzer, project, user, client),
            crash_id=crash_id,
        )

        filepath = output_file or f"{crash_id}.crash"
        download_file("crash", url, filepath, client, output_mode)
        output.success(f"Saved to {filepath}")
