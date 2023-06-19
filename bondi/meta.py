from typing import List

from pydantic import BaseModel, Field

from bondi.client import AutologinClient
from bondi.helper import parse_response
from bondi.models import FuzzerLang

URL_META = "/api/v1/meta"


class ResponseListConfigurationsOk(BaseModel):
    cpp: List[str] = Field(alias=FuzzerLang.cpp.value)
    go: List[str] = Field(alias=FuzzerLang.go.value)
    rust: List[str] = Field(alias=FuzzerLang.rust.value)
    python: List[str] = Field(alias=FuzzerLang.python.value)

    def display_dict(self):
        return {k: ", ".join(v) for k, v in self.dict().items()}


ResponseModel = ResponseListConfigurationsOk


def list_fuzzer_configurations(client: AutologinClient) -> ResponseModel:
    response = client.get(f"{URL_META}/fuzzers/configurations")
    return parse_response(response, ResponseModel)
