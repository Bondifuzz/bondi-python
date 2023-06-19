import os
from typing import Optional

from email_validator import EmailNotValidError, validate_email
from pydantic import AnyHttpUrl, ValidationError, parse_obj_as
from typer import BadParameter


def string(value: Optional[str]):

    if value is None:
        return value

    value = value.strip()
    if len(value) > 0:
        return value

    raise BadParameter("Empty string not allowed")


def email(value: Optional[str]):

    if value is not None:
        try:
            value = value.strip()
            validate_email(value)
        except EmailNotValidError as e:
            msg = "Provided string is not a valid email"
            raise BadParameter(msg) from e

    return value


def url(value: Optional[str]):

    if value is not None:
        try:
            value = value.strip()
            parse_obj_as(AnyHttpUrl, value)
        except ValidationError as e:
            msg = "Provided string is not a valid url"
            raise BadParameter(msg) from e

    return value


def file_read(value: str):

    value = value.strip()
    if value.lower() != "n":
        if not os.path.isfile(value):
            raise BadParameter("No such file")

    return value


def file_read_required(value: str):

    value = value.strip()
    if not os.path.isfile(value):
        raise BadParameter("No such file")

    return value


def positive_int(value: Optional[int]):

    if value is None:
        return None

    if value <= 0:
        raise BadParameter("Positive integer required")

    return value
