# import shutil
import shutil
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Union

import plotille as plt
import typer
from pydantic import BaseModel, validator

from bondi import output, validators
from bondi.callback import (
    DefaultFuzzerCallback,
    DefaultProjectCallback,
    DefaultRevisionCallback,
    DefaultUserCallback,
)
from bondi.cli.admin.users import complete_user_name
from bondi.cli.user.fuzzers import complete_fuzzer_name, send_get_fuzzer, url_fuzzer
from bondi.cli.user.projects import complete_project_name
from bondi.cli.user.revisions import (
    complete_revision_name,
    get_ids_for_revision_url,
    url_revision,
)
from bondi.client import AutologinClient
from bondi.defaults import load_default_fuzzer, load_default_project, load_default_user
from bondi.errors import BondiError
from bondi.helper import paginate
from bondi.models import AppContext, FuzzerLang, FuzzingEngine, StatisticsGroupBy
from bondi.util import utc_to_local, wrap_autocompletion_errors

########################################
# App
########################################

app = typer.Typer(name="statistics", help="Fuzzer statistics")

########################################
# Endpoints
########################################


def query_statistics(
    group_by: StatisticsGroupBy,
    date_begin: Optional[str],
    date_end: Optional[str],
):
    items = {"group_by": group_by.value}

    if date_begin:
        items.update(date_begin=date_begin)

    if date_end:
        items.update(date_end=date_end)

    return items


def url_statistics_fuzz(fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_fuzzer(fuzzer_id, project_id, user_id)}/statistics"


def url_statistics(revision_id: str, fuzzer_id: str, project_id: str, user_id: str):
    return f"{url_revision(revision_id, fuzzer_id, project_id, user_id)}/statistics"


########################################
# Models
########################################


class GetGrpStatBaseModel(BaseModel):

    """Base class for grouped statistics"""

    date: datetime
    """ Date period """

    unique_crashes: int
    """ Count of unique crashes found during period """

    known_crashes: int
    """ Count of all crashes found during period """

    @classmethod
    def columns(cls):
        return list(cls.__fields__.keys())

    @validator("date")
    def utc_to_local(date: datetime):
        return utc_to_local(date)

    def display_dict(self, group_by: StatisticsGroupBy):

        data = self.dict()
        if group_by in [StatisticsGroupBy.day, StatisticsGroupBy.week]:
            data["date"] = self.date.strftime(r"%a %b %d %Y")
        else:  # group_by == StatisticsGroupBy.month:
            data["date"] = self.date.strftime(r"%b %Y")

        return data


class GetGrpStatLibFuzzerResponseModel(GetGrpStatBaseModel):

    """Grouped statistics for libfuzzer engine"""

    execs_per_sec: int
    """ Average count of executions per second """

    edge_cov: int
    """ Edge coverage """

    feature_cov: int
    """ Feature coverage """

    peak_rss: int
    """ Max RAM usage """

    execs_done: int
    """ Count of fuzzing iterations executed """

    corpus_entries: int
    """ Count of files in merged corpus """

    corpus_size: int
    """ The size of generated corpus in bytes """

    def display_dict(self, group_by: StatisticsGroupBy):
        data = super().display_dict(group_by)
        data["peak_rss"] = round(self.peak_rss / 10**6, 2)
        data["execs_done"] = round(self.execs_done / 10**6, 2)
        data["corpus_size"] = round(self.corpus_size / 10**3)
        return data


class GetGrpStatAflResponseModel(GetGrpStatBaseModel):

    cycles_done: int
    """Queue cycles completed so far"""

    cycles_wo_finds: int
    """Number of cycles without any new paths found"""

    execs_done: int
    """Number of execve() calls attempted"""

    execs_per_sec: float
    """Overall number of execs per second"""

    corpus_count: int
    """Total number of entries in the queue"""

    corpus_favored: int
    """Number of queue entries that are favored"""

    corpus_found: int
    """Number of entries discovered through local fuzzing"""

    corpus_variable: int
    """Number of test cases showing variable behavior"""

    stability: float
    """Percentage of bitmap bytes that behave consistently"""

    bitmap_cvg: float
    """Percentage of edge coverage found in the map so far"""

    slowest_exec_ms: int
    """Real time of the slowest execution in ms"""

    peak_rss_mb: int
    """Max rss usage reached during fuzzing in MB"""

    def display_dict(self, group_by: StatisticsGroupBy):
        data = super().display_dict(group_by)
        data["stability"] = round(self.stability, 2)
        data["bitmap_cvg"] = round(self.bitmap_cvg, 2)
        data["execs_per_sec"] = round(self.execs_per_sec, 2)
        data["execs_done"] = round(self.execs_done / 10**6, 2)
        return data


