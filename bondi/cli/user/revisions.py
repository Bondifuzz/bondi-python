from datetime import datetime
from textwrap import wrap
from typing import List, Optional

import typer
from pydantic import BaseModel, validator

from bondi import output, validators
from bondi.callback import (
    DefaultFuzzerCallback,
    DefaultProjectCallback,
    DefaultUserCallback,
    PositiveIntCallback,
)
from bondi.cli.admin.users import complete_user_name, get_user_id
from bondi.cli.user.fuzzers import (
    complete_fuzzer_name,
    get_fuzzer_id,
    get_ids_for_fuzzer_url,
)
from bondi.client import AutologinClient
from bondi.constants import *
from bondi.defaults import (
    load_default_fuzzer,
    load_default_project,
    load_default_revision,
    load_default_user,
    remove_default_revision,
    save_default_revision,
)
from bondi.errors import (
    BadParameterError,
    BondiError,
    InternalError,
    ServerSideValidationError,
)
from bondi.helper import (
    download_file,
    paginate,
    parse_response,
    parse_response_no_model,
    upload_file,
)
from bondi.models import (
    AppContext,
    DeleteActions,
    RevisionHealth,
    RevisionStatus,
    UpdateResponseModel,
)
from bondi.util import (
    is_identifier,
    make_option,
    utc_to_local,
    wrap_autocompletion_errors,
)

from .projects import complete_project_name, get_project_id, send_get_project

########################################
# App
########################################

app = typer.Typer(name="revisions", help="Fuzzer revisions (versions)")

########################################
# Endpoints
########################################


def url_revisions(fuzzer_id: str, project_id: str, user_id: str):
    return (
        f"/api/v1/users/{user_id}"
        f"/projects/{project_id}"
        f"/fuzzers/{fuzzer_id}"
        f"/revisions"
    )


def url_last_revision(fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revisions(fuzzer_id, project_id, user_id)}/last"


def url_revision(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revisions(fuzzer_id, project_id, user_id)}/{revision_id}"


