# Volusia County Homeschool Directory

A directory of homeschool co-ops, classes, sports, arts, testing, and support
groups in Volusia County, Florida. Django 5.2 LTS on SQLite, built so that
adding a program takes four minutes and nobody has to be asked to do it.

The full development and hosting plan — stack rationale, phases, what to buy on
Hetzner, backup and recovery — is in
[docs/volusia-homeschool-directory-plan.md](docs/volusia-homeschool-directory-plan.md).

## Running it locally

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync                                  # create .venv and install dependencies
cp .env.example .env                     # then set SECRET_KEY
uv run python manage.py migrate
uv run python manage.py seed_categories  # the nine starting categories
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

The public site is at http://localhost:8000 and the admin at
http://localhost:8000/admin.

Generate a secret key with:

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(50))"
```

## Checks

```bash
uv run python manage.py test directory   # 28 tests
uv run ruff check . && uv run ruff format .
uv run python manage.py check --deploy   # run with DEBUG=false
```

## Layout

```
manage.py                 puts src/ on the path, then defers to Django
src/volusiahd/            project package: settings, urls, wsgi, unfold config
src/directory/            the single app — models, admin, views, tests
src/templates/            public templates; base.html carries the Open Graph tags
src/static/css/site.css   hand-written, no build step
docs/                     the development plan and hosting guides
Dockerfile, fly.toml      the production image and its Fly.io configuration
deploy/                   Litestream config and the container's start scripts
```

One app, not four. The admin groups by app, and the content manager should see
one card with four things on it — Programs, Categories, Pages, Submissions —
rather than hunting across sections.

## Things worth knowing before you change something

**Rich text is sanitized on save, not on form validation.** `ProseEditorField`
only cleans during `full_clean()`, which covers the admin but not a spreadsheet
import or a shell script. `SanitizedRichTextMixin` in
[src/directory/models.py](src/directory/models.py) moves that to `save()`,
because the templates render these fields with `|safe`. If you add another rich
text field, put it on a model that uses the mixin.

**The editor toolbar is deliberately narrow** — bold, italic, links, two list
types, one heading level. The nh3 allowlist is derived from that same config, so
the editor and the sanitizer cannot drift apart. Widening one widens the other;
that is the point.

**There is no faith-based or secular flag,** and no category that reintroduces
it under another name. This is a settled decision, explained in the plan.

**`last_verified_on` is the field that keeps the directory alive.** It is in the
list view, sortable, and there is a bulk "Mark as verified today" action so an
annual accuracy sweep is a morning's work.

**The flat-page route is last in [src/directory/urls.py](src/directory/urls.py)**
because `<slug:slug>/` would otherwise swallow every route above it.

## The two public forms

Providers **register** their own programs at `/register/`, and that form
collects every field `Program` publishes — description, schedule, cost, grades
and ages, address, logo, and a contact person. **Approving it is the only action
left**; `build_program_from()` in [src/directory/admin.py](src/directory/admin.py)
copies the record across field for field, and the tests in `ApprovalTests`
assert that nothing a registrant typed gets dropped. If you add a field to
`Program`, add it there too, or it becomes something she retypes by hand.

Anyone else can **suggest** a program they do not run at `/suggest/`. That form
is deliberately thin, because it is a lead rather than a listing: enough to
reach whoever runs it and invite them to register it properly.

Both land in one Submissions queue with a Kind column, so there is only ever one
inbox to check. Approving publishes immediately — except for a record with no
one-line description, which becomes a draft instead, because a listing has
nothing to show without one.

Neither form gets a rich text editor. Plain text becomes paragraphs on approval,
which keeps typography consistent and the sanitization surface narrow.

## Where this is in the plan

Phases 1 and 3 are done, along with most of Phase 4 — models, migrations, a
styled admin, the public list and detail pages, search, category filtering,
flat pages, provider registration and referral with a shared moderation queue,
sitemap, robots, and Open Graph tags.

Phase 2 moved from a Hetzner server to Fly.io. The image, `fly.toml`, and
Litestream setup are in the repository and build locally; the first real deploy
is next, following `docs/hosting-setup.md`. Phase 0, the twenty-program content inventory,
runs in parallel and is not a code task.
