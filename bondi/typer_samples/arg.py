from typing import Optional

import typer


def main(
    name: Optional[str] = typer.Argument(None, help="The name of the user to greet")
):
    if name is None:
        typer.echo("Hello World!")
    else:
        typer.echo(f"Hello {name}")


if __name__ == "__main__":
    typer.run(main)

# import typer


# def main(name: str = typer.Argument("World", envvar="AWESOME_NAME", show_envvar=False)):
#     typer.echo(f"Hello Mr. {name}")


# if __name__ == "__main__":
#     typer.run(main)
