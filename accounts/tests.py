from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from .decorators import role_required
from .models import User


class UserRoleTests(TestCase):
    def test_default_role_is_worker(self):
        user = User.objects.create_user(username='alice', password='pw')
        self.assertEqual(user.role, User.Role.WORKER)

    def test_is_manager_or_admin_property(self):
        worker = User.objects.create_user(username='w', password='pw', role=User.Role.WORKER)
        manager = User.objects.create_user(username='m', password='pw', role=User.Role.MANAGER)
        admin = User.objects.create_user(username='a', password='pw', role=User.Role.ADMIN)
        self.assertFalse(worker.is_manager_or_admin)
        self.assertTrue(manager.is_manager_or_admin)
        self.assertTrue(admin.is_manager_or_admin)


@role_required(User.Role.MANAGER, User.Role.ADMIN)
def _dummy_view(request):
    return HttpResponse('ok')


class RoleRequiredDecoratorTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_redirects_anonymous_to_login(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.get('/dummy/')
        request.user = AnonymousUser()
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_forbids_wrong_role(self):
        request = self.factory.get('/dummy/')
        request.user = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER)
        with self.assertRaises(PermissionDenied):
            _dummy_view(request)

    def test_allows_matching_role(self):
        request = self.factory.get('/dummy/')
        request.user = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER)
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 200)