def url_binaries(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    url = url_revision(revision_id, fuzzer_id, project_id, user_id)
    return f"{url}/files/binaries"


def url_seeds(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    url = url_revision(revision_id, fuzzer_id, project_id, user_id)
    return f"{url}/files/seeds"


def url_config(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    url = url_revision(revision_id, fuzzer_id, project_id, user_id)
    return f"{url}/files/config"


def url_corpus(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    url = url_revision(revision_id, fuzzer_id, project_id, user_id)
    return f"{url}/files/corpus"


def url_actions(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revision(revision_id, fuzzer_id, project_id, user_id)}/actions"


def url_start(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_actions(revision_id, fuzzer_id, project_id, user_id)}/start"


def url_restart(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_actions(revision_id, fuzzer_id, project_id, user_id)}/restart"


def url_stop(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_actions(revision_id, fuzzer_id, project_id, user_id)}/stop"


def url_resources(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revision(revision_id, fuzzer_id, project_id, user_id)}/resources"


########################################
# Models
########################################


class CreateRevisionResponseModel(BaseModel):
    id: str
    name: str


class ErrorModel(BaseModel):
    code: int
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class RunEvent(ErrorModel):
    pass


class RunFeedback(BaseModel):
    scheduler: RunEvent
    agent: Optional[RunEvent] = None

    def __str__(self) -> str:

        msg = f"Scheduler: {self.scheduler}"

        if self.agent:
            msg += f"\nAgent: {self.agent}"

        return msg


class UploadStatus(BaseModel):
    uploaded: bool
    last_error: Optional[ErrorModel] = None

    def __str__(self) -> str:

        if self.last_error:
            return f"Upload failure - {self.last_error}"

        return "Uploaded" if self.uploaded else "Not uploaded"


class GetRevisionResponseModel(BaseModel):
    id: str
    name: str
    description: str
    status: RevisionStatus
    health: RevisionHealth
    cpu_usage: int
    ram_usage: int
    tmpfs_size: int
    binaries: UploadStatus
    seeds: UploadStatus
    config: UploadStatus
    image_id: str
    feedback: Optional[RunFeedback]
    created: datetime
    last_start_date: Optional[datetime]
    last_stop_date: Optional[datetime]
    erasure_date: Optional[datetime]

    @validator(
        "erasure_date",
        "last_start_date",
        "last_stop_date",
        "created",
    )
    def utc_to_local(date: datetime):
        return utc_to_local(date)

    def display_dict(self):

        data = self.dict(exclude={"erasure_date"})
        data["binaries"] = str(self.binaries)
        data["seeds"] = str(self.binaries)
        data["config"] = str(self.binaries)
        data["description"] = "\n".join(wrap(self.description))
        data["created"] = self.created.strftime("%c")
        data["cpu_usage"] = f"{self.cpu_usage}m"
        data["ram_usage"] = f"{self.ram_usage}M"
        data["tmpfs_size"] = f"{self.tmpfs_size}M"
        data["deleted"] = self.erasure_date is not None

        data["status"] = self.status.value
        data["health"] = self.health.value

        if data["feedback"] is not None:
            data["feedback"] = str(self.feedback)
        else:
            data["feedback"] = C_NOT_YET

        if data["last_start_date"] is not None:
            data["last_start_date"] = self.last_start_date.strftime("%c")
        else:
            data["last_start_date"] = C_NOT_YET

        if data["last_stop_date"] is not None:
            data["last_stop_date"] = self.last_stop_date.strftime("%c")
        else:
            data["last_stop_date"] = C_NOT_YET

        return data


########################################
# Utils
########################################


def get_revision_id(
    revision: str,
    fuzzer_id: str,
    project_id: str,
    user_id: str,
    client: AutologinClient,
):
    if is_identifier(revision):
        return revision

    url = f"{url_revisions(fuzzer_id, project_id, user_id)}/lookup"
    response = client.get(url, params={"name": revision})
    ResponseModel = GetRevisionResponseModel

    try:
        data: ResponseModel = parse_response(response, ResponseModel)
    except ServerSideValidationError as e:
        raise InternalError() from e

    return data.id


def get_ids_for_revision_url(
    revision: str,
    fuzzer: str,
    project: str,
    user: str,
    client: AutologinClient,
):
    ids = get_ids_for_fuzzer_url(fuzzer, project, user, client)
    revision_id = get_revision_id(revision=revision, **ids, client=client)
    return {"revision_id": revision_id, **ids}


def send_list_revisions(
    fuzzer: str,
    project: str,
    user: Optional[str],
    client: AutologinClient,
):
    data = []
    ResponseModel = GetRevisionResponseModel

    url = url_revisions(
        **get_ids_for_fuzzer_url(fuzzer, project, user, client),
    )

    revision: ResponseModel
    for revision in paginate(client, url, ResponseModel):
        data.append(revision.display_dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_revision_name(ctx: typer.Context, incomplete: str):

    user = ctx.params.get("user") or load_default_user()
    project = ctx.params.get("project") or load_default_project()
    fuzzer = ctx.params.get("fuzzer") or load_default_fuzzer()

    with AutologinClient() as client:
        revs = send_list_revisions(fuzzer, project, user, client)

    rev_names: List[str] = [rev["name"] for rev in revs]
    return list(filter(lambda u: u.startswith(incomplete), rev_names))


########################################
# Create revision
########################################


@app.command(
    name="create",
    help="Create new fuzzer revision to run",
)
def create_revision(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        help="Revision name (auto generated if omitted)",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        prompt=True,
        prompt_required=False,
        help="Revision description",
    ),
    image_id: str = typer.Option(
        ...,
        "-i",
        "--image-id",
        prompt=True,
        prompt_required=False,
        help="Agent image id. Agent is responsible for running fuzzer in docker container",
    ),
    cpu_usage: int = typer.Option(
        None,
        "--cpu",
        "--cpu-usage",
        callback=PositiveIntCallback(),
        help="Max amount of CPU to allocate for fuzzer",
    ),
    ram_usage: int = typer.Option(
        None,
        "--ram",
        "--ram-usage",
        callback=PositiveIntCallback(),
        help="Max amount of RAM to allocate for fuzzer",
    ),
    tmpfs_size: int = typer.Option(
        None,
        "--tmpfs",
        "--tmpfs-size",
        callback=PositiveIntCallback(),
        help="Tempfs size to allocate for fuzzer",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision will be created",
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
    ResponseModel = CreateRevisionResponseModel

    json_data = {
        "name": name,
        "description": description or "No description",  # TODO: fix api server
        "image_id": image_id,
        "cpu_usage": cpu_usage,
        "ram_usage": ram_usage,
        "tmpfs_size": tmpfs_size,
    }

    with AutologinClient() as client:

        cur_project = send_get_project(project, user, client)
        total_ram_usage = ram_usage + tmpfs_size

        fuzzer_limits = cur_project.pool.fuzzer_limits
        cpu_limits, ram_limits = fuzzer_limits.cpu, fuzzer_limits.ram
        tmpfs_limits, total_limits = fuzzer_limits.tmpfs, fuzzer_limits.ram_total

        if cpu_usage < cpu_limits.min_value or cpu_usage > cpu_limits.max_value:
            msg = "CPU usage must be in range: [%d, %d] (mcpu)"
            args = cpu_limits.min_value, cpu_limits.max_value
            raise BadParameterError(ctx, msg % args, "cpu_usage")

        if ram_usage < ram_limits.min_value or ram_usage > ram_limits.max_value:
            msg = "RAM usage must be in range: [%d, %d] (MB)"
            args = ram_limits.min_value, ram_limits.max_value
            raise BadParameterError(ctx, msg % args, "ram_usage")

        if tmpfs_size < tmpfs_limits.min_value or tmpfs_size > tmpfs_limits.max_value:
            msg = "TmpFS size must be in range: [%d, %d] (MB)"
            args = tmpfs_limits.min_value, tmpfs_limits.max_value
            raise BadParameterError(ctx, msg % args, "tmpfs_size")

        if (
            total_ram_usage < total_limits.min_value
            or total_ram_usage > total_limits.max_value
        ):
            args = total_limits.min_value, total_limits.max_value
            msg = "Sum of TmpFS size and RAM usage must be in range: [%d, %d] (MB)"
            raise BadParameterError(ctx, msg % args, "ram_usage", "tmpfs_size")

        ids = get_ids_for_fuzzer_url(fuzzer, project, user, client)
        response = client.post(url_revisions(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Revision name"),
    ]

    output.dict_data(data.dict(), columns, output_mode)


########################################
# Upload revision files
########################################


@app.command(
    name="upload-files",
    help="Upload necessary files to run revision",
)
def upload_revision_files(
    ctx: typer.Context,
    binaries: str = typer.Option(
        ...,
        "-b",
        "--binaries",
        "--binaries-path",
        prompt_required=False,  # ?
        prompt=f"Binaries archive [.tar.gz]",
        callback=validators.file_read_required,
        help="Fuzzer binaries and other files required for run",
    ),
    seeds: str = typer.Option(
        ...,
        "-s",
        "--seeds",
        "--seeds-path",
        prompt_required=False,  # ?
        prompt=f"Seeds archive ({C_SKIP}) [.tar.gz]",
        callback=validators.file_read,
        help="Input samples for fuzzer (will increase fuzzing efficiency)",
    ),
    config: str = typer.Option(
        ...,
        "-c",
        "--config",
        "--config-path",
        prompt_required=False,  # ?
        prompt=f"Config ({C_SKIP}) [.json]",
        callback=validators.file_read,
        help="Fuzzer advanced configuration: entry point, environment variables, e.t.c.",
    ),
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        upload_file(
            label="binaries",
            url=url_binaries(**ids),
            filepath=binaries,
            client=client,
            output_mode=output_mode,
        )

        if seeds.lower() != "n":
            upload_file(
                label="seeds",
                url=url_seeds(**ids),
                filepath=seeds,
                client=client,
                output_mode=output_mode,
            )

        if config.lower() != "n":
            upload_file(
                label="config",
                url=url_config(**ids),
                filepath=config,
                client=client,
                output_mode=output_mode,
            )

    output.success("Files uploaded successfully")


########################################
# Copy corpus files
########################################


@app.command(
    name="copy-corpus",
    help="Copy corpus files from source revision to target revision",
)
def copy_corpus_files(
    source_revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Source revision name or id",
    ),
    target_revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Target revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=target_revision,
            client=client,
        )

        ids_ = ids.copy()
        ids_.pop("revision_id")

        src_rev_id = get_revision_id(
            client=client,
            revision=source_revision,
            **ids_,
        )

        dst_rev_id = ids["revision_id"]
        if src_rev_id == dst_rev_id:
            raise BondiError("Source and target revision IDs are the same")

        json_body = {"src_rev_id": src_rev_id}
        response = client.put(url_corpus(**ids), json=json_body)
        parse_response_no_model(response)

    output.success("Corpus files copied successfully")


########################################
# Get revision
########################################


@app.command(
    name="get",
    help="Get revision by name or id",
)
def get_revision(
    ctx: typer.Context,
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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
    ResponseModel = GetRevisionResponseModel

    with AutologinClient() as client:

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        response = client.get(url_revision(**ids))
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Fuzzer name"),
        ("description", "Description"),
        ("status", "Status"),
        ("health", "Health"),
        ("cpu_usage", "CPU usage"),
        ("ram_usage", "RAM usage"),
        ("tmpfs_size", "TmpFS size"),
        ("binaries", "Fuzzer binaries"),
        ("seeds", "Input seeds"),
        ("config", "Configuration file"),
        ("image_id", "Agent image id"),
        ("feedback", "Feedback"),
        ("created", "Created"),
        ("last_start_date", "Last start date"),
        ("last_stop_date", "Last stop date"),
        ("deleted", "Deleted"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List revisions
########################################


@app.command(
    name="list",
    help="List fuzzer revisions",
)
def list_revisions(
    ctx: typer.Context,
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

    columns = [
        ("id", "ID", 0.1),
        ("name", "Revision name", 0.2),
        ("description", "Description", 0.3),
        ("status", "Status", 0.1),
        ("health", "Health", 0.1),
        ("created", "Created", 0.2),
    ]

    with AutologinClient() as client:
        data = send_list_revisions(fuzzer, project, user, client)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)


########################################
# Start revision
########################################


@app.command(
    name="start",
    help="Start revision (start fuzzing)",
)
def start_revision(
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        response = client.post(url_start(**ids))
        parse_response_no_model(response)

    output.success(f"Revision started")


########################################
# Restart revision
########################################


@app.command(
    name="restart",
    help="Restart revision ignoring all errors occurred erarlier",
)
def restart_revision(
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        response = client.post(url_restart(**ids))
        parse_response_no_model(response)

    output.success(f"Revision restarted")


########################################
# Stop revision
########################################


@app.command(
    name="stop",
    help="Stop revision (stop fuzzing)",
)
def stop_revision(
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        response = client.post(url_stop(**ids))
        parse_response_no_model(response)

    output.success(f"Revision stopped")


########################################
# Update revision
########################################


@app.command(
    name="update",
    help="Update revision",
)
def update_revision(
    ctx: typer.Context,
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        help="New revision name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        help="New revision description",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        response = client.patch(url_revision(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "name": "Project name",
        "description": "Description",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Update revision resources
########################################


@app.command(
    name="update-resources",
    help="Change amount of CPU or RAM allocated for revision",
)
def update_revision_resources(
    ctx: typer.Context,
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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
    cpu_usage: Optional[int] = typer.Option(
        None,
        "--cpu",
        "--cpu-usage",
        help="New amount of CPU to allocate for fuzzer",
    ),
    ram_usage: Optional[int] = typer.Option(
        None,
        "--ram",
        "--ram-usage",
        help="New amount of RAM to allocate for fuzzer",
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = UpdateResponseModel

    json_data = {
        "cpu_usage": cpu_usage,
        "ram_usage": ram_usage,
    }

    if all(v is None for v in json_data.values()):
        param_names = "|".join(map(make_option, json_data.keys()))
        output.error(f"{C_NO_PARAMS_SET}: [{param_names}]")
        raise typer.Exit(code=1)

    with AutologinClient() as client:

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        response = client.patch(url_resources(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "cpu_usage": "CPU usage (mCPU)",
        "ram_usage": "RAM usage (MB)",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Get last revision user worked with
########################################


@app.command(
    name="get-last",
    help="Get last revision user worked with",
)
def get_last_revision(
    ctx: typer.Context,
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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
    ResponseModel = GetRevisionResponseModel

    with AutologinClient() as client:

        ids = get_ids_for_fuzzer_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            client=client,
        )

        response = client.get(url_last_revision(**ids))
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Fuzzer name"),
        ("description", "Description"),
        ("status", "Status"),
        ("health", "Health"),
        ("cpu_usage", "CPU usage"),
        ("ram_usage", "RAM usage"),
        ("binaries", "Fuzzer binaries"),
        ("seeds", "Input seeds"),
        ("config", "Configuration file"),
        ("image_id", "Agent image id"),
        ("feedback", "Feedback"),
        ("created", "Created"),
        ("last_start_date", "Last start date"),
        ("last_stop_date", "Last stop date"),
        ("deleted", "Deleted"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Download files
########################################


@app.command(
    name="download-files",
    help="Download revision files: binaries, seeds, config",
)
def download_revision_files(
    ctx: typer.Context,
    binaries: Optional[str] = typer.Option(
        None,
        "-b",
        "--binaries",
        "--binaries-path",
    ),
    seeds: Optional[str] = typer.Option(
        None,
        "-s",
        "--seeds",
        "--seeds-path",
    ),
    config: Optional[str] = typer.Option(
        None,
        "-c",
        "--config",
        "--config-path",
    ),
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        if user is None:
            user_id = client.login_result.user_id
        else:
            user_id = get_user_id(user, client)

        project_id = get_project_id(project, user_id, client)
        fuzzer_id = get_fuzzer_id(fuzzer, project_id, user_id, client)
        revision_id = get_revision_id(revision, fuzzer_id, project_id, user_id, client)

        ResponseModel = GetRevisionResponseModel
        response = client.get(url_revision(revision_id, fuzzer_id, project_id, user_id))
        data: ResponseModel = parse_response(response, ResponseModel)

        def download_binaries(filepath: str):
            if data.binaries.uploaded:
                url = url_binaries(revision_id, fuzzer_id, project_id, user_id)
                download_file("binaries", url, filepath, client, output_mode)
            else:
                output.message("binaries - not uploaded", output_mode)

        def download_seeds(filepath: str):
            if data.seeds.uploaded:
                url = url_seeds(revision_id, fuzzer_id, project_id, user_id)
                download_file("seeds", url, filepath, client, output_mode)
            else:
                output.message("seeds - not uploaded", output_mode)

        def download_config(filepath: str):
            if data.config.uploaded:
                url = url_config(revision_id, fuzzer_id, project_id, user_id)
                download_file("config", url, filepath, client, output_mode)
            else:
                output.message("config - not uploaded", output_mode)

        if binaries or seeds or config:
            if binaries:
                download_binaries(binaries)
            if seeds:
                download_seeds(seeds)
            if config:
                download_config(config)
        else:
            download_binaries("binaries.tar.gz")
            download_seeds("seeds.tar.gz")
            download_config("config.json")

    output.success("Files downloaded successfully")


########################################
# Delete/restore/erase fuzzer
########################################


@app.command(
    name="delete",
    help="Delete fuzzer revision (move to trashbin)",
)
def delete_revision(
    ctx: typer.Context,
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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
        msg = "Do you really want to delete this revision?"
        typer.confirm(msg, abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        url = url_revision(**ids)
        query = {"action": DeleteActions.delete.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Revision deleted successfully")


@app.command(
    name="restore",
    help="Restore fuzzer revision (move out of trashbin)",
)
def restore_revision(
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        url = url_revision(**ids)
        query = {"action": DeleteActions.restore.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Revision restored successfully")


@app.command(
    name="erase",
    help="Erase fuzzer revision without recovery possibility",
)
def erase(
    ctx: typer.Context,
    backup: bool = typer.Option(
        True,
        help="Whether to make internal backup of revision",
    ),
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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
        msg = "Do you really want to erase this revision?"
        typer.confirm(f"{C_WARN_UNRECOVERABLE}\n{msg}", abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        query = {
            "action": DeleteActions.erase.value,
            "no_backup": not backup,
        }

        url = url_revision(**ids)
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Revision erased successfully")


########################################
# Set/unset/get default revision
########################################


@app.command(
    name="set-default",
    help="Enable auto substitution of '--revision' option with selected one",
    short_help="Enable auto substitution of '--revision' option",
)
def set_default_revision(
    revision: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_revision_name,
        help="Revision name or id",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name, in which the revision was created",
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

        ids = get_ids_for_revision_url(
            user=user,
            project=project,
            fuzzer=fuzzer,
            revision=revision,
            client=client,
        )

        if is_identifier(revision):
            response = client.get(url_revision(**ids))
            parse_response(response, GetRevisionResponseModel)

    save_default_revision(ids["revision_id"])
    output.success("Default revision set successfully")


@app.command(
    name="unset-default",
    help="Disable auto substitution of '--revision' option",
)
def unset_default_revision():

    if load_default_revision():
        remove_default_revision()
        output.success("Default revision unset successfully")
    else:
        output.error("Default revision not set")


@app.command(
    name="get-default",
    help="Get id of revision selected for auto substitution",
)
def get_default_revision():

    revision = load_default_revision()

    if revision is not None:
        output.result(revision)
    else:
        output.error("Default revision not set")
