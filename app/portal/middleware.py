import time

REFRESHED_KEY = '_pivault_refreshed'
REFRESH_EVERY = 30 * 24 * 60 * 60  # Well inside the browsers' 400-day cookie cap.


class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            now = int(time.time())
            if now - request.session.get(REFRESHED_KEY, 0) >= REFRESH_EVERY:
                # Marks the session modified, so SessionMiddleware saves it with a fresh
                # SESSION_COOKIE_AGE and re-sends the cookie, restarting the browser's clock.
                request.session[REFRESHED_KEY] = now
                request.session.set_expiry(None)
        response['Cache-Control'] = 'no-store, private'
        response['X-Frame-Options'] = 'DENY'
        response['Content-Security-Policy'] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        return response
