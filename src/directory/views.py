from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ProgramReferralForm, ProgramRegistrationForm
from .models import Category, Page, Program, Tag


def _published_programs():
    return Program.objects.published().select_related("category")


def _listing_context(programs, **extra):
    """Shared context for every page that renders the program listing."""
    return {
        "programs": programs.distinct(),
        "categories": Category.objects.all(),
        "query": "",
        **extra,
    }


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
            | Q(locations__icontains=query)
            # Tags are not listed anywhere, so the search box is the main way
            # anyone reaches one. Typing "Lego" has to find the Lego programs.
            | Q(tags__name__icontains=query)
        )
    active_category = None
    if category_slug:
        active_category = get_object_or_404(Category, slug=category_slug)
        programs = programs.filter(category=active_category)

    return render(
        request,
        "directory/program_list.html",
        _listing_context(programs, query=query, active_category=active_category),
    )


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug)
    return render(
        request,
        "directory/program_list.html",
        _listing_context(_published_programs().filter(category=category), active_category=category),
    )


def tag_detail(request, slug):
    """A tag's own page. Tags are found, not browsed, so this is where a tag
    link from a program page or a search result lands."""
    tag = get_object_or_404(Tag, slug=slug)
    return render(
        request,
        "directory/program_list.html",
        _listing_context(_published_programs().filter(tags=tag), active_tag=tag),
    )


def program_detail(request, slug):
    program = get_object_or_404(_published_programs(), slug=slug)
    # No `contacts` here on purpose: the people behind a program are our
    # records, not the directory's content.
    return render(request, "directory/program_detail.html", {"program": program})


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
