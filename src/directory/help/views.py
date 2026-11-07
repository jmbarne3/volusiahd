"""The editor's guide, as pages inside the admin (#39).

Inside the admin rather than on a wiki or in a document, because the admin is
where the editor already is, and every instruction can link straight to the
screen it is about. Those links are `{% url %}` tags, so a screen renamed in
code breaks a test here rather than quietly breaking the guide.

The pages are templates under `admin/help/`. Adding a topic is a template and
a line in TOPICS. The routes are added to the admin site itself, in
`volusiahd/sites.py`, which is also what checks the reader is signed in.
"""

from django.contrib import admin
from django.http import Http404
from django.shortcuts import render

from .topics import BY_SLUG, TOPICS


def _context(request, **extra):
    return {**admin.site.each_context(request), "topics": TOPICS, **extra}


def index(request):
    return render(request, "admin/help/index.html", _context(request, title="Editor's guide"))


def topic(request, slug):
    if slug not in BY_SLUG:
        raise Http404("No such help topic.")
    current = BY_SLUG[slug]
    position = TOPICS.index(current)
    return render(
        request,
        f"admin/help/{slug}.html",
        _context(
            request,
            title=current.title,
            topic=current,
            previous=TOPICS[position - 1] if position else None,
            next=TOPICS[position + 1] if position + 1 < len(TOPICS) else None,
        ),
    )