########################################
# Autocompletion
########################################

STATS_GROUP_BY = [x for x in StatisticsGroupBy]
COLUMNS_LIBFUZZER = GetGrpStatLibFuzzerResponseModel.columns()
COLUMNS_AFL = GetGrpStatAflResponseModel.columns()


@wrap_autocompletion_errors
def get_available_stat_columns(ctx: typer.Context, column: str):

    fuzzer = ctx.params.get("fuzzer") or load_default_fuzzer()
    project = ctx.params.get("project") or load_default_project()
    user = ctx.params.get("user") or load_default_user()

    if not (fuzzer and project):
        raise BondiError("Required parameters not set. Unable to continue")

    with AutologinClient() as client:
        target_fuzzer = send_get_fuzzer(fuzzer, project, user, client)

    if target_fuzzer.engine == FuzzingEngine.libfuzzer:
        columns = COLUMNS_LIBFUZZER
    else:  # fuzzer.engine == FuzzingEngine.afl:
        columns = COLUMNS_AFL

    # Exclude 'date' from autocompletion
    # when drawing charts (no logic)
    if "chart" in ctx.command.name:
        columns.remove("date")

    return list(filter(lambda c: c.startswith(column), columns))


########################################
# Show statistics
########################################


@app.command(
    name="show",
    help="Show fuzzer statistics as a table",
)
def show_statistics(
    ctx: typer.Context,
    custom_column_names: Optional[List[str]] = typer.Option(
        None,
        "--cc",
        "--custom-column",
        autocompletion=get_available_stat_columns,
        help="Column names which will be included in table",
    ),
    group_by: StatisticsGroupBy = typer.Option(
        StatisticsGroupBy.day.value,
        "--group-by",
        autocompletion=lambda: STATS_GROUP_BY,
        metavar=f"[{'|'.join(STATS_GROUP_BY)}]",
        help="Time period to use when grouping statistics",
    ),
    last_days: Optional[int] = typer.Option(
        None,
        "--days",
        "--last-days",
        callback=validators.positive_int,
        help="Retrieve statistics for last N days",
    ),
    date_begin: Optional[datetime] = typer.Option(
        None,
        "--since",
        help="Retrieve all fuzzer statistics since provided date",
    ),
    date_end: Optional[datetime] = typer.Option(
        None,
        "--until",
        help="Retrieve all fuzzer statistics until provided date",
    ),
    revision: str = typer.Option(
        None,
        "-r",
        "--revision",
        callback=DefaultRevisionCallback(),
        autocompletion=complete_revision_name,
        help="Revision id or name",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name",
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
        target_fuzzer = send_get_fuzzer(fuzzer, project, user, client)
        target_engine = target_fuzzer.engine

    def select_custom_columns(columns: List[Tuple[str, str, float]]):

        result = []
        names, _, _ = zip(*columns)

        for cc_name in custom_column_names:
            if cc_name not in names:
                avail_cols = "|".join(names)
                output.error(f"Available columns for {target_engine}: [{avail_cols}]")
                raise typer.Exit(code=1)

            i = names.index(cc_name)
            result.append(columns[i])

        return result

    def select_columns(
        default_cols: List[Tuple[str, str, float]],
        additional_cols: List[Tuple[str, str, float]],
    ):
        if not custom_column_names:
            return default_columns
        else:
            all_cols = [*default_cols, *additional_cols]
            return select_custom_columns(all_cols)

    if target_engine == FuzzingEngine.libfuzzer:

        default_columns = [
            ("date", "Date", 0.2),
            ("edge_cov", "Edge coverage", 0.2),
            ("feature_cov", "Feature coverage", 0.2),
            ("peak_rss", "Peak RSS (MB)", 0.1),
            ("execs_per_sec", "Execs/s", 0.1),
            ("unique_crashes", "Unique crashes", 0.2),
            ("known_crashes", "Known crashes", 0.2),
        ]

        additional_columns = [
            ("execs_done", "Execs done (M)", 0.1),
            ("corpus_entries", "Corpus entries", 0.1),
            ("corpus_size", "Corpus size (Kb)", 0.1),
        ]

        if target_fuzzer.lang == FuzzerLang.rust:
            default_columns.remove("edge_cov")

        columns = select_columns(default_columns, additional_columns)
        ResponseModel = GetGrpStatLibFuzzerResponseModel

    else:  # target_engine == FuzzingEngine.afl:

        default_columns = [
            ("date", "Date", 0.2),
            ("cycles_done", "Cycles done", 0.2),
            ("cycles_wo_finds", "Cycles w/o finds", 0.2),
            ("bitmap_cvg", "Bitmap coverage", 0.1),
            ("execs_per_sec", "Execs/s", 0.1),
            ("peak_rss_mb", "Peak RSS (MB)", 0.1),
            ("stability", "Stability", 0.1),
        ]

        additional_columns = [
            ("execs_done", "Execs done (M)", 0.1),
            ("corpus_count", "Corpus entries", 0.1),
            ("corpus_favored", "Corpus favored", 0.1),
            ("corpus_found", "Corpus found", 0.1),
            ("corpus_variable", "Corpus variable", 0.1),
            ("slowest_exec_ms", "Slowest exec (ms)", 0.1),
        ]

        columns = select_columns(default_columns, additional_columns)
        ResponseModel = GetGrpStatAflResponseModel

    data = []
    with AutologinClient() as client:

        url = url_statistics(
            **get_ids_for_revision_url(
                revision,
                fuzzer,
                project,
                user,
                client,
            ),
        )

        if not date_begin and not date_end and last_days:
            date_begin = datetime.utcnow() - timedelta(days=last_days)

        client.params = query_statistics(
            group_by,
            date_begin,
            date_end,
        )

        stats: ResponseModel
        for stats in paginate(client, url, ResponseModel):
            data.append(stats.display_dict(group_by))

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    msg = f"Statistics for <fuzzer={fuzzer}, revision={revision}>"

    output.message(msg, output_mode)
    output.list_data(data, columns, output_mode)


class StatisticsChart:

    _x_slot_size = 12
    _x_ratio = 0.75
    _y_ratio = 0.7

    def __init__(
        self,
        group_by: StatisticsGroupBy,
        x_label: str = "X",
        y_label: str = "Y",
    ):
        term = shutil.get_terminal_size()
        self._width = int(term.columns * self._x_ratio)
        self._height = int(term.lines * self._y_ratio)
        self._group_by = group_by
        self._x_label = x_label
        self._y_label = y_label

    def _create_figure(
        self,
        x_values: List[datetime],
        y_values: List[Union[float, int]],
    ):

        fig = plt.Figure()
        fig.x_label = self._x_label
        fig.y_label = self._y_label
        fig.width = self._width
        fig.height = self._height

        x_min, y_min = min(x_values), min(y_values)
        x_max, y_max = max(x_values), max(y_values)

        fig.set_x_limits(x_min, x_max)
        fig.set_y_limits(0, None)

        fig.register_label_formatter(datetime, self._get_x_formatter())
        fig.register_label_formatter(float, self._get_y_formatter(y_values[0]))

        #
        # Shrink chart width if len(x_values) is too small
        # ----------|-|---------|---------|--------->
        #           | 05/23/22  05/24/22  05/25/22
        #

        width = self._calc_chart_width(x_values)
        if width < fig.width:
            fig.width = width

        #
        # Shrink chart height if integer range is too small
        #            ^
        #          3 | ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
        #          2 | ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
        #          1 |
        #          0 |
        # -----------|-|------|------>
        #            |
        #

        if isinstance(y_values[0], int):
            delta = (y_max - y_min) or 2
            if delta / fig.height < 1:
                fig.height = delta

        return fig

    def _calc_chart_width(self, x_values: list):
        return round(self._x_ratio * (self._x_slot_size * len(x_values)))

    def draw(
        self,
        x_values: List[datetime],
        y_values: List[Union[float, int]],
        label: Optional[str] = None,
    ):
        assert len(x_values) > 0
        assert len(y_values) > 0

        fig = self._create_figure(x_values, y_values)
        fig.plot(x_values, y_values, lc=plt.color("red"), label=label)
        typer.echo(fig.show(legend=True))

    @staticmethod
    def justify(val: str, chars: int, left: bool = False):
        return val.ljust(chars) if left else val.rjust(chars)

    @classmethod
    def _month_formatter(cls, val: datetime, chars: int, delta, left=False):
        return cls.justify(val.strftime(r"%b %Y"), chars, left)

    @classmethod
    def _day_formatter(cls, val: datetime, chars: int, delta, left=False):
        return cls.justify(val.strftime(r"%m/%d/%y"), chars, left)

    @classmethod
    def _hour_formatter(cls, val: datetime, chars: int, delta, left=False):
        return cls.justify(val.strftime(r"%H:%M:%S"), chars, left)

    @classmethod
    def _float_formatter(cls, val: float, chars: int, delta, left=False):
        return cls.justify(str(round(val, 2)), chars, left)

    @classmethod
    def _int_formatter(cls, val: float, chars: int, delta, left=False):
        return cls.justify(str(int(val)), chars, left)

    def _get_x_formatter(self):
        if self._group_by == StatisticsGroupBy.day:
            return self._day_formatter
        elif self._group_by == StatisticsGroupBy.week:
            return self._day_formatter
        else:  # StatisticsGroupBy.month
            return self._month_formatter

    def _get_y_formatter(self, value: Union[int, float]):
        if isinstance(value, int):
            return self._int_formatter
        else:
            return self._float_formatter


@app.command(
    name="show-chart",
    help="Show fuzzer statistics as a chart",
)
def show_statistics_chart(
    ctx: typer.Context,
    column: str = typer.Option(
        ...,
        "-c",
        "--column",
        autocompletion=get_available_stat_columns,
        callback=validators.string,
        help="Column name by which the statistics chart will be drawn",
    ),
    group_by: StatisticsGroupBy = typer.Option(
        StatisticsGroupBy.day.value,
        "--group-by",
        autocompletion=lambda: STATS_GROUP_BY,
        metavar=f"[{'|'.join(STATS_GROUP_BY)}]",
        help="Time period to use when grouping statistics",
    ),
    last_days: Optional[int] = typer.Option(
        None,
        "--days",
        "--last-days",
        callback=validators.positive_int,
        help="Retrieve statistics for last N days",
    ),
    date_begin: Optional[datetime] = typer.Option(
        None,
        "--since",
        help="Retrieve all fuzzer statistics since provided date",
    ),
    date_end: Optional[datetime] = typer.Option(
        None,
        "--until",
        help="Retrieve all fuzzer statistics until provided date",
    ),
    revision: str = typer.Option(
        None,
        "-r",
        "--revision",
        callback=DefaultRevisionCallback(),
        autocompletion=complete_revision_name,
        help="Revision id or name",
    ),
    fuzzer: str = typer.Option(
        None,
        "-f",
        "--fuzzer",
        callback=DefaultFuzzerCallback(),
        autocompletion=complete_fuzzer_name,
        help="Fuzzer id or name",
    ),
    project: str = typer.Option(
        None,
        "-p",
        "--project",
        callback=DefaultProjectCallback(),
        autocompletion=complete_project_name,
        help="Project id or name",
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
        target_fuzzer = send_get_fuzzer(fuzzer, project, user, client)
        target_engine = target_fuzzer.engine

    x_values = []
    y_values = []

    if target_engine == FuzzingEngine.libfuzzer:

        column_names = {
            "date": "Date",
            "edge_cov": "Edge coverage",
            "feature_cov": "Feature coverage",
            "peak_rss": "Peak RSS (MB)",
            "execs_per_sec": "Execs/s",
            "unique_crashes": "Unique crashes",
            "known_crashes": "Known crashes",
            "execs_done": "Execs done (M)",
            "corpus_entries": "Corpus entries",
            "corpus_size": "Corpus size (Kb)",
        }

        ResponseModel = GetGrpStatLibFuzzerResponseModel
        columns = COLUMNS_LIBFUZZER

    else:  # target_engine == FuzzingEngine.afl:

        column_names = {
            "date": "Date",
            "edge_cov": "Edge coverage",
            "feature_cov": "Feature coverage",
            "peak_rss": "Peak RSS (MB)",
            "execs_per_sec": "Execs/s",
            "unique_crashes": "Unique crashes",
            "known_crashes": "Known crashes",
            "execs_done": "Execs done (M)",
            "corpus_entries": "Corpus entries",
            "corpus_size": "Corpus size (Kb)",
        }

        ResponseModel = GetGrpStatLibFuzzerResponseModel
        columns = COLUMNS_AFL

    if column not in columns:
        avail_cols = "|".join(columns)
        output.error(f"Available columns for {target_engine}: [{avail_cols}]")
        raise typer.Exit(code=1)

    with AutologinClient() as client:

        url = url_statistics(
            **get_ids_for_revision_url(
                revision,
                fuzzer,
                project,
                user,
                client,
            ),
        )

        if not date_begin and not date_end and last_days:
            date_begin = datetime.utcnow() - timedelta(days=last_days)

        client.params = query_statistics(
            group_by,
            date_begin,
            date_end,
        )

        stats: ResponseModel
        for stats in paginate(client, url, ResponseModel):
            y_values.append(stats.display_dict(group_by).get(column, 0))
            x_values.append(stats.date)

    if len(y_values) < 2:
        output.error(f"Not enough values to draw chart")
        raise typer.Exit(code=1)

    app_ctx: AppContext = ctx.obj
    output_mode = app_ctx.output_mode
    msg = f"Statistics for <fuzzer={fuzzer}, revision={revision}>"
    output.message(msg, output_mode)

    chart = StatisticsChart(group_by, "date", column)
    chart.draw(x_values, y_values, column_names[column])
