from typing import Dict, List, Optional

import typer
from pydantic import BaseModel

from bondi import output, validators
from bondi.callback import (
    CallbackInvoker,
    DefaultProjectCallback,
    DefaultUserCallback,
    StringCallback,
    UrlCallback,
)
from bondi.cli.admin.users import complete_user_name
from bondi.client import AutologinClient
from bondi.constants import C_NO_PARAMS_SET, C_NOT_YET, C_WARN_UNRECOVERABLE
from bondi.defaults import load_default_project, load_default_user
from bondi.errors import InternalError, ServerSideValidationError
from bondi.helper import paginate, parse_response, parse_response_no_model
from bondi.models import (
    AppContext,
    ICredentialsDisplay,
    IntegrationStatus,
    IntegrationType,
    UpdateResponseModel,
)
from bondi.util import is_identifier, make_option, wrap_autocompletion_errors

from .projects import complete_project_name, get_ids_for_project_url

########################################
# App
########################################

app = typer.Typer(name="integrations", help="Project integrations")

########################################
# Endpoints
########################################


def url_integrations(project_id, user_id: str):
    return f"/api/v1/users/{user_id}/projects/{project_id}/integrations"


def url_integration(integration_id: str, project_id, user_id: str):
    return f"{url_integrations(project_id, user_id)}/{integration_id}"


def url_integration_enable(integration_id: str, project_id, user_id: str):
    return f"{url_integration(integration_id, project_id, user_id)}/enabled"


def url_integration_config(integration_id: str, project_id, user_id: str):
    return f"{url_integration(integration_id, project_id, user_id)}/config"


########################################
# Models
########################################


class CreateIntegrationResponseModel(BaseModel):

    id: str
    """ Unique identifier of integration """

    name: str
    """ Name of integration. Must be created by user """

    type: IntegrationType
    """ Type of integration: jira, youtrack, mail, etc... """

    status: IntegrationStatus
    """ Integration status: whether works or not """

    enabled: bool
    """ When set, integration with bug tracker is enabled """

    num_undelivered: int
    """ Count of reports which were not delivered to bug tracker """

    last_error: Optional[str]
    """ Last error caused integration to fail """

    def display_dict(self):
        data = self.dict()
        data["type"] = self.type.value
        data["status"] = self.status.value
        data["last_error"] = self.last_error or C_NOT_YET
        return data


class GetIntegrationResponseModel(BaseModel):
    id: str
    name: str
    type: IntegrationType
    status: IntegrationStatus
    last_error: Optional[str]
    num_undelivered: int
    enabled: bool

    def display_dict(self):
        data = self.dict()
        data["type"] = self.type.value
        data["status"] = self.status.value
        data["last_error"] = self.last_error or C_NOT_YET
        return data


class JiraIntegrationConfig(BaseModel, ICredentialsDisplay):

    id: str
    url: str
    project: str
    username: str
    password: str
    issue_type: str
    priority: str

    def display_dict(self, hide: bool = True):

        data = self.dict()

        if hide:
            data["username"] = "*" * 16 + data["username"][-4:]
            data["password"] = "*" * 16 + data["password"][-4:]

        return data


########################################
# Utils
########################################

JIRA_ISSUE_TYPES = ["Bug", "Story", "Task", "<Custom>"]
JIRA_PRIORITIES = ["Lowest", "Low", "Medium", "High", "Highest", "<Custom>"]


def get_integration_id(
    integration: str,
    project_id: str,
    user_id: str,
    client: AutologinClient,
):
    if is_identifier(integration):
        return integration

    url = url_integrations(project_id, user_id)
    response = client.get(f"{url}/lookup?name={integration}")
    ResponseModel = GetIntegrationResponseModel

    try:
        data: ResponseModel = parse_response(response, ResponseModel)
    except ServerSideValidationError as e:
        raise InternalError() from e

    return data.id


def get_ids_for_integration_url(
    integration: str,
    project: str,
    user: str,
    client: AutologinClient,
):
    ids = get_ids_for_project_url(project, user, client)
    integration_id = get_integration_id(integration=integration, **ids, client=client)
    return {"integration_id": integration_id, **ids}


