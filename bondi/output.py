import json
import shutil
import textwrap
from typing import Any, Dict, List, Tuple

import typer
from pydantic import BaseModel
from pydantic.error_wrappers import display_errors
from tabulate import tabulate

from bondi.util import get_cli_opts

from .errors import BadParameterError, BondiValidationError
from .models import OutputMode

STATUS_OK = "OK"
STATUS_FAILED = "FAILED"
TERMINAL_WIDTH = shutil.get_terminal_size().columns


def success(message: str):
    typer.echo(f"{STATUS_OK} - {message}")


def error(message: str):
    typer.echo(f"{STATUS_FAILED} - {message}", err=True)


def message(message: str, mode: OutputMode):
    if mode == OutputMode.human:
        typer.echo(message)


def result(value: str):
    typer.echo(value)


def validation_errors(e: BondiValidationError):
    typer.echo(f"{STATUS_FAILED} - {e}\n{display_errors(e.errors)}", err=True)


def bad_parameters(e: BadParameterError):
    opts = ", ".join([get_cli_opts(e.ctx, param) for param in e.params])
    typer.echo(f"{STATUS_FAILED} - {e}. See help for {opts}", err=True)


def dt_plain(num_columns: int):

    """
    Each column: 2 * num_columns
    Borders: 0

    ---------------  ------------------------
    id               8174545
    name             my_revision
    status           Running
    ---------------  ------------------------
    """

    return 2 * (num_columns - 1)


def dt_psql(num_columns: int):

    """
    Each column: 3 * (num_columns - 1)
    Borders: 2 * 2

    +---------+-------------------+---------------+----------+----------+
    | id      | name              | description   | type     | status   |
    |---------+-------------------+---------------+----------+----------|
    | 2551227 | LibFuzzer for Cpp | Test fuzzer   | Built-in | Ready    |
    +---------+-------------------+---------------+----------+----------+
    """

    return 3 * (num_columns - 1) + 2 * 2


def _get_wrapped_size(size: int, ratio: float):
    return int(size * ratio) or 1


def wrap_text(text: str, ratio: float, size: int):
    return textwrap.fill(text, _get_wrapped_size(size, ratio))


def wrap_headers(headers: List[Tuple[str, float]], size: int):

    result = []
    size -= 2 * len(headers)  # heuristics for headers

    for title, ratio in headers:
        result.append(wrap_text(title, ratio, size))

    return result


def _diff_data_human(
    old: Dict[str, str],
    new: Dict[str, str],
    columns: Dict[str, str],
):
    name_ratio = 0.2  # 20% of terminal working space
    val_ratio = 0.4  # 2 * 40% of terminal working space

    headers = [
        ("Property name", name_ratio),
        ("Old value", val_ratio),
        ("New value", val_ratio),
    ]

    size = TERMINAL_WIDTH - dt_psql(len(headers))
    headers = wrap_headers(headers, size)

    def wrap_diff(name: str, old: Any, new: Any):
        return (
            wrap_text(columns.get(name, name), name_ratio, size),
            wrap_text(str(old), val_ratio, size),
            wrap_text(str(new), val_ratio, size),
        )

    props_diff = zip(old.keys(), old.values(), new.values())
    values = [wrap_diff(k, v1, v2) for k, v1, v2 in props_diff]
    typer.echo(tabulate(values, headers, tablefmt="psql"))


def _diff_data_json(old: Dict[str, str], new: Dict[str, str]):
    typer.echo(json.dumps({"old": old, "new": new}))


def diff_data(
    old: Dict[str, str],
    new: Dict[str, str],
    columns: Dict[str, str],
    mode: OutputMode,
):
    if mode == OutputMode.human:
        _diff_data_human(old, new, columns)
    else:
        _diff_data_json(old, new)


def _list_data_human(
    list_of_data: List[Dict[str, str]],
    columns: List[Tuple[str, str, float]],
):

    if not list_of_data:
        success("No records. Empty")
        return

    names, titles, ratios = zip(*columns)
    size = TERMINAL_WIDTH - dt_psql(len(titles))

    rows = []
    for data in list_of_data:
        rows.append(
            [
                wrap_text(str(data.get(name, name)), ratio, size)
                for name, ratio in zip(names, ratios)
            ]
        )

    headers = wrap_headers(list(zip(titles, ratios)), size)
    typer.echo(tabulate(rows, headers=headers, tablefmt="psql"))


def _default(obj):
    if isinstance(obj, BaseModel):
        return obj.dict()
    raise TypeError()


def _dump_json(data: dict):
    typer.echo(json.dumps(data, default=_default))


def list_data(
    data: List[Dict[str, str]],
    columns: List[Tuple[str, str, float]],
    mode: OutputMode,
):

    if mode == OutputMode.human:
        _list_data_human(data, columns)
    else:
        _dump_json(data)


def _dict_data_human(
    data: Dict[str, str],
    columns: List[Tuple[str, str]],
    key_ratio: float = 0.2,
    val_ratio: float = 0.8,
):
    rows = []
    size = TERMINAL_WIDTH - dt_plain(2)

    for name, display_name in columns:
        key = wrap_text(display_name, key_ratio, size)
        val = wrap_text(str(data.get(name, name)), val_ratio, size)
        rows.append([key, val])

    typer.echo(tabulate(rows, tablefmt="simple"))


def dict_data(
    data: dict,
    columns: List[Tuple[str, str]],
    mode: OutputMode,
):

    if mode == OutputMode.human:
        _dict_data_human(data, columns)
    else:
        _dump_json(data)
