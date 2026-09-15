from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import SubmissionForm
from .models import Category, Page, Program


def _published_programs():
    return Program.objects.published().prefetch_related("categories")


def home(request):
    return render(
        request,
        "directory/home.html",
        {
            "featured": _published_programs().filter(is_featured=True)[:6],
            "categories": Category.objects.all(),
            "program_count": Program.objects.published().count(),
        },
    )


def program_list(request):
    programs = _published_programs()
    query = request.GET.get("q", "").strip()
    category_slug = request.GET.get("category", "").strip()

    if query:
        programs = programs.filter(
            Q(name__icontains=query)
            | Q(short_description__icontains=query)
            | Q(description__icontains=query)
            | Q(city__icontains=query)
        )
    active_category = None
    if category_slug:
        active_category = get_object_or_404(Category, slug=category_slug)
        programs = programs.filter(categories=active_category)

    return render(
        request,
        "directory/program_list.html",
        {
            "programs": programs.distinct(),
            "categories": Category.objects.all(),
            "query": query,
            "active_category": active_category,
        },
    )


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug)
    return render(
        request,
        "directory/program_list.html",
        {
            "programs": _published_programs().filter(categories=category),
            "categories": Category.objects.all(),
            "query": "",
            "active_category": category,
        },
    )


def program_detail(request, slug):
    program = get_object_or_404(_published_programs(), slug=slug)
    return render(
        request,
        "directory/program_detail.html",
        {
            "program": program,
            "contacts": program.contacts.filter(is_public=True),
        },
    )


def page_detail(request, slug):
    page = get_object_or_404(Page, slug=slug, is_published=True)
    return render(request, "directory/page.html", {"page": page})


def submit_program(request):
    if request.method == "POST":
        form = SubmissionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Thank you — we have your suggestion and will review it before it appears.",
            )
            return redirect("directory:submit_thanks")
    else:
        form = SubmissionForm()
    return render(request, "directory/submit.html", {"form": form})


def submit_thanks(request):
    return render(request, "directory/submit_thanks.html")
