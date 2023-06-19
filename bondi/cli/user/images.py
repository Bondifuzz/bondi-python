from typing import Optional

import typer
from pydantic import BaseModel

from bondi import output
from bondi.callback import DefaultProjectCallback, DefaultUserCallback
from bondi.cli.user.projects import complete_project_name, get_ids_for_project_url
from bondi.client import AutologinClient
from bondi.helper import paginate
from bondi.models import AppContext, FuzzerLang, FuzzingEngine
from bondi.util import shorten

########################################
# App
########################################

app = typer.Typer(name="images", help="User's agent images")

########################################
# Endpoints
########################################


def url_images(project_id: str, user_id: str):
    return f"/api/v1/users/{user_id}/projects/{project_id}/images"


########################################
# Util
########################################

########################################
# List available images
########################################


class GetImageResponseModel(BaseModel):
    id: str
    name: str
    description: str
    type: str
    status: str
    # engine: str
    # lang: str

    def display_dict(self):
        data = self.dict()
        data["description"] = shorten(self.description)
        return data


@app.command(
    name="list-available",
    help="Get images corresponding to provided <Programming language, Fuzzing engine>",
    short_help="Get images corresponding to provided <lang, engine>",
)
def list_available(
    ctx: typer.Context,
    lang: FuzzerLang = typer.Option(
        ...,
        "-l",
        "--lang",
        "--fuzzer-lang",
        prompt=True,
        prompt_required=False,
        help="Programming language, for which the fuzzer is designed",
    ),
    engine: FuzzingEngine = typer.Option(
        ...,
        "-e",
        "--engine",
        "--fuzzing-engine",
        prompt=True,
        prompt_required=False,
        help="Engine, for which the fuzzer is designed",
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
        help="Name or id of owner (admin only)",
        hidden=True,
    ),
):
    data = []
    ResponseModel = GetImageResponseModel

    with AutologinClient() as client:

        client.params = {
            "langs": [lang.value],
            "engines": [engine.value],
        }

        url = url_images(
            **get_ids_for_project_url(project, user, client),
        )

        image: ResponseModel
        for image in paginate(client, url, ResponseModel):
            data.append(image.display_dict())

    columns = [
        ("id", "ID", 0.2),
        ("name", "Image name", 0.2),
        ("description", "Description", 0.4),
        ("type", "Image type", 0.1),
        ("status", "Status", 0.1),
    ]

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)
