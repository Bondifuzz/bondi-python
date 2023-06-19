import os
import sys

import typer

from bondi import output
from bondi.cli import config
from bondi.cli.admin import builtin_images, users
from bondi.cli.user import (
    crashes,
    fuzzers,
    images,
    integrations,
    projects,
    revisions,
    statistics,
)
from bondi.defaults import DEFAULTS_DIR
from bondi.errors import BadParameterError, BondiError, BondiValidationError
from bondi.models import AppContext, OutputMode, Verbosity
from bondi.util import APP_DIR

########################################
# Admin CLI
########################################

app_admin = typer.Typer(
    name="admin",
    help="Bondifuzz admin CLI. Use with caution",
    short_help="Admin CLI",
)

app_admin.add_typer(builtin_images.app)
app_admin.add_typer(users.app)

########################################
# Client CLI
########################################

app = typer.Typer(
    name="bondi",
    help="Bondifuzz command line interface implemented in python",
)

app.add_typer(app_admin)
app.add_typer(images.app)
app.add_typer(projects.app)
app.add_typer(integrations.app)
app.add_typer(fuzzers.app)
app.add_typer(statistics.app)
app.add_typer(revisions.app)
app.add_typer(crashes.app)
app.add_typer(config.app)


@app.callback()
def common_options(
    ctx: typer.Context,
    verbosity: str = typer.Option(
        Verbosity.none.value,
        "-v",
        "--verbosity",
        help="Enable logging output. Usually used for debugging",
        autocompletion=lambda: [e.value for e in Verbosity],
        metavar=f"[{'|'.join([e.value for e in Verbosity])}]",
    ),
    output_mode: str = typer.Option(
        OutputMode.human.value,
        "-o",
        "--output-mode",
        help="Choose an output mode. Human-readable by default",
        autocompletion=lambda: [e.value for e in OutputMode],
        metavar=f"[{'|'.join([e.value for e in OutputMode])}]",
    ),
    silent: bool = typer.Option(
        False,
        "-s",
        "--silent",
        help="Show only result of operation and suppress any other output",
    ),
    auto_approve: bool = typer.Option(
        False,
        "-y",
        "--yes",
        help="Automatic 'yes', if set. No prompts with 'y/n' will be shown",
    ),
    prompt: bool = typer.Option(
        True,
        help="If set, prompts required values interactively. Otherwise, exits with error",
    ),
):
    os.makedirs(APP_DIR, exist_ok=True)
    os.makedirs(DEFAULTS_DIR, exist_ok=True)

    ctx.obj = AppContext(
        verbosity=verbosity,
        output_mode=output_mode,
        auto_approve=auto_approve,
        silent=silent,
        prompt=prompt,
    )


def main():
    try:
        app()
    except BondiValidationError as e:
        output.validation_errors(e)
    except BadParameterError as e:
        output.bad_parameters(e)
        sys.exit(1)
    except BondiError as e:
        output.error(str(e))
        sys.exit(1)
    else:
        sys.exit(0)
