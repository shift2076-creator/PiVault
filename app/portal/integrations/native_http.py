"""Small private-upstream JSON client. Secrets stay in headers, never URLs/errors."""
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from django.core.exceptions import ValidationError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NativeError(ValidationError):
    def __init__(self, service, status=None, uncertain=False):
        self.service=service
        self.status=status
        self.uncertain=uncertain
        suffix=f' (HTTP {status})' if status else ''
        super().__init__(f'{service} account setup failed{suffix}.')


class JSONClient:
    def __init__(self, service, base_url, headers=None, timeout=10):
        parsed=urlsplit(base_url)
        # Caller configuration is private server configuration, never form data.
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Expected an upstream origin without credentials or query.')
        self.service=service
        self.base_url=base_url.rstrip('/')
        self.headers=dict(headers or {})
        self.timeout=timeout
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def request(self, method, path, data=None):
        if not path.startswith('/') or path.startswith('//') or '\r' in path or '\n' in path:
            raise ValueError('Expected an upstream-relative API path.')
        body=json.dumps(data).encode() if data is not None else None
        request=Request(self.base_url+path,data=body,method=method,
                        headers={**self.headers,'Content-Type':'application/json','Accept':'application/json'})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:
                    raise NativeError(self.service,uncertain=method!='GET')
                return json.loads(raw) if raw else None
        except HTTPError as error:
            # Never include the response body: upstreams may echo submitted fields.
            raise NativeError(self.service,status=error.code,
                              uncertain=method!='GET' and error.code>=500) from None
        except (URLError,OSError,ValueError):
            raise NativeError(self.service,uncertain=method!='GET') from None


@dataclass
class CreatedAccount:
    service: str
    native_id: str
    rollback: object
