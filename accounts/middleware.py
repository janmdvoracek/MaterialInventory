from django.contrib.auth import get_user
from django.http import Http404
from django.urls import reverse


class AdminSessionRequiredMiddleware:
    """Answer 404 for everything under /admin/ unless the session is an admin's.

    Django's admin serves its own login form to anonymous requests, and on a
    public deployment that is the one page where guessing a password is worth an
    attacker's time — it is the superuser surface, and it sits at the URL every
    scanner tries first. This gate makes the whole prefix invisible to anyone
    not already logged in as an admin, so the app's own /login/, which
    django-axes rate-limits, is the only login form on the internet.

    **A 404 and not a redirect to /login/**, deliberately: an admin who is
    logged out is one visit to the app away from getting in, while a redirect
    would confirm to a scanner that the admin is here. The cost is that /admin/
    typed while logged out looks like a broken URL rather than a prompt; the
    runbook says to expect that.

    **Position in MIDDLEWARE is load-bearing**: it goes *above* CommonMiddleware,
    because APPEND_SLASH turns any 404 for /admin into a 301 to /admin/ —
    handing back exactly the confirmation the 404 withholds — and above
    CsrfViewMiddleware, so an anonymous POST to /admin/login/ is refused as a
    404 rather than a 403. That puts it above AuthenticationMiddleware too, so
    it resolves the user itself; it needs only SessionMiddleware above it.

    Being middleware rather than a per-ModelAdmin check is what covers every
    admin URL at once, /admin/login/ included, which is the whole point.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._prefix = None

    @property
    def prefix(self):
        # Read off the URLconf rather than hardcoded, so mounting the admin
        # somewhere else stays covered. Lazily: reverse() needs the URLconf
        # loaded, which it is not while middleware is being instantiated.
        if self._prefix is None:
            self._prefix = reverse('admin:index')
        return self._prefix

    def __call__(self, request):
        if self._is_admin_path(request.path) and not _may_open_admin(request):
            raise Http404
        return self.get_response(request)

    def _is_admin_path(self, path):
        # The slashless form matters: /admin is where APPEND_SLASH would
        # otherwise redirect from.
        return path.startswith(self.prefix) or path == self.prefix.rstrip('/')


def _may_open_admin(request):
    # request.user only exists below AuthenticationMiddleware, and this sits
    # above it; get_user() is exactly what that middleware's lazy user calls.
    # AnonymousUser has no `has_admin_access`, hence the authentication check.
    user = request.user if hasattr(request, 'user') else get_user(request)
    return user.is_authenticated and user.has_admin_access
