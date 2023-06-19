from enum import Enum
from typing import List, Optional

import typer
from pydantic import BaseModel

from bondi import output, validators
from bondi import yandex_cloud as yc
from bondi.callback import DefaultUserCallback, PositiveIntCallback, StringCallback
from bondi.cli.admin.users import complete_user_name, get_user_id
from bondi.client import AutologinClient
from bondi.constants import C_NO_PARAMS_SET, C_WARN_UNRECOVERABLE
from bondi.defaults import (
    load_default_project,
    load_default_user,
    remove_default_project,
    save_default_project,
)
from bondi.errors import BadParameterError, InternalError, ServerSideValidationError
from bondi.helper import paginate, parse_response, parse_response_no_model
from bondi.models import AppContext, DeleteActions, UpdateResponseModel
from bondi.util import is_identifier, make_option, shorten, wrap_autocompletion_errors

########################################
# App
########################################

app = typer.Typer(name="projects", help="User's projects")

########################################
# Endpoints
########################################


def url_projects(user_id: str):
    return f"/api/v1/users/{user_id}/projects"


def url_project(project_id: str, user_id: str):
    return f"/api/v1/users/{user_id}/projects/{project_id}"


def url_project_pool(project_id: str, user_id: str):
    return f"/api/v1/users/{user_id}/projects/{project_id}/pool"


########################################
# Models
########################################


class ResourcePoolStatus(str, Enum):
    creating = "Creating"
    resizing = "Resizing"
    deleting = "Deleting"
    ready = "Ready"
    repairing = "Repairing"
    faulty = "Faulty"
    unreleased = "Unreleased"


class NodeGroupModel(BaseModel):
    node_cpu: int
    node_ram: int
    node_count: int


class PoolResourcesModel(BaseModel):
    cpu: int
    ram: int
    nodes: int


class ResourceLimits(BaseModel):
    min_value: int
    max_value: int


class FuzzerResourceLimits(BaseModel):
    cpu: ResourceLimits
    ram: ResourceLimits
    tmpfs: ResourceLimits
    ram_total: ResourceLimits


class GetPoolResponseModel(BaseModel):
    id: str
    status: ResourcePoolStatus
    node_group: NodeGroupModel
    resources: PoolResourcesModel
    fuzzer_limits: FuzzerResourceLimits

    def _node_group(self):
        node_cpu = self.node_group.node_cpu
        node_ram = self.node_group.node_ram
        node_count = self.node_group.node_count
        msg = "CPU per node: %d cores, RAM per node: %dGB, Node count: %d"
        return msg % (node_cpu, node_ram, node_count)

    def _resources(self):
        cpu = self.resources.cpu
        ram = self.resources.ram
        nodes = self.resources.nodes
        msg = "CPU in total: %d mcpu, RAM in total: %dMB, Nodes ready: %d"
        return msg % (cpu, ram, nodes)

    def display_dict(self):
        data = self.dict()
        data["status"] = self.status.value
        data["node_group"] = self._node_group()
        data["resources"] = self._resources()
        return data


class CreateProjectResponseModel(BaseModel):
    id: str
    name: str
    pool: GetPoolResponseModel

    def display_dict(self):
        data = self.dict()
        data["pool.status"] = self.pool.status.value
        return data


class GetProjectResponseModel(BaseModel):
    id: str
    name: str
    description: str
    pool: Optional[GetPoolResponseModel]

    def display_dict(self):

        data = self.dict()
        data["description"] = shorten(self.description)
        data["pool.status"] = "Error: no pool in this project"

        if self.pool is not None:
            pool_display = self.pool.display_dict()
            data["pool.status"] = pool_display["status"]
            data["pool.node_group"] = pool_display["node_group"]
            data["pool.resources"] = pool_display["resources"]

        return data


########################################
# Utils
########################################


def get_project_id(
    project: str,
    user_id: str,
    client: AutologinClient,
):
    if is_identifier(project):
        return project

    url = f"{url_projects(user_id)}/lookup"
    response = client.get(url, params={"name": project})
    ResponseModel = GetProjectResponseModel

    try:
        data: ResponseModel = parse_response(response, ResponseModel)
    except ServerSideValidationError as e:
        raise InternalError() from e

    return data.id


