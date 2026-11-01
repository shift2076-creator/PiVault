class PrivateResponseMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response['Cache-Control'] = 'no-store, private'
        response['X-Frame-Options'] = 'DENY'
        # A view may set its own policy (the Android devices page allows its local sync service); this is the default.
        response.setdefault('Content-Security-Policy', (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        ))
        return response
