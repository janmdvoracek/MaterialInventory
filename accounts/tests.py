from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from materials.models import Material

from .decorators import role_required
from .models import User


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='worker', password='pw')

    def test_logout_link_in_page_posts_rather_than_gets(self):
        # Django's LogoutView rejects GET, so the header control has to be a
        # POST form; a plain <a href> would 405 and nobody could log out.
        self.client.force_login(self.user)
        html = self.client.get(reverse('transform_create')).content.decode()
        self.assertIn(f'action="{reverse("logout")}"', html)
        self.assertIn('method="post"', html)

    def test_logout_via_post_succeeds(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='worker', password='old-password-123')

    def test_password_change_link_shown_to_any_authenticated_user(self):
        # Not just staff -- every worker needs a way to change their own password
        # without admin access.
        self.client.force_login(self.user)
        html = self.client.get(reverse('transform_create')).content.decode()
        self.assertIn(reverse('password_change'), html)
        self.assertIn('Změnit heslo', html)

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse('password_change'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_successful_password_change(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('password_change'),
            {
                'old_password': 'old-password-123',
                'new_password1': 'brand-new-password-456',
                'new_password2': 'brand-new-password-456',
            },
        )
        self.assertRedirects(response, reverse('password_change_done'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brand-new-password-456'))

    def test_wrong_old_password_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('password_change'),
            {
                'old_password': 'not-the-real-password',
                'new_password1': 'brand-new-password-456',
                'new_password2': 'brand-new-password-456',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-password-123'))

    def test_mismatched_new_passwords_rejected(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('password_change'),
            {
                'old_password': 'old-password-123',
                'new_password1': 'brand-new-password-456',
                'new_password2': 'does-not-match',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-password-123'))

    def test_password_change_does_not_log_user_out(self):
        # Django's update_session_auth_hash must be in play, or changing your
        # own password would kick you out mid-session.
        self.client.force_login(self.user)
        self.client.post(
            reverse('password_change'),
            {
                'old_password': 'old-password-123',
                'new_password1': 'brand-new-password-456',
                'new_password2': 'brand-new-password-456',
            },
        )
        response = self.client.get(reverse('transform_create'))
        self.assertEqual(response.status_code, 200)


class AdminLinkTests(TestCase):
    def test_admin_link_shown_to_staff_user(self):
        user = User.objects.create_user(username='manager', password='pw', role=User.Role.MANAGER, is_staff=True)
        self.client.force_login(user)
        html = self.client.get(reverse('transform_create')).content.decode()
        self.assertIn(reverse('admin:index'), html)
        self.assertIn('Administrace', html)

    def test_admin_link_hidden_from_non_staff_user(self):
        user = User.objects.create_user(username='worker', password='pw', role=User.Role.WORKER, is_staff=False)
        self.client.force_login(user)
        html = self.client.get(reverse('transform_create')).content.decode()
        self.assertNotIn('Administrace', html)


class AdminCzechTests(TestCase):
    """The admin is Czech from three separate mechanisms, each of which fails
    silently back to English: app `verbose_name`s, model/field `verbose_name`s,
    and the `LOCALE_PATHS` catalog that patches strings Django itself has not
    translated. The catalog is the fragile one — it is read from the compiled
    .mo, so editing the .po without running `compilemessages` changes nothing.
    """

    def setUp(self):
        self.admin_user = User.objects.create_superuser(username='admin', password='pw', role=User.Role.ADMIN)
        self.client.force_login(self.admin_user)
        self.material = Material.objects.create(sku='SKU1', name='Kamenivo')

    def test_index_lists_apps_and_models_in_czech(self):
        html = self.client.get(reverse('admin:index')).content.decode()
        for label in ('Katalog', 'Zpracování', 'Zakázky', 'Uživatelé', 'Materiály', 'Položky zpracování'):
            with self.subTest(label=label):
                self.assertIn(label, html)

    def test_change_form_labels_are_czech(self):
        response = self.client.get(reverse('admin:materials_material_change', args=[self.material.pk]))
        self.assertContains(response, 'Kód (SKU)')
        self.assertContains(response, 'Aktivní')

    def test_no_english_blank_option_anywhere_in_the_admin(self):
        # Django 6's `- Select an option -`, translated by the local catalog.
        response = self.client.get(reverse('admin:workorders_workorder_add'))
        self.assertNotContains(response, '- Select an option -')
        self.assertContains(response, 'Vyberte možnost')

    def test_delete_confirmation_is_czech(self):
        # Fails if locale/cs/LC_MESSAGES/django.mo is missing or stale.
        response = self.client.get(reverse('admin:materials_material_delete', args=[self.material.pk]))
        self.assertContains(response, 'Opravdu chcete odstranit')
        self.assertNotContains(response, 'Are you sure you want to delete')

    def test_date_hierarchy_label_is_czech(self):
        response = self.client.get(reverse('admin:inventory_stockmovement_changelist'))
        self.assertContains(response, 'Filtrovat podle:')
        self.assertNotContains(response, 'Filter by')


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
