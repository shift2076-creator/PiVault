"""Private process/readiness probe; this is not a new public route."""
from urllib.request import ProxyHandler, Request, build_opener

request = Request('http://127.0.0.1:8000/users/sign-in', headers={
    'Host': '192.168.4.90', 'X-Forwarded-Proto': 'https',
})
with build_opener(ProxyHandler({})).open(request, timeout=4) as response:
    if response.status != 200 or b'PiVault' not in response.read(65536):
        raise SystemExit(1)
