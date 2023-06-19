import os
from contextlib import suppress

from .util import APP_DIR

DEFAULTS_DIR = os.path.join(APP_DIR, "defaults")
DEFAULT_USER_PATH = os.path.join(DEFAULTS_DIR, "user.txt")
DEFAULT_PROJECT_PATH = os.path.join(DEFAULTS_DIR, "project.txt")
DEFAULT_FUZZER_PATH = os.path.join(DEFAULTS_DIR, "fuzzer.txt")
DEFAULT_REVISION_PATH = os.path.join(DEFAULTS_DIR, "revision.txt")


def read_file(filepath: str):

    content = None
    with suppress(FileNotFoundError):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

    return content


def write_file(filepath: str, data: str):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(data)


def remove_file(filepath: str):
    with suppress(FileNotFoundError):
        os.remove(filepath)


def load_default_user():
    return read_file(DEFAULT_USER_PATH)


def load_default_project():
    return read_file(DEFAULT_PROJECT_PATH)


def load_default_fuzzer():
    return read_file(DEFAULT_FUZZER_PATH)


def load_default_revision():
    return read_file(DEFAULT_REVISION_PATH)


def save_default_user(id_string: str):
    write_file(DEFAULT_USER_PATH, id_string)


def save_default_project(id_string: str):
    write_file(DEFAULT_PROJECT_PATH, id_string)


def save_default_fuzzer(id_string: str):
    write_file(DEFAULT_FUZZER_PATH, id_string)


def save_default_revision(id_string: str):
    write_file(DEFAULT_REVISION_PATH, id_string)


def remove_default_user():
    remove_file(DEFAULT_USER_PATH)
    remove_default_project()


def remove_default_project():
    remove_file(DEFAULT_PROJECT_PATH)
    remove_default_fuzzer()


def remove_default_fuzzer():
    remove_file(DEFAULT_FUZZER_PATH)
    remove_default_revision()


def remove_default_revision():
    remove_file(DEFAULT_REVISION_PATH)
