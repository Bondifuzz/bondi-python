from __future__ import annotations

import os
from typing import IO, TYPE_CHECKING, List, Optional, Type

import typer
from httpx import Response
from pydantic import BaseModel, ValidationError

from bondi.output import OutputMode

from .errors import APIError, BondiError, InternalError, ServerSideValidationError

if TYPE_CHECKING:
    from .client import AutologinClient


class ErrorModel(BaseModel):
    code: str
    message: str


class PydanticError(BaseModel):
    loc: List[str]
    msg: str
    type: str


class PydanticErrorList(BaseModel):
    detail: List[PydanticError]


_STATUS_CODES = [200, 201, 202, 204]


def parse_validation_error_and_raise(json_data: dict):

    try:
        errors = PydanticErrorList.parse_obj(json_data).dict()
    except ValidationError as e:
        raise InternalError() from e  # TODO: logger.debug

    raise ServerSideValidationError(errors["detail"])


def parse_error_and_raise(json_data: dict):

    try:
        error = ErrorModel.parse_obj(json_data["error"])

    except (KeyError, ValidationError):
        parse_validation_error_and_raise(json_data)

    raise APIError(error.code, error.message)


def parse_stream_response(response: Response):

    if response.status_code in _STATUS_CODES:
        return response.iter_bytes()

    try:
        json_data = response.json()
        if not isinstance(json_data, dict):
            raise ValueError()

    except ValueError as e:
        raise InternalError() from e  # TODO: logger.debug

    parse_error_and_raise(json_data)


def parse_response(
    response: Response,
    model: Type[BaseModel],
    grab_result: bool = True,
):
    try:
        json_data = response.json()
        if not isinstance(json_data, dict):
            raise ValueError()

        if response.status_code not in _STATUS_CODES:
            parse_error_and_raise(json_data)

        if grab_result:
            data = model.parse_obj(json_data["result"])
        else:
            data = model.parse_obj(json_data)

    except ValidationError as e:
        raise InternalError() from e  # TODO: logger.debug

    except ValueError as e:
        raise InternalError() from e  # TODO: logger.debug

    except KeyError as e:
        raise InternalError() from e  # TODO: logger.debug

    return data


def parse_response_no_model(response: Response):
    return parse_response(response, BaseModel, False)


def paginate(client: AutologinClient, url: str, model: Type[BaseModel]):

    try:
        pg_num = 0
        while True:

            # Fetch next page
            response = client.get(url, params={"pg_num": pg_num})

            # Ensure no errors occurred
            json_data = response.json()
            if response.status_code not in _STATUS_CODES:
                parse_error_and_raise(json_data)

            # Get page items
            items = json_data["result"]["items"]
            pg_size = int(json_data["result"]["pg_size"])

            # Items must be a list
            if not isinstance(items, list):
                raise ValueError("'result.items' is not a list")

            # No items in page -> exit
            if not items:
                break

            # Yield parsed item
            for item in items:
                yield model.parse_obj(item)

            # Page not full -> next page will be empty
            if len(items) < pg_size:
                break

            pg_num += 1

    except ValidationError as e:
        raise InternalError() from e  # TODO: logger.debug

    except ValueError as e:
        raise InternalError() from e  # TODO: logger.debug

    except KeyError as e:
        raise InternalError() from e  # TODO: logger.debug


def upload_progressbar(label: str, file: IO, length: Optional[int] = None):
    with typer.progressbar(file, length, label) as progress:
        for value in progress:
            yield value


def download_progressbar(label: str, file: IO, length: Optional[int] = None):
    with typer.progressbar(file, length, label) as progress:
        for value in progress:
            yield value


def download_file(
    label: str,
    url: str,
    filepath: str,
    client: AutologinClient,
    output_mode: OutputMode,
):
    response = client.get(url)
    stream = parse_stream_response(response)

    try:
        file_size = int(response.headers["Content-Length"])
    except (KeyError, ValueError):
        file_size = None

    def streaming_download(file: IO):
        if output_mode == OutputMode.human:
            for chunk in download_progressbar(label, file, file_size):
                yield chunk
        else:
            for chunk in file:
                yield chunk

    try:
        with open(filepath, "wb") as f:
            for chunk in streaming_download(stream):
                f.write(chunk)

    except OSError as e:
        msg = f"Failed to open file for writing: '{filepath}'"
        raise BondiError(msg) from e


def upload_file(
    label: str,
    url: str,
    filepath: str,
    client: AutologinClient,
    output_mode: OutputMode,
):
    def streaming_upload(file: IO, length: int):
        if output_mode == OutputMode.human:
            for chunk in upload_progressbar(label, file, length):
                yield chunk
        else:
            for chunk in file:
                yield chunk

    def get_file_size(file: IO):
        file.seek(0, os.SEEK_END)
        file_size = f.tell()
        file.seek(0, os.SEEK_SET)
        return file_size

    try:
        with open(filepath, "rb") as f:
            file_size = get_file_size(f)
            chunks = streaming_upload(f, file_size)
            headers = {"Content-Length": str(file_size)}
            response = client.put(url, data=chunks, headers=headers)
            parse_response_no_model(response)

    except FileNotFoundError as e:
        msg = f"File does not exsit: '{filepath}'"
        raise BondiError(msg) from e

    except OSError as e:
        msg = f"Failed to open file for reading: '{filepath}'"
        raise BondiError(msg) from e
