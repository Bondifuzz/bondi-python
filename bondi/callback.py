from typing import Any, Callable, Optional

from click import MissingParameter
from typer import Context
from typer.core import TyperOption
from typer.main import get_click_param
from typer.utils import get_params_from_function

from bondi import validators
from bondi.defaults import (
    load_default_fuzzer,
    load_default_project,
    load_default_revision,
    load_default_user,
)

from .models import AppContext
from .util import beautify


class OptionCallback:

    """
    Implements advanced callable which handles
    --prompt/--no-prompt options and provides
    two callbacks: validation and default value loader
    """

    # Make type annotations the same as
    # those in __call__ method

    __annotations__ = {
        "ctx": Context,
        "param": TyperOption,
        "value": Optional[str],
    }

    def __init__(
        self,
        validation_fn: Optional[Callable[..., Any]] = None,
        default_val_fn: Optional[Callable[[], Any]] = None,
        required: bool = True,
    ) -> None:

        self.validation_fn = validation_fn
        self.default_val_fn = default_val_fn
        self.required = required

    def invoke(
        self,
        ctx: Context,
        param: TyperOption,
        value: Optional[str],
    ):
        if ctx.resilient_parsing:
            return value

        app_ctx: AppContext = ctx.obj
        prompt_required = app_ctx.prompt

        #
        # If value is not set, try to use
        # default value function to load it
        #

        if value is None:
            if self.default_val_fn:
                value = self.default_val_fn()

        #
        # If value is not set, prompt it, if allowed
        #

        if value is None:
            if self.required and prompt_required:
                param_name: str = param.name
                param.prompt = beautify(param_name)
                value = param.prompt_for_value(ctx)

        #
        # If value is still not set
        # and required, raise exception
        #

        if value is None and self.required:
            raise MissingParameter(param=param)

        #
        # Perform validation, if needed
        #

        if self.validation_fn:
            value = self.validation_fn(value)

        return param.type_cast_value(ctx, value)

    def __call__(
        self,
        ctx: Context,
        param: TyperOption,
        value: Optional[str],
    ):
        return self.invoke(ctx, param, value)


class StringCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(validators.string, None, required)


class PositiveIntCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(validators.positive_int, None, required)


class UrlCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(validators.url, None, required)


class EmailCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(validators.email, None, required)


class DefaultUserCallback(OptionCallback):
    def __init__(self, required: bool = False) -> None:
        super().__init__(
            validation_fn=validators.string,
            default_val_fn=load_default_user,
            required=required,
        )


class DefaultProjectCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(
            validation_fn=validators.string,
            default_val_fn=load_default_project,
            required=required,
        )


class DefaultFuzzerCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(
            validation_fn=validators.string,
            default_val_fn=load_default_fuzzer,
            required=required,
        )


class DefaultRevisionCallback(OptionCallback):
    def __init__(self, required: bool = True) -> None:
        super().__init__(
            validation_fn=validators.string,
            default_val_fn=load_default_revision,
            required=required,
        )


class CallbackInvoker:

    """
    Allows to invoke an OptionCallback
    for parameter in the command handler function.
    It's useful, when one option depends on the other option,
    e.g. '-t type1 -p1 type1-param | -t type2 -p2 type2-param'
    """

    def __init__(self, ctx: Context) -> None:
        self.params = get_params_from_function(ctx.command.callback)
        self.ctx = ctx

    def invoke_callback_for_option(
        self,
        option_name: str,
        callback: OptionCallback,
    ):
        param, _ = get_click_param(self.params[option_name])
        assert isinstance(param, TyperOption)

        default = param.get_default(self.ctx)
        return callback.invoke(self.ctx, param, default)
