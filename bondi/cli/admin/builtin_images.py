import itertools
from typing import Dict, List, Optional

import typer
from pydantic import BaseModel

from bondi import output, validators
from bondi.callback import OptionCallback
from bondi.client import AutologinClient
from bondi.constants import C_NO_PARAMS_SET
from bondi.helper import paginate, parse_response, parse_response_no_model
from bondi.meta import list_fuzzer_configurations
from bondi.models import (
    AppContext,
    FuzzerLang,
    FuzzingEngine,
    ImageStatus,
    ImageType,
    UpdateResponseModel,
)
from bondi.util import make_option, wrap_autocompletion_errors

########################################
# App
########################################


app = typer.Typer(
    name="images",
    help="Manage agent docker images (built-in)",
)

########################################
# Endpoints
########################################

URL_IMAGES = "/api/v1/admin/images"
URL_REGISTRY = "cr.yandex/<registry-id>"

########################################
# Models
########################################


class CreateImageResponseModel(BaseModel):
    id: str
    name: str
    status: ImageStatus

    def display_dict(self):
        data = self.dict()
        data["status"] = self.status.value
        return data


class GetImageResponseModel(BaseModel):
    id: str
    name: str
    description: str
    type: ImageType
    status: ImageStatus
    engine: FuzzingEngine
    lang: FuzzerLang

    def display_dict(self):
        data = self.dict()
        data["type"] = self.type.value
        data["status"] = self.status.value
        data["engine"] = self.engine.value
        data["lang"] = self.lang.value
        return data


########################################
# Utils
########################################


def send_list_images(client: AutologinClient):

    data = []
    ResponseModel = GetImageResponseModel

    image: ResponseModel
    for image in paginate(client, URL_IMAGES, ResponseModel):
        data.append(image.display_dict())

    return data


########################################
# Autocompletion
########################################

FUZZING_ENGINES = [e.value for e in FuzzingEngine]
FUZZER_LANGS = [l.value for l in FuzzerLang]


@wrap_autocompletion_errors
def get_engine_by_lang(ctx: typer.Context, opt_engine: str):

    opt_lang = ctx.params.get("lang")
    if opt_lang is None or opt_lang not in FUZZER_LANGS:
        return FUZZING_ENGINES

    with AutologinClient() as client:
        configurations = list_fuzzer_configurations(client)

    configurations_dict = configurations.dict(by_alias=True)
    engines: List[str] = configurations_dict[opt_lang]

    return list(filter(lambda e: e.startswith(opt_engine), engines))


@wrap_autocompletion_errors
def get_lang_by_engine(ctx: typer.Context, opt_lang: str):

    opt_engine = ctx.params.get("engine")
    if opt_engine is None or opt_engine not in FUZZING_ENGINES:
        return FUZZER_LANGS

    with AutologinClient() as client:
        configurations = list_fuzzer_configurations(client)

    configurations_dict = configurations.dict(by_alias=True)
    configurations_engines = configurations_dict.values()

    inverted: Dict[str, List[str]] = {}
    for engine in set(itertools.chain(*configurations_engines)):
        inverted[engine] = list()

    for lang, engines in configurations_dict.items():
        for engine in engines:
            if engine in inverted:
                inverted[engine].append(lang)

    langs: List[str] = inverted[opt_engine]
    return list(filter(lambda l: l.startswith(opt_lang), langs))


@wrap_autocompletion_errors
def complete_image_id(incomplete: str):
    with AutologinClient() as client:
        for image in send_list_images(client):
            image_id: str = image["id"]
            if image_id.startswith(incomplete):
                yield image_id, image["name"]


########################################
# Show configurations
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
# Create image
########################################


