from typing import List, Optional

import typer
from pydantic import BaseModel

from bondi import output, validators
from bondi.callback import DefaultUserCallback, PositiveIntCallback, StringCallback
from bondi.cli.admin.users import complete_user_name, get_user_id
from bondi.client import AutologinClient
from bondi.defaults import  load_default_user
from bondi.helper import paginate, parse_response, parse_response_no_model
from bondi.models import AppContext
from bondi.util import wrap_autocompletion_errors


########################################
# App
########################################

app = typer.Typer(name="pools", help="User's pools")

########################################
# Endpoints
########################################


def url_pools(user_id: str):
    return f"/api/v1/users/{user_id}/pools"


def url_pool(pool_id: str, user_id: str):
    return f"/api/v1/users/{user_id}/pools/{pool_id}"

########################################
# Models
########################################

class PoolResources(BaseModel):
    cpu_total: int
    ram_total: int
    nodes_total: int

    cpu_avail: int
    ram_avail: int
    nodes_avail: int

    fuzzer_max_cpu: int
    fuzzer_max_ram: int

class GetPoolResponseModel(BaseModel):
    id: str
    resources: PoolResources

    def _resources(self):
        cpu = self.resources.cpu_avail
        ram = self.resources.ram_avail
        nodes = self.resources.nodes_avail
        msg = "CPU avail: %d mcpu, RAM avail: %d MB, Nodes: %d"
        return msg % (cpu, ram, nodes)

    def display_dict(self):
        data = self.dict()
        data["resources"] = self._resources()
        return data


########################################
# Utils
########################################


def get_owner_id(user: Optional[str], client: AutologinClient):
    return client.login_result.user_id if not user else get_user_id(user, client)


def send_get_pool(
    pool_id: str,
    user: Optional[str],
    client: AutologinClient,
):
    ResponseModel = GetPoolResponseModel
    user_id = get_owner_id(user, client)
    response = client.get(url_pool(pool_id, user_id))
    data: ResponseModel = parse_response(response, ResponseModel)

    return data


def send_list_pools(user: Optional[str], client: AutologinClient):

    data = []
    ResponseModel = GetPoolResponseModel

    owner_id = get_owner_id(user, client)
    url = url_pools(owner_id)

    pool: ResponseModel
    for pool in paginate(client, url, ResponseModel):
        data.append(pool.display_dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_pool_id(ctx: typer.Context, incomplete: str):
    user = ctx.params.get("user") or load_default_user()

    with AutologinClient() as client:
        pools = send_list_pools(user, client)

    pools_ids: List[str] = [pool["id"] for pool in pools]
    return list(filter(lambda p: p.startswith(incomplete), pools_ids))


########################################
# Get pool
########################################


@app.command(
    name="get",
    help="Get pool information by name or id",
)
def get_pool(
    ctx: typer.Context,
    pool_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_pool_id,
        help="Pool id",
    ),
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the pool (admin only)",
        hidden=True,
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = GetPoolResponseModel

    with AutologinClient() as client:
        user_id = get_owner_id(user, client)
        response = client.get(url_pool(pool_id, user_id))
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("resources", "Resources"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List pools
########################################


@app.command(
    name="list",
    help="List pools",
)
def list_pools(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(
        None,
        "-u",
        "--user",
        callback=DefaultUserCallback(),
        autocompletion=complete_user_name,
        help="Owner of the pool (admin only)",
        hidden=True,
    ),
):
    columns = [
        ("id", "ID", 0.2),
        ("resources", "Resources", 0.8),
    ]

    with AutologinClient() as client:
        data = send_list_pools(user, client)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)