from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .decorators import role_required
from .models import User


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='worker', password='pw')

    def test_logout_link_in_page_posts_rather_than_gets(self):
        # Django's LogoutView rejects GET, so the header control has to be a
        # POST form; a plain <a href> would 405 and nobody could log out.
        self.client.force_login(self.user)
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn(f'action="{reverse("logout")}"', html)
        self.assertIn('method="post"', html)

    def test_logout_via_post_succeeds(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)


class AdminLinkTests(TestCase):
    def test_admin_link_shown_to_staff_user(self):
        user = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER, is_staff=True)
        self.client.force_login(user)
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn(reverse('admin:index'), html)
        self.assertIn('Administrace', html)

    def test_admin_link_hidden_from_non_staff_user(self):
        user = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER, is_staff=False)
        self.client.force_login(user)
        html = self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn('Administrace', html)


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

    def test_superuser_counts_as_manager_or_admin_despite_worker_role(self):
        # createsuperuser never sets a role, so it falls back to WORKER.
        superuser = User.objects.create_superuser(username='root', password='pw')
        self.assertEqual(superuser.role, User.Role.WORKER)
        self.assertTrue(superuser.is_manager_or_admin)


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

    def test_allows_superuser_despite_worker_role(self):
        request = self.factory.get('/dummy/')
        request.user = User.objects.create_superuser(username='root', password='pw')
        self.assertEqual(request.user.role, User.Role.WORKER)
        response = _dummy_view(request)
        self.assertEqual(response.status_code, 200)
