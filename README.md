# Volusia County Homeschool Directory

A directory of homeschool co-ops, classes, sports, arts, testing, and support
groups in Volusia County, Florida. Django 5.2 LTS on SQLite, built so that
adding a program takes four minutes and nobody has to be asked to do it.

The development plan and the hosting guides — stack rationale, phases, the Fly.io
deploy step by step, backup and recovery — live in `docs/`, which is deliberately
not in version control. Ask Jim for a copy.

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
uv run python manage.py test directory accounts --exclude-tag=network  # 98 tests
uv run ruff check . && uv run ruff format .
uv run python manage.py check --deploy   # run with DEBUG=false
```

These same checks run in GitHub Actions on every push, and again against the
exact ref you deploy. The `network` tag hides one file,
`accounts/tests/test_contract.py`, which asks Google's live discovery document
whether our OAuth constants are still right. It runs on a weekly schedule of
its own; to run it by hand:

```bash
uv run python manage.py test accounts.tests.test_contract
```

## Deploying

There is one environment and one machine, and nothing deploys itself. A push
to `main` runs the checks and changes nothing on the live site. Shipping is a
deliberate act: open **Actions → Deploy to production → Run workflow**, give
it a tag, branch, or commit SHA, and that is what goes out.

The checks run against the ref you named rather than against whatever passed
CI earlier, so a tag cut from an older commit is tested exactly as it will be
deployed. Afterwards the workflow polls the site until it answers 200.
**That smoke test is not decoration** — `fly.toml` deliberately defines no
health check, so `flyctl deploy` returns once the machine is running, not once
Django is answering. Without the poll, a container that boots and immediately
crashes still reports a green deploy.

The only thing GitHub needs is one repository secret:

| Secret | How to get it |
|---|---|
| `FLY_API_TOKEN` | `fly tokens create deploy -a volusiahd` |

That is the whole list, and the reason it is so short is worth knowing.
`flyctl deploy --remote-only` builds the image on Fly's builders and tells Fly
to run it; Fly injects the app's own secrets at boot. So `SECRET_KEY`, the
`B2_*` Litestream credentials, the Tigris `AWS_*` values, and the Google OAuth
client stay in `fly secrets` and must **not** be copied into GitHub. Anything
duplicated there is a second place to rotate and a second place to leak.

A deploy token is scoped to the one app: it cannot reach anything else in the
Fly organization. Add `-x 8760h` if you want it to expire in a year.

The workflows are three files plus one they share:

```
.github/workflows/checks.yml     lint, tests, production config check
.github/workflows/ci.yml         runs the checks on every push
.github/workflows/deploy.yml     manual, takes a ref, deploys and smoke-tests
.github/workflows/google-contract.yml   weekly: asks Google if we still match
```

`checks.yml` exists as its own callable workflow so CI and deploy cannot run
different checks. Duplicating those steps would eventually mean deploying
something that was never really tested.

## Layout

```
manage.py                 puts src/ on the path, then defers to Django
src/volusiahd/            project package: settings, urls, wsgi, unfold config
src/directory/            the single app — models, admin, views, tests
src/accounts/             Google sign-in for the admin; no other app imports it
src/templates/            public templates; base.html carries the Open Graph tags
src/static/css/site.css   hand-written, no build step
Dockerfile, fly.toml      the production image and its Fly.io configuration
deploy/                   Litestream config and the container's start scripts
```

One app, not four. The admin groups by app, and the content manager should see
one card with four things on it — Programs, Categories, Pages, Submissions —
rather than hunting across sections.

## Things worth knowing before you change something

**A Google account is not an admin account.** The admin login page offers
"Sign in with Google", but nothing in `src/accounts/` creates a `User` — the
row has to already exist, be active, and be staff, or the sign-in is refused
and logged. Adding someone is a superuser opening **Access → People** in the
admin. The credentials, the setup steps, and the reasoning behind skipping the
JWT signature check are in [docs/admin-access.md](docs/admin-access.md).

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

Phase 2 moved from a Hetzner server to Fly.io, and the site is deployed: one
machine in Ashburn, SQLite on a volume with Litestream streaming to Backblaze
B2, and uploaded logos in object storage. Still open are the restore drill and
`RUNBOOK.md`. Phase 0, the twenty-program content inventory, runs in parallel
and is not a code task.
