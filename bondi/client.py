import os
import platform
import threading
from contextlib import contextmanager
from datetime import datetime
from threading import Condition
from typing import Optional

from httpx import USE_CLIENT_DEFAULT, Client, Cookies, Request, Response, TransportError
from pydantic import BaseModel

from .errors import *
from .helper import parse_response
from .util import (
    AuthConfig,
    LoginResult,
    load_auth_config,
    load_login_result,
    save_login_result,
)


class LoginResultModel(BaseModel):
    user_id: str
    user_name: str
    display_name: str
    is_admin: bool


COOKIE_USER_ID = "USER_ID"
COOKIE_SESSION_ID = "SESSION_ID"


class AutologinClient(Client):

    """Makes requests with lazy authentication"""

    _username: str
    _password: str
    _login_result: Optional[LoginResult]
    _auth_tid: Optional[int]
    _cond_var: Condition

    def __init__(self):

        try:
            config = load_auth_config()
        except ConfigNotFoundError as e:
            self._load_from_env()

        try:
            login_result = load_login_result()
            cookies = {
                COOKIE_USER_ID: login_result.user_id,
                COOKIE_SESSION_ID: login_result.session_id,
            }

        except (ConfigNotFoundError, ConfigLoadError):
            login_result = None
            cookies = None

        super().__init__(base_url=config.url, cookies=cookies)
        self._username = config.username
        self._password = config.password
        self._login_result = login_result
        self._cond_var = Condition()
        self._auth_tid = None

    @property
    def login_result(self):
        return self._login_result

    def _check_auth(self):
        if not self._login_result:
            with self._cond_var:
                with self._auth_in_progress():
                    self._do_auth_save_login_result()

    def __enter__(self):
        super().__enter__()
        self._check_auth()
        return self

    def _load_from_env(self):

        try:
            config = AuthConfig(
                url=os.environ["BONDI_SERVER_URL"],
                username=os.environ["BONDI_USERNAME"],
                password=os.environ["BONDI_PASSWORD"],
            )

        except KeyError as e:
            raise ConfigNotFoundError() from e

        return config

    def _get_session_metadata(self):

        meta = {
            "host": platform.node(),
            "date": datetime.now().strftime("%c"),
            "system": platform.system(),
            "release": platform.release(),
        }

        return ", ".join([f"{k}={v}" for k, v in meta.items()])

    def _do_auth_save_login_result(self):

        credentials = {
            "username": self._username,
            "password": self._password,
            "session_metadata": self._get_session_metadata(),
        }

        self._login_result = None
        response = self.post("/api/v1/auth/login", json=credentials)
        if response.status_code == 401:  # Unauthorized
            raise AuthError()

        try:
            Model = LoginResultModel
            parsed: Model = parse_response(response, Model)

            self._login_result = LoginResult(
                user_id=response.cookies[COOKIE_USER_ID],
                session_id=response.cookies[COOKIE_SESSION_ID],
                display_name=parsed.display_name,
                user_name=parsed.user_name,
                is_admin=parsed.is_admin,
            )

        except KeyError as e:
            raise AuthError() from e

        save_login_result(self._login_result)

    @contextmanager
    def _auth_in_progress(self):
        self._auth_tid = threading.get_ident()
        yield
        self._auth_tid = None
        self._cond_var.notify_all()

    def _is_thread_authenticating(self):
        return self._auth_tid == threading.get_ident()

    def _is_auth_in_progress(self):
        return self._auth_tid is not None

    def _send(
        self,
        request: Request,
        *,
        stream=False,
        auth=USE_CLIENT_DEFAULT,
        follow_redirects=USE_CLIENT_DEFAULT,
    ) -> Response:

        #
        # Send request and check response code
        # is not 401 which means authorization required
        #

        response = super().send(
            request=request,
            follow_redirects=follow_redirects,
            stream=stream,
            auth=auth,
        )

        if response.status_code != 401:  # Unauthorized
            return response

        #
        # Check if authentication is being performed in this thread.
        # If so, return response immediately to avoid infinite recursion
        # caused by: send -> 401 -> do_login -> post -> request -> send
        #

        with self._cond_var:
            if self._is_thread_authenticating():
                return response

        #
        # Check if authentication is being performed now.
        # If so, current thread must wait for successful login from another
        # thread and then retry failed request. Otherwise, the current thread
        # must perform an authentication and then retry failed request
        #

        with self._cond_var:
            if self._is_auth_in_progress():
                self._cond_var.wait()
            else:
                with self._auth_in_progress():
                    self._do_auth_save_login_result()

        #
        # After login/waiting each thread must ensure the login result
        # is present. Finally, thread can retry the failed request
        #

        if not self.cookies:
            raise InternalError()  # TODO: logger.debug

        #
        # Add obtained cookies to request and send it
        #

        Cookies(self.cookies).set_cookie_header(request)

        return super().send(
            request=request,
            follow_redirects=follow_redirects,
            stream=stream,
            auth=auth,
        )

    def send(
        self,
        request: Request,
        *,
        stream=False,
        auth=USE_CLIENT_DEFAULT,
        follow_redirects=USE_CLIENT_DEFAULT,
    ) -> Response:

        try:
            res = self._send(
                request=request,
                stream=stream,
                follow_redirects=follow_redirects,
                auth=auth,
            )
        except TransportError as e:
            raise ConnectionError(request.url) from e

        return res