@app.command(
    name="create",
    help="Create agent docker image (built-in)",
)
def create_builtin_image(
    ctx: typer.Context,
    name: str = typer.Option(
        ...,
        "-n",
        "--name",
        prompt=True,
        prompt_required=False,
        callback=validators.string,
        help="Image name",
    ),
    description: str = typer.Option(
        ...,
        "-d",
        "--description",
        prompt=True,
        prompt_required=False,
        callback=validators.string,
        help="Image description",
    ),
    lang: FuzzerLang = typer.Option(
        ...,
        "-l",
        "--lang",
        prompt=True,
        prompt_required=False,
        autocompletion=get_lang_by_engine,
        metavar=f"[{'|'.join(FUZZER_LANGS)}]",
        help="Target programming language",
    ),
    engine: FuzzingEngine = typer.Option(
        ...,
        "-e",
        "--engine",
        prompt=True,
        prompt_required=False,
        autocompletion=get_engine_by_lang,
        metavar=f"[{'|'.join(FUZZING_ENGINES)}]",
        help="Target fuzzing engine",
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = CreateImageResponseModel

    json_data = {
        "name": name,
        "description": description,
        "engine": engine,
        "lang": lang,
    }

    with AutologinClient() as client:
        response = client.post(URL_IMAGES, json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Image name"),
        ("status", "Status"),
    ]

    output.message("Don't forget to push docker image:", output_mode)
    output.message(f"$ docker push {URL_REGISTRY}/agents/{data.id}", output_mode)
    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# Get image
########################################


@app.command(
    name="get",
    help="Get agent image information by name or id",
)
def get_builtin_image(
    ctx: typer.Context,
    image_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_image_id,
        help="Image name or id",
    ),
):
    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    ResponseModel = GetImageResponseModel

    with AutologinClient() as client:
        response = client.get(f"{URL_IMAGES}/{image_id}")
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = [
        ("id", "ID"),
        ("name", "Image name"),
        ("description", "Description"),
        ("status", "Status"),
        ("engine", "Fuzzing engine"),
        ("lang", "Target language"),
    ]

    output.dict_data(data.display_dict(), columns, output_mode)


########################################
# List images
########################################


@app.command(
    name="list",
    help="List agent docker images",
)
def list_builtin_images(ctx: typer.Context):

    columns = [
        ("id", "ID", 0.1),
        ("name", "Image name", 0.2),
        ("description", "Description", 0.5),
        ("status", "Status", 0.1),
        ("engine", "Engine", 0.1),
        ("lang", "Lang", 0.1),
    ]

    with AutologinClient() as client:
        data = send_list_images(client)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    output.list_data(data, columns, output_mode)


########################################
# Update image
########################################


@app.command(
    name="update",
    help="Update agent image information",
)
def update_builtin_image(
    ctx: typer.Context,
    image_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_image_id,
        help="Image name or id",
    ),
    name: Optional[str] = typer.Option(
        None,
        "-n",
        "--name",
        callback=OptionCallback(
            validation_fn=validators.string,
            required=False,
        ),
        help="New image name",
    ),
    description: Optional[str] = typer.Option(
        None,
        "-d",
        "--description",
        callback=OptionCallback(
            validation_fn=validators.string,
            required=False,
        ),
        help="New image description",
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
        url = f"{URL_IMAGES}/{image_id}"
        response = client.patch(url, json=json_data)
        data: ResponseModel = parse_response(response, ResponseModel)

    columns = {
        "name": "Image name",
        "description": "Description",
    }

    output.diff_data(data.old, data.new, columns, output_mode)


########################################
# Delete image
########################################


@app.command(
    name="delete",
    help="Delete agent image",
)
def delete_builtin_image(
    ctx: typer.Context,
    image_id: str = typer.Argument(
        ...,
        callback=validators.string,
        autocompletion=complete_image_id,
        help="Image name or id",
    ),
):
    app_ctx: AppContext = ctx.obj
    if not app_ctx.auto_approve:
        msg = "Do you really want to delete this image?"
        typer.confirm(msg, abort=True)

    with AutologinClient() as client:
        response = client.delete(f"{URL_IMAGES}/{image_id}")
        parse_response_no_model(response)

    output.message("Don't forget to delete docker image from registry")
    output.success("Image deleted successfully")
