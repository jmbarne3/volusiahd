from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProgramReferralForm, ProgramRegistrationForm
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


def register_program(request):
    """The provider's own door. Everything `Program` publishes is collected here."""
    if request.method == "POST":
        form = ProgramRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect("directory:register_thanks")
    else:
        form = ProgramRegistrationForm()
    return render(request, "directory/register.html", {"form": form})


def register_thanks(request):
    return render(request, "directory/register_thanks.html")


def refer_program(request):
    """The neighbour's door. A lead, so we can invite them to register."""
    if request.method == "POST":
        form = ProgramReferralForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("directory:refer_thanks")
    else:
        form = ProgramReferralForm()
    return render(request, "directory/refer.html", {"form": form})


def refer_thanks(request):
    return render(request, "directory/refer_thanks.html")
