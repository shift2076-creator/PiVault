class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        # Authenticated sessions have no inactivity countdown.
        if request.user.is_authenticated:
            from datetime import datetime, timezone
            request.session.set_expiry(datetime.max.replace(tzinfo=timezone.utc))
        response['Cache-Control'] = 'no-store, private'
        response['X-Frame-Options'] = 'DENY'
        response['Content-Security-Policy'] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        return response