def get_owner_id(user: Optional[str], client: AutologinClient):
    return client.login_result.user_id if not user else get_user_id(user, client)


def get_ids_for_project_url(
    project: str,
    user: Optional[str],
    client: AutologinClient,
):
    user_id = get_owner_id(user, client)
    project_id = get_project_id(project, user_id, client)

    return {
        "user_id": user_id,
        "project_id": project_id,
    }


def send_get_project(
    project: str,
    user: Optional[str],
    client: AutologinClient,
):
    ids = get_ids_for_project_url(
        user=user,
        project=project,
        client=client,
    )

    ResponseModel = GetProjectResponseModel
    response = client.get(url_project(**ids))
    data: ResponseModel = parse_response(response, ResponseModel)

    return data


def send_list_projects(user: Optional[str], client: AutologinClient):

    data = []
    ResponseModel = GetProjectResponseModel

    owner_id = get_owner_id(user, client)
    url = url_projects(owner_id)

    project: ResponseModel
    for project in paginate(client, url, ResponseModel):
        data.append(project.display_dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_project_name(ctx: typer.Context, incomplete: str):

    user = ctx.params.get("user") or load_default_user()

    with AutologinClient() as client:
        projects = send_list_projects(user, client)

    project_names: List[str] = [project["name"] for project in projects]
    return list(filter(lambda u: u.startswith(incomplete), project_names))


@wrap_autocompletion_errors
def complete_node_cpu(ctx: typer.Context, opt_cpu: str):

    all_cpu_values = yc.get_all_cpu_values()
    all_mem_values = yc.get_all_mem_values()
    node_ram: Optional[int] = ctx.params.get("node_ram")

    def get_cpu_values(mem: int):

        res = []
        for cpu in all_cpu_values:
            if yc.is_valid_mem_per_core(cpu, mem):
                res.append(cpu)

        return res

    cpu_values = []
    if node_ram and node_ram in all_mem_values:
        cpu_values.extend(get_cpu_values(node_ram))
    else:
        cpu_values.extend(all_cpu_values)

    #
    # XXX: Use zfill hack here
    # because shell sort breaks the order
    #

    def to_str(x):
        return str(x).zfill(2)

    cpu_values_str = list(map(to_str, sorted(cpu_values, reverse=True)))
    return list(filter(lambda s: s.startswith(opt_cpu), cpu_values_str))


@wrap_autocompletion_errors
def complete_node_ram(ctx: typer.Context, opt_ram: str):

    all_cpu_values = yc.get_all_cpu_values()
    all_mem_values = yc.get_all_mem_values()
    node_cpu: Optional[int] = ctx.params.get("node_cpu")

    def get_mem_values(cpu: int):

        res = []
        for mem in all_mem_values:
            if yc.is_valid_mem_per_core(cpu, mem):
                res.append(mem)

        return res

    mem_values = []
    if node_cpu and node_cpu in all_cpu_values:
        mem_values.extend(get_mem_values(node_cpu))
    else:
        mem_values.extend(all_mem_values)

    #
    # XXX: Use zfill hack here
    # because shell sort breaks the order
    #

    def to_str(x):
        return str(x).zfill(3)

    mem_values_str = list(map(to_str, sorted(mem_values, reverse=True)))
    return list(filter(lambda s: s.startswith(opt_ram), mem_values_str))


########################################
# Create project
########################################


@app.command(
    name="create",
    help="Create new project",
)
def create_project(
    ctx: typer.Context,
    name: str = typer.Option(
        None,
        "-n",
        "--name",
        callback=StringCallback(),
        help="Project name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        callback=validators.string,
        help="Project description",
    ),
    node_cpu: int = typer.Option(
        None,
        "--node-cpu",
        "--node-cpu-cores",
        callback=PositiveIntCallback(),
        autocompletion=complete_node_cpu,
        help="Amount of CPU to allocate per node (in cores). Use <TAB> to get possible values",
    ),
    node_ram: int = typer.Option(
        None,
        "--node-ram",
        "--node-ram-gb",
        callback=PositiveIntCallback(),
        autocompletion=complete_node_ram,
        help="Amount of RAM to allocate per node (in GB). Use <TAB> to get possible values",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = CreateProjectResponseModel

    if node_cpu not in yc.get_all_cpu_values():
        msg = "Invalid number of cpu cores"
        raise BadParameterError(ctx, msg, "node_cpu")

    if node_ram not in yc.get_all_mem_values():
        msg = "Invalid amount of memory"
        raise BadParameterError(ctx, msg, "node_ram")

    if not yc.is_valid_mem_per_core(node_cpu, node_ram):
        msg = "Inconsistent values of cpu cores and ram"
        raise BadParameterError(ctx, msg, "node_cpu", "node_ram")

    json_data = {
        "name": name,
        "description": description or "No description",  # TODO: fix api server
        "pool": {
            "node_cpu": node_cpu,
            "node_ram": node_ram,
        },
    }

    with AutologinClient() as client:
        url = url_projects(get_owner_id(user, client))
        response = client.post(url, json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Project name"),
        ("pool.status", "Pool status"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Create project pool
########################################


class CreatePoolResponseModel(BaseModel):
    id: str
    status: str


@app.command(
    name="create-pool",
    help="Create new pool for project",
)
def create_pool(
    ctx: typer.Context,
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    node_cpu: int = typer.Option(
        None,
        "--node-cpu",
        "--node-cpu-cores",
        callback=PositiveIntCallback(),
        autocompletion=complete_node_cpu,
        help="Amount of CPU to allocate per node (in cores). Use <TAB> to get possible values",
    ),
    node_ram: int = typer.Option(
        None,
        "--node-ram",
        "--node-ram-gb",
        callback=PositiveIntCallback(),
        autocompletion=complete_node_ram,
        help="Amount of RAM to allocate per node (in GB). Use <TAB> to get possible values",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = CreatePoolResponseModel

    if node_cpu not in yc.get_all_cpu_values():
        msg = "Invalid number of cpu cores"
        raise BadParameterError(ctx, msg, "node_cpu")

    if node_ram not in yc.get_all_mem_values():
        msg = "Invalid amount of memory"
        raise BadParameterError(ctx, msg, "node_ram")

    if not yc.is_valid_mem_per_core(node_cpu, node_ram):
        msg = "Inconsistent values of cpu cores and ram"
        raise BadParameterError(ctx, msg, "node_cpu", "node_ram")

    json_data = {
        "node_cpu": node_cpu,
        "node_ram": node_ram,
    }

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        response = client.post(url_project_pool(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("status", "Status"),
    ]

    output.dict_data(data.dict(), columns, output_mode)


########################################
# Get project pool
########################################


@app.command(
    name="get-pool",
    help="Get pool by project name or id",
)
def get_project(
    ctx: typer.Context,
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = GetPoolResponseModel

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        response = client.get(url_project_pool(**ids))
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("status", "Status"),
        ("node_group", "Node group"),
        ("resources", "Resources"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Get project
########################################


@app.command(
    name="get",
    help="Get project information by name or id",
)
def get_project(
    ctx: typer.Context,
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = GetProjectResponseModel

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        response = client.get(url_project(**ids))
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Project name"),
        ("description", "Description"),
        ("pool.status", "Pool status"),
    ]

    pool_columns = [
        ("pool.node_group", "Node group"),
        ("pool.resources", "Pool resources"),
    ]

    if data.pool:
        columns.extend(pool_columns)

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List projects
########################################


@app.command(
    name="list",
    help="List projects",
)
def list_projects(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    columns = [
        ("id", "ID", 0.2),
        ("name", "Project name", 0.2),
        ("description", "Description", 0.4),
        ("pool.status", "Pool status", 0.2),
    ]

    with AutologinClient() as client:
        data = send_list_projects(user, client)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)


########################################
# Update project
########################################


@app.command(
    name="update",
    help="Update project",
)
def update_project(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        prompt_required=False,
        callback=validators.string,
        help="New project name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        prompt_required=False,
        callback=validators.string,
        help="New project description",
    ),
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
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

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        response = client.patch(url_project(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "name": "Project name",
        "description": "Description",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Update project pool
########################################


@app.command(
    name="update-pool",
    help="Update project pool CPU/RAM",
)
def update_project_pool(
    ctx: typer.Context,
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    node_cpu: Optional[int] = typer.Option(
        None,
        "--node-cpu",
        "--node-cpu-cores",
        autocompletion=complete_node_cpu,
        callback=PositiveIntCallback(required=False),
        help="Amount of CPU to allocate per node (in cores). Use <TAB> to get possible values",
    ),
    node_ram: Optional[int] = typer.Option(
        None,
        "--node-ram",
        "--node-ram-gb",
        autocompletion=complete_node_ram,
        callback=PositiveIntCallback(required=False),
        help="Amount of RAM to allocate per node (in GB). Use <TAB> to get possible values",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode

    json_data = {
        "node_cpu": node_cpu,
        "node_ram": node_ram,
    }

    if all(v is None for v in json_data.values()):
        param_names = "|".join(map(make_option, json_data.keys()))
        output.error(f"{C_NO_PARAMS_SET}: [{param_names}]")
        raise typer.Exit(code=1)

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        url = url_project_pool(**ids)
        response = client.patch(url, json=json_data)
        parse_response_no_model(response)

    output.success("Pool resources updated successfully")


########################################
# Delete/restore/erase project
########################################


@app.command(
    name="delete-pool",
    help="Stop all fuzzers and delete project pool",
)
def delete_project(
    ctx: typer.Context,
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to delete this pool?"
        typer.confirm(msg, abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        response = client.delete(url_project_pool(**ids))
        parse_response_no_model(response)

    output.success("Pool deleted successfully")


@app.command(
    name="delete",
    help="Delete project (move to trash bin)",
)
def delete_project(
    ctx: typer.Context,
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to delete this project?"
        typer.confirm(msg, abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        url = url_project(**ids)
        query = {"action": DeleteActions.delete.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Project deleted successfully")


@app.command(
    name="restore",
    help="Restore project (move out of trash bin)",
)
def restore_project(
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        url = url_project(**ids)
        query = {"action": DeleteActions.restore.value}
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Project restored successfully")


@app.command(
    name="erase",
    help="Delete project without recovery possibility",
)
def erase_project(
    ctx: typer.Context,
    backup: bool = typer.Option(
        True,
        help="Whether to create a backup of erased project data",
    ),
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to erase this project?"
        typer.confirm(f"{C_WARN_UNRECOVERABLE}\n{msg}", abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        query = {
            "action": DeleteActions.erase.value,
            "no_backup": not backup,
        }

        url = url_project(**ids)
        response = client.delete(url, params=query)
        parse_response_no_model(response)

    output.success("Project erased successfully")


########################################
# Set/unset/get default project
########################################


@app.command(
    name="set-default",
    help="Enable auto substitution of '--project' option with selected one",
    short_help="Enable auto substitution of '--project' option",
)
def set_default_project(
    project: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_project_name,
        help="Project name or id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the project (admin only)",
        hidden=True,
    ),
):
    with AutologinClient() as client:

        ids = get_ids_for_project_url(
            user=user,
            project=project,
            client=client,
        )

        if is_identifier(project):
            response = client.get(url_project(**ids))
            parse_response(response, GetProjectResponseModel)

    save_default_project(ids["project_id"])
    output.success("Default project set successfully")


@app.command(
    name="unset-default",
    help="Disable auto substitution of '--project' option",
)
def unset_default_project():

    if load_default_project():
        remove_default_project()
        output.success("Default project unset successfully")
    else:
        output.error("Default project not set")


@app.command(
    name="get-default",
    help="Get id of project selected for auto substitution",
)
def get_default_project():

    project = load_default_project()

    if project is not None:
        output.result(project)
    else:
        output.error("Default project not set")
