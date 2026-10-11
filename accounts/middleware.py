from django.contrib.auth import get_user
from django.http import Http404
from django.urls import reverse


class AdminSessionRequiredMiddleware:
    """Answer 404 for everything under /admin/, login included, unless the session is an admin's.

    The server is public, so the admin login form isn't served at all; the
    app's rate-limited /login/ is the only one. A 404 rather than a redirect,
    so a scanner can't tell the admin exists.

    It must sit above CommonMiddleware (APPEND_SLASH would 301 /admin) and
    CsrfViewMiddleware (an anonymous POST should 404, not 403). That puts it
    above AuthenticationMiddleware, so it resolves the user itself.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._prefix = None

    @property
    def prefix(self):
        # From the URLconf, so a moved admin stays covered. Lazy: the URLconf
        # isn't loaded yet when middleware is instantiated.
        if self._prefix is None:
            self._prefix = reverse('admin:index')
        return self._prefix

    def __call__(self, request):
        if self._is_admin_path(request.path) and not _may_open_admin(request):
            raise Http404
        return self.get_response(request)

    def _is_admin_path(self, path):
        # Include the slashless /admin, which APPEND_SLASH would redirect.
        return path.startswith(self.prefix) or path == self.prefix.rstrip('/')


def _may_open_admin(request):
    # This runs above AuthenticationMiddleware, so call get_user() as it would.
    # AnonymousUser has no `has_admin_access`.
    user = request.user if hasattr(request, 'user') else get_user(request)
    return user.is_authenticated and user.has_admin_access
