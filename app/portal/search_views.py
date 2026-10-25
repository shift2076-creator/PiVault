"""Unified search page and the launcher's list of searchable apps."""
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET
from . import search


def _term(request):
    return ' '.join(request.GET.get('q', '').split())[:200]


@require_GET
def apps(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return JsonResponse({'error': 'Sign in required.'}, status=401)
    return JsonResponse({'apps': [{'key': key, 'label': label}
                                  for key, label in search.available(request.user)]})


@require_GET
def results(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return redirect('/users/sign-in')
    term = _term(request)
    allowed = search.available(request.user)
    # No app parameter means every app the user can search (a bare /search?q= link).
    wanted = set(request.GET.getlist('app')) or {key for key, _ in allowed}
    sections = search.run(request.user, term, wanted) if len(term) >= 2 else []
    return render(request, 'search.html', {
        'term': term, 'too_short': 0 < len(term) < 2, 'sections': sections,
        'apps': [{'key': key, 'label': label, 'checked': key in wanted} for key, label in allowed],
        'count': sum(len(s['results']) for s in sections),
    })
