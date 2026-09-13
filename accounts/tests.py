from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from materials.models import Material
from workorders.models import WorkOrder

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


class AdminIndexTests(TestCase):
    """What the admin index does *not* offer.

    The first two were dropped because they looked like features and were not:
    one duplicated what the job page already edits, the other could not have any
    effect at all. The third is kept out for a different reason — it would be
    the only English in a Czech admin.
    """

    def setUp(self):
        self.admin_user = User.objects.create_superuser(username='admin', password='pw', role=User.Role.ADMIN)
        self.client.force_login(self.admin_user)

    def test_line_items_have_no_section_of_their_own(self):
        # They are edited as an inline on the job; a second top-level section
        # for them would duplicate what Zakázky already shows.
        html = self.client.get(reverse('admin:index')).content.decode()
        self.assertNotIn('Položky zpracování', html)
        with self.assertRaises(NoReverseMatch):
            reverse('admin:workorders_stockmovement_changelist')

    def test_groups_are_not_in_the_admin(self):
        # Permissions play no part in this app, and every account that can reach
        # the admin is a superuser, which bypasses them anyway.
        html = self.client.get(reverse('admin:index')).content.decode()
        self.assertNotIn('Skupiny', html)
        self.assertNotIn('Autentizace a autorizace', html)
        with self.assertRaises(NoReverseMatch):
            reverse('admin:auth_group_changelist')

    def test_axes_is_not_in_the_admin(self):
        # AXES_ENABLE_ADMIN = False. Axes ships no Czech catalog, so its section
        # and both its models would render English in an admin this project
        # keeps Czech through three separate mechanisms. The attempts are still
        # recorded — `manage.py axes_list_attempts` reads them.
        html = self.client.get(reverse('admin:index')).content.decode()
        self.assertNotIn('Access attempts', html)
        self.assertNotIn('Axes', html)
        with self.assertRaises(NoReverseMatch):
            reverse('admin:axes_accessattempt_changelist')


class AdminUserListTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(username='admin', password='pw', role=User.Role.ADMIN)
        self.client.force_login(self.admin_user)
        self.worker = User.objects.create_user(username='worker', password='pw')

    def test_is_active_can_be_unchecked_from_the_changelist(self):
        # Like materials and machines, an account is retired from the list
        # without opening it.
        response = self.client.post(
            reverse('admin:accounts_user_changelist') + f'?q={self.worker.username}',
            {
                'form-TOTAL_FORMS': '1',
                'form-INITIAL_FORMS': '1',
                'form-0-id': str(self.worker.pk),
                '_save': 'Uložit',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.worker.refresh_from_db()
        self.assertFalse(self.worker.is_active)


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
        for label in ('Materiály', 'Stroje', 'Zakázky', 'Uživatelé'):
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
        WorkOrder.objects.create(created_by=self.admin_user)
        response = self.client.get(reverse('admin:workorders_workorder_changelist'))
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


class LoginRateLimitTests(TestCase):
    """django-axes, configured to lock the *account* rather than the address.

    The app is on the public internet and Django ships no brute-force
    protection, so this is the one thing the old LAN perimeter was doing that
    had to be replaced with code. The policy lives in `config/settings.py`.
    """

    def setUp(self):
        self.password = 'correct-horse-battery'
        self.user = User.objects.create_user(username='worker', password=self.password)
        self.url = reverse('login')

    def _attempt(self, username='worker', password='wrong'):
        return self.client.post(self.url, {'username': username, 'password': password})

    def test_failures_below_the_limit_just_re_render_the_login_page(self):
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            response = self._attempt()
            self.assertEqual(response.status_code, 200)
        # Still not locked: the real password works on the last allowed try.
        # Note this posts to the login view rather than calling client.login(),
        # which passes no request and which the axes backend rejects outright.
        response = self._attempt(password=self.password)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_hitting_the_limit_locks_out_with_the_czech_page(self):
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._attempt()
        response = self._attempt()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Účet je dočasně uzamčen', status_code=429)

    def test_lockout_survives_the_correct_password(self):
        # The point of the whole exercise: once locked, knowing the password is
        # not enough until the cool-off expires.
        for _ in range(settings.AXES_FAILURE_LIMIT):
            self._attempt()
        response = self._attempt(password=self.password)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_lockout_is_per_username_not_per_address(self):
        # AXES_LOCKOUT_PARAMETERS = ['username']. Every request in this test
        # comes from the same address, and behind the reverse proxy every
        # request in production does too. Locking by IP would mean one worker
        # mistyping their password took the whole depot offline.
        other = User.objects.create_user(username='druhy', password=self.password)
        for _ in range(settings.AXES_FAILURE_LIMIT):
            self._attempt()
        response = self.client.post(self.url, {'username': other.username, 'password': self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), other.pk)

    def test_a_successful_login_clears_the_counter(self):
        # AXES_RESET_ON_SUCCESS. Four typos spread over a week must not add up
        # to a lockout on an unrelated fifth day.
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            self._attempt()
        self.client.post(self.url, {'username': 'worker', 'password': self.password})
        self.client.logout()
        for _ in range(settings.AXES_FAILURE_LIMIT - 1):
            response = self._attempt()
            self.assertEqual(response.status_code, 200)
