"""Static file storage for production: minify our CSS, then hash and compress it.

Our stylesheets are written to be read — they carry long comments explaining why
each rule exists — and those comments were about half of every first visit's CSS.
This strips them during `collectstatic`, which runs when the Docker image is
built, so development keeps serving the commented source and production serves
the small one.

Only files from our own `STATICFILES_DIRS` are minified. The admin, Unfold and
the prose editor ship their own CSS, mostly minified already, and rewriting files
we did not write is a way to break something we cannot easily see.
"""

from pathlib import Path

from django.conf import settings
from rcssmin import cssmin
from whitenoise.storage import CompressedManifestStaticFilesStorage


class MinifiedCompressedManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """WhiteNoise's storage, with our CSS minified before it is hashed.

    Minifying first means the hash in the file name, the manifest and the
    compressed `.gz` and `.br` copies are all of the minified file.
    """

    def post_process(self, paths, dry_run=False, **options):
        paths = dict(paths)
        if not dry_run:
            for name, (source_storage, _) in list(paths.items()):
                if name.endswith(".css") and self._is_ours(source_storage):
                    self._minify(name)
                    # Hashing reads each file again from where it was found, not
                    # from the copy here — so without this the hashed file, the
                    # one pages actually load, would be the unminified source.
                    # Point it at the minified copy instead.
                    paths[name] = (self, name)
        yield from super().post_process(paths, dry_run=dry_run, **options)

    @staticmethod
    def _is_ours(source_storage):
        location = getattr(source_storage, "location", None)
        if not location:
            return False
        ours = {Path(directory).resolve() for directory in settings.STATICFILES_DIRS}
        return Path(location).resolve() in ours

    def _minify(self, name):
        # Minifying is idempotent, so a file collectstatic did not re-copy this
        # time (already minified on an earlier run) comes through unchanged.
        path = Path(self.path(name))
        source = path.read_text(encoding="utf-8")
        path.write_text(cssmin(source), encoding="utf-8")