def send_create_integration(
    json_data: Dict[str, str],
    project: str,
    user: str,
):
    with AutologinClient() as client:
        ResponseModel = CreateIntegrationResponseModel
        ids = get_ids_for_project_url(project, user, client)
        response = client.post(url_integrations(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    return data


def send_get_integration(
    integration: str,
    project: str,
    user: str,
):
    with AutologinClient() as client:

        ids = get_ids_for_integration_url(
            user=user,
            integration=integration,
            project=project,
            client=client,
        )

        ResponseModel = GetIntegrationResponseModel
        response = client.get(url_integration(**ids))
        data: ResponseModel = parse_response(response, ResponseModel)

    return data


def send_list_integrations(
    project: str,
    user: Optional[str],
    client: AutologinClient,
):
    data = []
    ResponseModel = GetIntegrationResponseModel

    url = url_integrations(
        **get_ids_for_project_url(project, user, client),
    )

    integration: ResponseModel
    for integration in paginate(client, url, ResponseModel):
        data.append(integration.display_dict())

    return data


########################################
# Autocompletion
########################################


@wrap_autocompletion_errors
def complete_integration_name(ctx: typer.Context, incomplete: str):

    user = ctx.params.get("user") or load_default_user()
    project = ctx.params.get("project") or load_default_project()

    with AutologinClient() as client:
        integrations = send_list_integrations(project, user, client)

    integr_names: List[str] = [integr["name"] for integr in integrations]
    return list(filter(lambda u: u.startswith(incomplete), integr_names))


########################################
# Create integration
########################################


def make_jira_config_dict(
    invoker: CallbackInvoker,
    url: Optional[str],
    username: Optional[str],
    password: Optional[str],
    project: Optional[str],
    priority: Optional[str],
    issue_type: Optional[str],
    required: bool = True,
):
    if not url:
        url = invoker.invoke_callback_for_option(
            option_name="jira_url",
            callback=UrlCallback(required),
        )

    if not username:
        username = invoker.invoke_callback_for_option(
            option_name="jira_username",
            callback=StringCallback(required),
        )

    if not password:
        password = invoker.invoke_callback_for_option(
            option_name="jira_password",
            callback=StringCallback(required),
        )

    if not project:
        project = invoker.invoke_callback_for_option(
            option_name="jira_project",
            callback=StringCallback(required),
        )

    if not priority:
        priority = invoker.invoke_callback_for_option(
            option_name="jira_priority",
            callback=StringCallback(required),
        )

    if not issue_type:
        issue_type = invoker.invoke_callback_for_option(
            option_name="jira_issue_type",
            callback=StringCallback(required),
        )

    return {
        "url": url,
        "username": username,
        "password": password,
        "project": project,
        "priority": priority,
        "issue_type": issue_type,
    }


@app.command(
    name="create",
    help="Create new integration with bug tracker",
)
def create_integration(
    ctx: typer.Context,
    ########################################
    # Jira integration config
    ########################################
    jira_url: Optional[str] = typer.Option(
        None,
        "--jira-url",
        callback=UrlCallback(required=False),
        help="Url to Jira server",
    ),
    jira_username: Optional[str] = typer.Option(
        None,
        "--jira-username",
        callback=StringCallback(required=False),
        help="Jira account username",
    ),
    jira_password: Optional[str] = typer.Option(
        None,
        "--jira-password",
        hide_input=True,
        callback=StringCallback(required=False),
        help="Jira account password or access token",
    ),
    jira_project: Optional[str] = typer.Option(
        None,
        "--jira-project",
        callback=StringCallback(required=False),
        help="Jira project name",
    ),
    jira_issue_type: Optional[str] = typer.Option(
        None,
        "--jira-issue-type",
        callback=StringCallback(required=False),
        autocompletion=lambda: JIRA_ISSUE_TYPES,
        metavar=f"[{'|'.join(JIRA_ISSUE_TYPES)}]",
        help="Jira issue type",
    ),
    jira_priority: Optional[str] = typer.Option(
        None,
        "--jira-priority",
        autocompletion=lambda: JIRA_PRIORITIES,
        metavar=f"[{'|'.join(JIRA_PRIORITIES)}]",
        callback=StringCallback(required=False),
        help="Jira priority",
    ),
    ########################################
    # Common
    ########################################
    integration_name: str = typer.Option(
        None,
        "-n",
        "--name",
        callback=StringCallback(),
        help="Integration name",
    ),
    integration_type: IntegrationType = typer.Option(
        None,
        "-t",
        "--type",
        callback=StringCallback(),
        autocompletion=lambda: [t.value for t in IntegrationType],
        metavar=f"[{'|'.join([t.value for t in IntegrationType])}]",
        help="Integration type",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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
    invoker = CallbackInvoker(ctx)

    json_data = {
        "name": integration_name,
        "type": integration_type,
    }

    if integration_type == IntegrationType.jira:

        cfg = make_jira_config_dict(
            invoker,
            jira_url,
            jira_username,
            jira_password,
            jira_project,
            jira_priority,
            jira_issue_type,
        )

        json_data.update({"config": cfg})
        data = send_create_integration(
            json_data,
            project,
            user,
        )

    else:
        assert False, "Unreachable"

    columns = [
        ("id", "ID"),
        ("name", "Integration name"),
        ("type", "Type"),
        ("status", "Status"),
        ("enabled", "Enabled"),
    ]

    assert data is not None
    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Get integration
########################################


@app.command(
    name="get",
    help="Get integration by name or id",
)
def get_integration(
    ctx: typer.Context,
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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

    data = send_get_integration(
        integration,
        project,
        user,
    )

    columns = [
        ("id", "ID"),
        ("name", "Integration name"),
        ("type", "Type"),
        ("status", "Status"),
        ("enabled", "Enabled"),
        ("num_undelivered", "Undelivered reports"),
        ("last_error", "Last Error"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List integrations
########################################


@app.command(
    name="list",
    help="List integrations in project",
)
def list_integrations(
    ctx: typer.Context,
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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
        data = send_list_integrations(project, user, client)

    columns = [
        ("id", "ID", 0.2),
        ("name", "Integration name", 0.3),
        ("type", "Type", 0.1),
        ("status", "Status", 0.1),
        ("enabled", "Enabled", 0.1),
        ("num_undelivered", "Undelivered reports", 0.2),
    ]

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)


########################################
# Update integration
########################################


@app.command(
    name="update",
    help="Update integration",
)
def update_integration(
    ctx: typer.Context,
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        callback=validators.string,
        help="New integration name",
    ),
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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
    }

    if all(v is None for v in json_data.values()):
        param_names = "|".join(map(make_option, json_data.keys()))
        output.error(f"{C_NO_PARAMS_SET}: [{param_names}]")
        raise typer.Exit(code=1)

    with AutologinClient() as client:

        ids = get_ids_for_integration_url(
            user=user,
            integration=integration,
            project=project,
            client=client,
        )

        response = client.patch(url_integration(**ids), json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "name": "Integration name",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Get integration config
########################################


@app.command(
    name="get-config",
    help="Get bug tracker configuration",
)
def get_integration_config(
    ctx: typer.Context,
    hide: bool = typer.Option(
        True,
        help="Whether to hide sensitive content",
    ),
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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
    ResponseModel = GetIntegrationResponseModel

    with AutologinClient() as client:

        ids = get_ids_for_integration_url(
            user=user,
            integration=integration,
            project=project,
            client=client,
        )

        response = client.get(url_integration(**ids))
        integration: ResponseModel = parse_response(response, ResponseModel)

        if integration.type == IntegrationType.jira:

            columns = [
                ("id", "Config ID"),
                ("url", "Jira URL"),
                ("username", "Username"),
                ("password", "Password"),
                ("project", "Project"),
                ("issue_type", "Issue type"),
                ("priority", "Priority"),
            ]

            ResponseModelCfg = JiraIntegrationConfig

        # elif integration.type == IntegrationType.youtrack:

        #     columns = [
        #         ("id", "ID"),
        #         ("name", "Integration name"),
        #         ("type", "Type"),
        #         ("status", "Status"),
        #         ("enabled", "Enabled"),
        #         ("num_undelivered", "Undelivered reports"),
        #         ("last_error", "Last Error"),
        #     ]

        #     ResponseModelCfg = GetJiraIntegrationConfigResponseModel

        else:
            assert False, "Unreachable"

        response = client.get(url_integration_config(**ids))
        config: ResponseModelCfg = parse_response(response, ResponseModelCfg)
        output.dict_data(config.display_dict(hide), columns, output_mode)


########################################
# Update integration config
########################################


def send_update_integration(
    json_data: Dict[str, str],
    integration: str,
    project: str,
    user: str,
):
    with AutologinClient() as client:

        ids = get_ids_for_integration_url(
            user=user,
            integration=integration,
            project=project,
            client=client,
        )

        # TODO: put -> patch when partial update will be implemented

        url = url_integration_config(**ids)
        response = client.put(url, json=json_data)

        ResponseModel = UpdateResponseModel
        data: ResponseModel = parse_response(response, ResponseModel)

    return data


@app.command(
    name="update-config",
    help="Update bug tracker configuration",
)
def update_integration_config(
    ctx: typer.Context,
    ########################################
    # Jira integration config
    ########################################
    jira_url: Optional[str] = typer.Option(
        None,
        "--jira-url",
        callback=UrlCallback(required=False),
        help="New Jira server url",
    ),
    jira_username: Optional[str] = typer.Option(
        None,
        "--jira-username",
        callback=StringCallback(required=False),
        help="New Jira account username",
    ),
    jira_password: Optional[str] = typer.Option(
        None,
        "--jira-password",
        callback=StringCallback(required=False),
        help="New Jira account password or access token",
    ),
    jira_project: Optional[str] = typer.Option(
        None,
        "--jira-project",
        callback=StringCallback(required=False),
        help="New Jira project name",
    ),
    jira_issue_type: Optional[str] = typer.Option(
        None,
        "--jira-issue-type",
        callback=StringCallback(required=False),
        help="New Jira issue type",
    ),
    jira_priority: Optional[str] = typer.Option(
        None,
        "--jira-priority",
        callback=StringCallback(required=False),
        help="New Jira priority",
    ),
    ########################################
    # Common
    ########################################
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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
    invoker = CallbackInvoker(ctx)

    target_integration = send_get_integration(
        integration,
        project,
        user,
    )

    json_data = {
        "type": target_integration.type,
    }

    if target_integration.type == IntegrationType.jira:

        cfg = make_jira_config_dict(
            invoker,
            jira_url,
            jira_username,
            jira_password,
            jira_project,
            jira_priority,
            jira_issue_type,
            required=False,
        )

        if all(v is None for v in cfg.values()):
            param_names = "|".join(map(make_option, cfg.keys()))
            output.error(f"{C_NO_PARAMS_SET}: [{param_names}]")
            raise typer.Exit(code=1)

        json_data.update({"config": cfg})
        data = send_update_integration(
            json_data,
            integration,
            project,
            user,
        )

        columns = {
            "id": "Config ID",
            "url": "Jira URL",
            "username": "Username",
            "password": "Password",
            "project": "Project",
            "issue_type": "Issue type",
            "priority": "Priority",
        }

    else:
        assert False, "Unreachable"

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Enable/disable integration
########################################


@app.command(
    name="enable",
    help="Enable crash notifications in bug tracker",
)
def enable_integration(
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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

        ids = get_ids_for_integration_url(
            user=user,
            integration=integration,
            project=project,
            client=client,
        )

        url = url_integration_enable(**ids)
        response = client.put(url, json={"enabled": True})
        parse_response(response, UpdateResponseModel)

    output.success(f"Integration enabled")


@app.command(
    name="disable",
    help="Disable crash notifications in bug tracker",
)
def disable_integration(
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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

        ids = get_ids_for_integration_url(
            user=user,
            integration=integration,
            project=project,
            client=client,
        )

        url = url_integration_enable(**ids)
        response = client.put(url, json={"enabled": False})
        parse_response(response, UpdateResponseModel)

    output.success(f"Integration disabled")


########################################
# Delete integration
########################################


@app.command(
    name="delete",
    help="Delete integration",
)
def delete_integration(
    ctx: typer.Context,
    integration: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_integration_name,
        help="Integration name or id",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Name or id of project",
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
        msg = "Do you really want to delete this integration?"
        typer.confirm(f"{C_WARN_UNRECOVERABLE}\n{msg}", abort=True)

    with AutologinClient() as client:

        ids = get_ids_for_integration_url(
            user=user,
            project=project,
            integration=integration,
            client=client,
        )

        response = client.delete(url_integration(**ids))
        parse_response_no_model(response)

    output.success("Integration deleted successfully")
