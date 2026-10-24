"""Job photos."""

import io
import shutil
import tempfile
from decimal import Decimal

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import User
from workorders.forms import (
    PHOTO_MAX_BYTES,
    PHOTO_MAX_COUNT,
    PHOTO_MAX_TOTAL_BYTES,
)
from workorders.models import JobPhoto, WorkOrder
from workorders.photos import PHOTO_MAX_SIDE

from .helpers import ReviewFixtureMixin, job_payload


class JobPhotoTests(ReviewFixtureMixin, TestCase):
    """„Fotky": optional photos on a job.

    Both callers are multipart; photos are only ever added and removed only via „Odebrat"; files are never public and
    are deleted with their row; every upload is re-encoded, so fixtures are real images.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A real directory, since these tests are about files on disk. The
        # override resets `default_storage`, so the model field follows it.
        cls.media_root = tempfile.mkdtemp()
        cls.media_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls.media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.media_override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def _photo(self, name='foto.jpg', size=(40, 30), image_format='JPEG', mode='RGB', color='red', **save_kwargs):
        buffer = io.BytesIO()
        Image.new(mode, size, color).save(buffer, image_format, **save_kwargs)
        return SimpleUploadedFile(name, buffer.getvalue())

    def _stored_image(self, photo):
        """A stored `JobPhoto`, decoded — the bytes are the app's, not the upload's."""
        with photo.file.open('rb') as stored:
            image = Image.open(stored)
            image.load()
        return image

    def _submit(self, url=None, **overrides):
        """A balanced job, with whatever „Fotky" the test passes."""
        payload = job_payload(
            [{'material': self.material_raw, 'quantity': Decimal('5')}],
            [{'material': self.material_finished, 'quantity': Decimal('5')}],
        )
        payload.update(overrides)
        return self.client.post(url or reverse('transform_create'), payload)

    def _job_with_photo(self, **overrides):
        self.client.force_login(self.worker)
        response = self._submit(photo=self._photo(**overrides))
        self.assertRedirects(response, reverse('transform_create'))
        return WorkOrder.objects.get()

    def _job_with_photos(self, count):
        self.client.force_login(self.worker)
        response = self._submit(photo=[self._photo(name=f'foto{i}.jpg', size=(10 + i, 10)) for i in range(count)])
        self.assertRedirects(response, reverse('transform_create'))
        return WorkOrder.objects.get()

    def _edit_url(self, work_order):
        self.client.force_login(self.manager)
        return reverse('job_edit', args=[work_order.pk])

    def test_a_job_records_no_photo_by_default(self):
        # Optional everywhere: no upload is no row, not a failed job.
        work_order = self.submit_job(self.worker)
        self.assertFalse(work_order.photos.exists())

    def test_an_uploaded_photo_is_stored_on_the_job(self):
        photo = self._job_with_photo().photos.get()
        self.assertTrue(default_storage.exists(photo.file.name))
        image = self._stored_image(photo)
        self.assertEqual(image.format, 'JPEG')
        self.assertEqual(image.size, (40, 30))

    def test_several_picked_photos_are_all_stored_in_order(self):
        # What a gallery pick of several files posts: one name, several parts.
        work_order = self._job_with_photos(3)
        self.assertEqual(
            [self._stored_image(photo).size for photo in work_order.photos.all()], [(10, 10), (11, 10), (12, 10)]
        )

    def test_several_camera_shots_are_all_stored(self):
        # photo_picker.js keeps every shot in an input of its own, all named
        # `camera_photo`; the form reads them all rather than the last.
        self.client.force_login(self.worker)
        response = self._submit(camera_photo=[self._photo(size=(20, 10)), self._photo(size=(30, 10))])
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(
            [self._stored_image(photo).size for photo in WorkOrder.objects.get().photos.all()], [(20, 10), (30, 10)]
        )

    def test_a_camera_shot_and_a_picked_file_are_both_stored(self):
        # They used to be refused together, when a job held one photo.
        self.client.force_login(self.worker)
        response = self._submit(photo=self._photo(size=(20, 10)), camera_photo=self._photo(size=(30, 10)))
        self.assertRedirects(response, reverse('transform_create'))
        self.assertEqual(WorkOrder.objects.get().photos.count(), 2)

    def test_a_large_photo_is_shrunk_to_the_long_side_cap(self):
        # The point of the whole thing: a phone photo is thousands of pixels
        # wide, and the disk is sized for a few hundred KB per photo.
        work_order = self._job_with_photo(size=(PHOTO_MAX_SIDE * 2, PHOTO_MAX_SIDE))
        self.assertEqual(self._stored_image(work_order.photos.get()).size, (PHOTO_MAX_SIDE, PHOTO_MAX_SIDE // 2))

    def test_a_camera_shot_is_shrunk_like_a_picked_file(self):
        self.client.force_login(self.worker)
        response = self._submit(camera_photo=self._photo(size=(PHOTO_MAX_SIDE * 2, 10)))
        self.assertRedirects(response, reverse('transform_create'))
        image = self._stored_image(WorkOrder.objects.get().photos.get())
        self.assertEqual((image.format, image.width), ('JPEG', PHOTO_MAX_SIDE))

    def test_a_portrait_shot_is_turned_upright_and_its_exif_dropped(self):
        # A phone stores portrait pixels sideways plus an EXIF flag; the flag
        # goes with the EXIF block (GPS position included), so the pixels turn.
        exif = Image.Exif()
        exif[0x0112] = 6  # Orientation: rotate 90° clockwise to display.
        work_order = self._job_with_photo(size=(40, 30), exif=exif.tobytes())
        image = self._stored_image(work_order.photos.get())
        self.assertEqual(image.size, (30, 40))
        self.assertFalse(image.getexif())

    def test_a_heic_photo_is_stored_as_jpeg(self):
        # What an iPhone shoots by default, and what pillow-heif is here for.
        photo = self._job_with_photo(name='IMG_0001.HEIC', image_format='HEIF').photos.get()
        self.assertTrue(photo.file.name.endswith('.jpg'))
        self.assertEqual(self._stored_image(photo).format, 'JPEG')

    def test_a_transparent_png_lands_on_white(self):
        # JPEG has no alpha; a bare convert('RGB') would turn a screenshot's
        # transparent margin black.
        work_order = self._job_with_photo(name='snimek.png', image_format='PNG', mode='RGBA', color=(0, 0, 0, 0))
        self.assertEqual(self._stored_image(work_order.photos.get()).getpixel((0, 0)), (255, 255, 255))

    def test_a_file_named_like_a_photo_but_not_one_is_refused(self):
        # The extension passes; decoding is what catches it, and it is a field
        # error rather than the 500 an unhandled Pillow error would be. Named,
        # since it may be one of several.
        self.client.force_login(self.worker)
        response = self._submit(photo=[self._photo(name='dobra.jpg'), SimpleUploadedFile('spatna.jpg', b'%PDF-1.4')])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'spatna.jpg: Soubor se nepodařilo otevřít jako fotku.')
        self.assertNotContains(response, 'dobra.jpg:')

    def test_a_camera_file_that_is_not_a_photo_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(camera_photo=SimpleUploadedFile('foto.jpg', b'%PDF-1.4'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Soubor se nepodařilo otevřít jako fotku.')

    def test_the_stored_name_is_the_app_s_and_not_the_phone_s(self):
        # `IMG_0001.jpg` off two phones would otherwise collide every day.
        first = self._job_with_photo(name='IMG_0001.jpg').photos.get()
        self.client.force_login(self.other_worker)
        self._submit(photo=self._photo(name='IMG_0001.jpg'))
        second = JobPhoto.objects.exclude(pk=first.pk).get()
        self.assertNotIn('IMG_0001', first.file.name)
        self.assertNotEqual(first.file.name, second.file.name)
        self.assertTrue(first.file.name.startswith('job_photos/'))
        self.assertTrue(first.file.name.endswith('.jpg'))

    def test_both_job_pages_post_as_multipart(self):
        # Without the enctype the browser posts the file's *name* and the photo
        # silently never arrives — the one part of this the partial cannot do.
        work_order = self.submit_job(self.worker)
        edit_url = self._edit_url(work_order)
        for url in (reverse('transform_create'), edit_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'enctype="multipart/form-data"')
                self.assertContains(response, 'name="photo"')

    def test_both_job_pages_offer_the_camera_and_the_gallery_separately(self):
        # Chrome on Android opens a bare `accept="image/*"` input straight into
        # the file picker, so the camera needs an input of its own — and only
        # that one may carry `capture`, which takes the gallery away. The
        # gallery takes several files at once; the camera returns one shot.
        work_order = self.submit_job(self.worker)
        edit_url = self._edit_url(work_order)
        for url in (reverse('transform_create'), edit_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(
                    response,
                    '<input type="file" name="camera_photo" accept="image/*" capture="environment" '
                    'id="id_camera_photo">',
                    html=True,
                )
                gallery = response.context['order_form']['photo'].as_widget()
                self.assertIn('accept="image/*"', gallery)
                self.assertIn('multiple', gallery)
                self.assertNotIn('capture', gallery)

    def test_a_file_that_is_not_a_photo_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(photo=SimpleUploadedFile('poznamky.pdf', b'%PDF-1.4'))
        self.assertEqual(response.status_code, 200)
        # Nothing at all is written, so the complaint has to be on the page.
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Přípona souboru')

    def test_a_photo_past_the_size_cap_is_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(photo=SimpleUploadedFile('foto.jpg', b'x' * (PHOTO_MAX_BYTES + 1)))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'foto.jpg: Fotka je příliš velká')

    def test_more_photos_than_the_cap_are_refused(self):
        self.client.force_login(self.worker)
        response = self._submit(photo=[self._photo(name=f'foto{i}.jpg') for i in range(PHOTO_MAX_COUNT + 1)])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(
            response, f'Zakázka může mít nejvýše {PHOTO_MAX_COUNT} fotek (vybráno {PHOTO_MAX_COUNT + 1}).'
        )

    def test_a_batch_past_the_total_size_cap_is_refused_before_anything_is_decoded(self):
        # Each file is within its own cap and none is a picture: refused on the
        # total, and without a decoding complaint, so nothing was decoded.
        count = PHOTO_MAX_TOTAL_BYTES // PHOTO_MAX_BYTES + 1
        self.client.force_login(self.worker)
        response = self._submit(
            photo=[SimpleUploadedFile(f'foto{i}.jpg', b'x' * PHOTO_MAX_BYTES) for i in range(count)]
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(WorkOrder.objects.exists())
        self.assertContains(response, 'Fotky jsou dohromady příliš velké')
        self.assertNotContains(response, 'Soubor se nepodařilo otevřít')

    def test_the_job_page_links_every_photo_to_its_page_and_into_the_panel(self):
        # Phones follow the first link to the photo page, never the bare file,
        # which strands an installed app; desktop follows the second into the panel.
        work_order = self._job_with_photos(2)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        for number in (1, 2):
            with self.subTest(number=number):
                photo_url = reverse('job_photo', args=[work_order.pk, number])
                self.assertContains(
                    response, f'<a class="photo-page-link" href="{photo_url}">Fotka {number}</a>', html=True
                )
                self.assertContains(
                    response, f'<a class="photo-panel-link" href="#fotka-{number}">Fotka {number}</a>', html=True
                )

    def test_the_job_pages_pin_every_photo_in_a_panel(self):
        # Every photo is in the panel, lazily loaded, so only the one shown is
        # fetched; the buttons switch by anchor, without a script or a reload.
        work_order = self._job_with_photos(3)
        photos = list(work_order.photos.all())
        self.client.force_login(self.manager)
        for name in ('job_detail', 'job_edit'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=[work_order.pk]))
                self.assertContains(response, '<main class="with-photo-panel">')
                self.assertContains(response, '<aside class="photo-panel"', count=1)
                # `{# #}` is single-line only; a multi-line one renders as text.
                self.assertNotContains(response, '{#')
                for number, photo in enumerate(photos, start=1):
                    self.assertContains(response, f'id="fotka-{number}"', count=1)
                    self.assertContains(response, f'<img src="{photo.file.url}"', count=2)
                self.assertContains(response, 'loading="lazy"', count=6)
                self.assertContains(response, 'href="#fotka-2">Předchozí fotka</a>', count=1)
                self.assertContains(response, 'href="#fotka-2">Další fotka</a>', count=1)
                self.assertNotContains(response, 'href="#fotka-0"')
                self.assertNotContains(response, 'href="#fotka-4"')

    def test_no_panel_without_photos(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        for name in ('job_detail', 'job_edit'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=[work_order.pk]))
                self.assertNotContains(response, 'photo-panel')
                self.assertContains(response, '<main class="">')

    def test_a_photo_opens_full_size_without_leaving_the_app(self):
        # A link to an in-page overlay, not to the bare file.
        work_order = self._job_with_photos(1)
        photo = work_order.photos.get()
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_photo', args=[work_order.pk, 1]))
        self.assertContains(response, '<a class="photo-frame" href="#fotka-1-plna"')
        self.assertContains(response, '<div class="photo-full" id="fotka-1-plna">')
        self.assertContains(response, '<a class="btn secondary" href="#fotka-1">Zavřít</a>', html=True)
        self.assertNotContains(response, f'href="{photo.file.url}"')

    def test_the_photo_page_shows_the_photo_with_a_way_back(self):
        work_order = self._job_with_photos(3)
        photos = list(work_order.photos.all())
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_photo', args=[work_order.pk, 2]))
        self.assertContains(response, f'src="{photos[1].file.url}"')
        self.assertContains(response, f'href="{reverse("job_detail", args=[work_order.pk])}"')
        self.assertContains(response, f'href="{reverse("job_photo", args=[work_order.pk, 1])}"')
        self.assertContains(response, f'href="{reverse("job_photo", args=[work_order.pk, 3])}"')

    def test_the_photo_page_offers_no_step_past_either_end(self):
        work_order = self._job_with_photos(2)
        self.client.force_login(self.manager)
        first = self.client.get(reverse('job_photo', args=[work_order.pk, 1]))
        self.assertNotContains(first, f'href="{reverse("job_photo", args=[work_order.pk, 0])}"')
        self.assertNotContains(first, 'Předchozí fotka')
        self.assertContains(first, 'class="btn secondary photo-next"')
        last = self.client.get(reverse('job_photo', args=[work_order.pk, 2]))
        self.assertContains(last, 'class="btn secondary photo-previous"')
        self.assertNotContains(last, 'Další fotka')

    def test_a_lone_photo_has_no_buttons(self):
        work_order = self._job_with_photos(1)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_photo', args=[work_order.pk, 1]))
        self.assertNotContains(response, 'photo-nav')

    def test_a_photo_number_the_job_does_not_have_is_a_404(self):
        work_order = self._job_with_photos(1)
        self.client.force_login(self.manager)
        for number in (0, 2):
            with self.subTest(number=number):
                self.assertEqual(self.client.get(reverse('job_photo', args=[work_order.pk, number])).status_code, 404)

    def test_the_photo_page_is_the_reviewers(self):
        work_order = self._job_with_photos(1)
        url = reverse('job_photo', args=[work_order.pk, 1])
        # Another worker's job; its author's case is test_the_author_sees_the_photos_of_their_pending_job.
        self.client.force_login(self.other_worker)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertIn('/login/', self.client.get(url).url)

    def test_the_job_page_has_no_photo_card_without_photos(self):
        work_order = self.submit_job(self.worker)
        self.client.force_login(self.manager)
        response = self.client.get(reverse('job_detail', args=[work_order.pk]))
        self.assertNotContains(response, '<h2>Fotky</h2>', html=True)

    def test_the_photo_is_served_to_a_reviewer(self):
        photo = self._job_with_photo().photos.get()
        self.client.force_login(self.manager)
        response = self.client.get(photo.file.url)
        self.assertEqual(response.status_code, 200)
        with photo.file.open('rb') as stored:
            self.assertEqual(b''.join(response.streaming_content), stored.read())

    def test_the_photo_is_not_public(self):
        # MEDIA_ROOT is not served by the proxy or by whitenoise, so this URL is
        # the only way in — and it carries the job page's own gate.
        photo = self._job_with_photo().photos.get()
        # A worker may record a job, and photos with it, but the job pages are
        # the manager's — so this is the same 403 `job_detail` gives them.
        self.client.force_login(self.other_worker)
        self.assertEqual(self.client.get(photo.file.url).status_code, 403)
        self.client.logout()
        response = self.client.get(photo.file.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_the_author_sees_the_photos_of_their_pending_job(self):
        # `job_edit` shows them their stored photos, so the file and `job_photo` must open.
        work_order = self._job_with_photo()
        photo = work_order.photos.get()
        self.assertEqual(self.client.get(photo.file.url).status_code, 200)
        self.assertEqual(self.client.get(reverse('job_photo', args=[work_order.pk, 1])).status_code, 200)
        self.client.force_login(self.manager)
        self.client.post(reverse('job_approve', args=[work_order.pk]), {'revision': work_order.revision})
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(photo.file.url).status_code, 403)
        self.assertEqual(self.client.get(reverse('job_photo', args=[work_order.pk, 1])).status_code, 403)

    def test_a_worker_gets_403_for_a_missing_file(self):
        # Not a 404, which would tell them what exists.
        self._job_with_photo()
        self.assertEqual(self.client.get(reverse('protected_media', args=['job_photos/nic.jpg'])).status_code, 403)

    def test_a_path_that_is_not_a_file_is_a_404_rather_than_a_500(self):
        self._job_with_photo()
        self.client.force_login(self.manager)
        for path in ('job_photos/2026/01/nic.jpg', 'job_photos'):
            with self.subTest(path=path):
                # The second is a directory, which opens as an OSError of its own.
                response = self.client.get(reverse('protected_media', args=[path]))
                self.assertEqual(response.status_code, 404)

    def test_a_path_climbing_out_of_the_media_root_is_refused(self):
        # The storage joins through `safe_join`; nothing here has to parse paths.
        self.client.force_login(self.manager)
        response = self.client.get('/media/../config/settings.py')
        self.assertEqual(response.status_code, 400)

    def test_an_edit_that_sends_no_file_keeps_the_photos(self):
        # A file input cannot be re-filled, so every edit arrives without one.
        work_order = self._job_with_photos(2)
        stored = [photo.file.name for photo in work_order.photos.all()]
        url = self._edit_url(work_order)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertRedirects(self._submit(url=url), reverse('job_detail', args=[work_order.pk]))
        self.assertEqual([photo.file.name for photo in work_order.photos.all()], stored)
        for name in stored:
            self.assertTrue(default_storage.exists(name))

    def test_an_edit_adds_photos_beside_the_stored_ones(self):
        # The whole point of the change: a new photo never replaces one.
        work_order = self._job_with_photo()
        old = work_order.photos.get().file.name
        url = self._edit_url(work_order)
        with self.captureOnCommitCallbacks(execute=True):
            self._submit(url=url, photo=self._photo(size=(20, 10)), camera_photo=self._photo(size=(30, 10)))
        photos = list(work_order.photos.all())
        self.assertEqual(photos[0].file.name, old)
        self.assertTrue(default_storage.exists(old))
        self.assertEqual([self._stored_image(photo).size for photo in photos[1:]], [(20, 10), (30, 10)])

    def test_an_edit_removes_only_the_ticked_photo(self):
        work_order = self._job_with_photos(3)
        first, second, third = work_order.photos.all()
        url = self._edit_url(work_order)
        # The removed file goes on commit, which a TestCase never reaches.
        with self.captureOnCommitCallbacks(execute=True):
            self._submit(url=url, remove_photos=[second.pk])
        self.assertEqual(list(work_order.photos.all()), [first, third])
        self.assertFalse(default_storage.exists(second.file.name))
        self.assertTrue(default_storage.exists(first.file.name))
        self.assertTrue(default_storage.exists(third.file.name))

    def test_an_edit_can_remove_and_add_at_once(self):
        work_order = self._job_with_photo()
        old = work_order.photos.get()
        url = self._edit_url(work_order)
        with self.captureOnCommitCallbacks(execute=True):
            self._submit(url=url, remove_photos=[old.pk], photo=self._photo(size=(20, 10)))
        self.assertEqual(self._stored_image(work_order.photos.get()).size, (20, 10))
        self.assertFalse(default_storage.exists(old.file.name))

    def test_an_edit_cannot_remove_another_job_s_photo(self):
        # The choices are the job's own photos; any other pk is not one of them.
        other = self._job_with_photo()
        work_order = self.submit_job(self.worker)
        url = self._edit_url(work_order)
        response = self._submit(url=url, remove_photos=[other.photos.get().pk])
        self.assertEqual(response.status_code, 200)
        self.assertIn('remove_photos', response.context['order_form'].errors)
        self.assertTrue(other.photos.exists())

    def test_the_cap_counts_the_photos_the_job_keeps(self):
        work_order = self._job_with_photos(PHOTO_MAX_COUNT)
        url = self._edit_url(work_order)
        response = self._submit(url=url, photo=self._photo())
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, f'Zakázka může mít nejvýše {PHOTO_MAX_COUNT} fotek (vybráno 1, už uloženo {PHOTO_MAX_COUNT}).'
        )
        self.assertEqual(work_order.photos.count(), PHOTO_MAX_COUNT)
        # Removing one makes room for one.
        with self.captureOnCommitCallbacks(execute=True):
            response = self._submit(url=url, photo=self._photo(), remove_photos=[work_order.photos.first().pk])
        self.assertRedirects(response, reverse('job_detail', args=[work_order.pk]))
        self.assertEqual(work_order.photos.count(), PHOTO_MAX_COUNT)

    def test_the_edit_page_offers_each_stored_photo_for_removal(self):
        work_order = self._job_with_photos(2)
        response = self.client.get(self._edit_url(work_order))
        for number, photo in enumerate(work_order.photos.all(), start=1):
            with self.subTest(number=number):
                photo_url = reverse('job_photo', args=[work_order.pk, number])
                self.assertContains(
                    response, f'<a class="photo-page-link" href="{photo_url}">Fotka {number}</a>', html=True
                )
                self.assertContains(
                    response, f'<a class="photo-panel-link" href="#fotka-{number}">Fotka {number}</a>', html=True
                )
                self.assertContains(response, f'name="remove_photos" value="{photo.pk}"')

    def test_no_removal_box_where_there_is_nothing_to_remove(self):
        work_order = self.submit_job(self.worker)
        self.assertNotContains(self.client.get(self._edit_url(work_order)), 'remove_photos')
        self.assertNotContains(self.client.get(reverse('transform_create')), 'remove_photos')

    def test_a_row_button_keeps_the_ticked_removal_boxes(self):
        # The no-JS rebuild is unbound and reads the POST back field by field;
        # a multi-value field needs every value, not the last.
        work_order = self._job_with_photos(3)
        first, _second, third = work_order.photos.all()
        url = self._edit_url(work_order)
        response = self._submit(url=url, remove_photos=[first.pk, third.pk], add_consumed='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['order_form']['remove_photos'].value(), [str(first.pk), str(third.pk)])
        self.assertEqual(work_order.photos.count(), 3)

    def test_deleting_a_job_deletes_its_photos(self):
        # Django removes neither on delete() nor on a cascade; the post_delete
        # receiver is what does.
        work_order = self._job_with_photos(2)
        stored = [photo.file.name for photo in work_order.photos.all()]
        self.client.force_login(self.manager)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('job_delete', args=[work_order.pk]))
        self.assertFalse(WorkOrder.objects.exists())
        self.assertFalse(JobPhoto.objects.exists())
        for name in stored:
            self.assertFalse(default_storage.exists(name))

    def test_the_admin_s_bulk_delete_deletes_the_photos_too(self):
        # A queryset delete: the receiver's registration is what keeps the
        # cascade off the fast-delete path, so it still fires per photo.
        work_order = self._job_with_photo()
        stored = work_order.photos.get().file.name
        self.client.force_login(User.objects.create_superuser(username='sprava', password='pw'))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('admin:workorders_workorder_changelist'),
                {'action': 'delete_selected', '_selected_action': [work_order.pk], 'post': 'yes'},
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(default_storage.exists(stored))

    def _admin_post(self, work_order, photo_rows):
        """The admin's change form for `work_order`, its photo inline set to `photo_rows`."""
        data = {
            'description': work_order.description,
            'notes': '',
            'performed_on': work_order.performed_on.isoformat(),
            'status': work_order.status,
            **{
                f'{prefix}-{key}': value
                for prefix in ('movements', 'machine_usages', 'worker_hours')
                for key, value in (
                    ('TOTAL_FORMS', '0'),
                    ('INITIAL_FORMS', '0'),
                    ('MIN_NUM_FORMS', '0'),
                    ('MAX_NUM_FORMS', '1000'),
                )
            },
            'photos-TOTAL_FORMS': str(len(photo_rows)),
            'photos-INITIAL_FORMS': str(sum(1 for row in photo_rows if 'id' in row)),
            'photos-MIN_NUM_FORMS': '0',
            'photos-MAX_NUM_FORMS': str(PHOTO_MAX_COUNT),
        }
        for index, row in enumerate(photo_rows):
            data[f'photos-{index}-work_order'] = work_order.pk
            for key, value in row.items():
                data[f'photos-{index}-{key}'] = value
        self.client.force_login(User.objects.create_superuser(username='sprava', password='pw'))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('admin:workorders_workorder_change', args=[work_order.pk]), data)
        self.assertEqual(response.status_code, 302)

    def test_the_admin_shrinks_a_photo_added_there(self):
        work_order = self._job_with_photo()
        self._admin_post(
            work_order,
            [
                {'id': work_order.photos.get().pk},
                {'file': self._photo(name='jina.png', size=(PHOTO_MAX_SIDE * 2, 10), image_format='PNG')},
            ],
        )
        image = self._stored_image(work_order.photos.last())
        self.assertEqual(work_order.photos.count(), 2)
        self.assertEqual((image.format, image.width), ('JPEG', PHOTO_MAX_SIDE))

    def test_the_admin_does_not_leave_a_replaced_photo_behind(self):
        # The admin writes the file through its own widget, so it needs the
        # cleanup the receiver cannot give a row that survives.
        work_order = self._job_with_photo()
        photo = work_order.photos.get()
        old = photo.file.name
        self._admin_post(work_order, [{'id': photo.pk, 'file': self._photo(name='jina.jpg', size=(20, 10))}])
        photo.refresh_from_db()
        self.assertNotEqual(photo.file.name, old)
        self.assertFalse(default_storage.exists(old))
        self.assertEqual(self._stored_image(photo).size, (20, 10))

    def test_the_admin_deletes_the_file_of_a_photo_deleted_there(self):
        work_order = self._job_with_photo()
        photo = work_order.photos.get()
        self._admin_post(work_order, [{'id': photo.pk, 'DELETE': 'on'}])
        self.assertFalse(work_order.photos.exists())
        self.assertFalse(default_storage.exists(photo.file.name))
