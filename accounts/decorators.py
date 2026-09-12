from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import User

# Reviewing jobs and reading the depot-wide reports. Gated on the view, not just
# by hiding the nav entry — a hidden link is not access control.
REVIEWER_ROLES = (User.Role.MANAGER, User.Role.ADMIN)


def role_required(*roles):
    """Restrict a view to authenticated users whose role is in `roles`.

    Anonymous users get the normal login redirect; authenticated users with
    the wrong role get a 403 rather than being bounced back to login.
    """

    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def _wrapped(request, *args, **kwargs):
            # Superusers bypass the role check: `createsuperuser` never sets a role,
            # so they'd otherwise default to WORKER and be locked out of their own app.
            if not request.user.is_superuser and request.user.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
