import hashlib

from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from . import geocoding
from .addresses import MIN_QUERY_CHARS
from .forms import ProgramReferralForm, ProgramRegistrationForm
from .models import WEEKDAYS, Category, Page, Program, Tag

# How long an answer to the same question is worth keeping. Addresses do not
# move, and the second person to type "Ormond Beach" should cost nothing. An
# hour is short enough that a correction in OpenStreetMap shows up the same day
# and long enough to cover a form being filled in twice.
ADDRESS_CACHE_SECONDS = 60 * 60

# What one person's typing is allowed to ask for at once. The script waits for
# typing to stop before asking anything, so this is a ceiling on suggestions per
# request, not a budget.
ADDRESS_RESULT_LIMIT = 5


def _published_programs():
    # Every listing renders at least the first address of every program, so the
    # locations come along rather than costing a query per card. Tags bring their
    # question with them because a program page lists an answer against the
    # question it answers, and that is a query per tag otherwise.
    return (
        Program.objects.published()
        .select_related("category")
        .prefetch_related("locations", "tags__group")
    )


def _narrow(request, programs):
    """Apply the search box and every filter the query string carries.

    Every filter is a no-op when handed nothing, so this runs unconditionally and
    an absent parameter means "do not narrow by this". Each one is also read back
    into the context, because a filter you cannot see is applied to you is worse
    than no filter at all.
    """
    chosen = {
        "q": request.GET.get("q", "").strip(),
        "day": request.GET.get("day", "").strip(),
        "faith": request.GET.get("faith", "").strip(),
        "age": request.GET.get("age", "").strip(),
        "online": request.GET.get("online") == "1",
        "directpay": request.GET.get("directpay") == "1",
        "tags": [slug.strip() for slug in request.GET.getlist("tag") if slug.strip()],
    }

    if chosen["q"]:
        programs = programs.filter(
            Q(name__icontains=chosen["q"])
            | Q(host_name__icontains=chosen["q"])
            | Q(short_description__icontains=chosen["q"])
            | Q(description__icontains=chosen["q"])
            | Q(highlights__icontains=chosen["q"])
            | Q(class_names__icontains=chosen["q"])
            # Both spellings of a place: what the program typed and what it
            # resolved to. Somebody searching "Ormond" should find a program
            # that wrote "the church on Granada" and resolved to Ormond Beach.
            | Q(locations__query__icontains=chosen["q"])
            | Q(locations__label__icontains=chosen["q"])
            | Q(locations__city__icontains=chosen["q"])
            # Tags are not listed anywhere, so the search box is the main way
            # anyone reaches one. Typing "Lego" has to find the Lego programs.
            | Q(tags__name__icontains=chosen["q"])
        )

    programs = (
        programs.meeting_on(chosen["day"])
        .with_faith_basis(chosen["faith"])
        .serving_age(chosen["age"])
        .tagged(chosen["tags"])
    )
    if chosen["online"]:
        programs = programs.online()
    if chosen["directpay"]:
        programs = programs.direct_pay()
    return programs, chosen


def _listing_context(request, programs, **extra):
    """Shared context for every page that renders the program listing."""
    programs, chosen = _narrow(request, programs)
    return {
        "programs": programs.distinct(),
        "categories": Category.objects.all(),
        "query": chosen["q"],
        "filters": chosen,
        "filters_active": any(
            chosen[key] for key in ["day", "faith", "age", "online", "directpay", "tags"]
        ),
        "weekdays": WEEKDAYS,
        "faith_choices": Program.Faith.choices,
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
    category_slug = request.GET.get("category", "").strip()

    active_category = None
    if category_slug:
        active_category = get_object_or_404(Category, slug=category_slug)
        programs = programs.filter(category=active_category)

    return render(
        request,
        "directory/program_list.html",
        _listing_context(
            request,
            programs,
            active_category=active_category,
            # On this page the heading is a query parameter, so the filter form
            # has to carry it. On a category page it is in the path already and a
            # hidden copy would send it twice.
            category_in_query=bool(category_slug),
        ),
    )


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug)
    return render(
        request,
        "directory/program_list.html",
        _listing_context(
            request, _published_programs().filter(category=category), active_category=category
        ),
    )


def tag_detail(request, slug):
    """A tag's own page. Tags are found, not browsed, so this is where a tag
    link from a program page or a search result lands."""
    tag = get_object_or_404(Tag, slug=slug)
    return render(
        request,
        "directory/program_list.html",
        _listing_context(request, _published_programs().filter(tags=tag), active_tag=tag),
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


@require_GET
def address_search(request):
    """What the address picker asks as somebody types. Suggestions, as JSON.

    A proxy rather than letting the browser call Photon directly. Three reasons,
    and none of them is secrecy: the identifying User-Agent and the bias toward
    this county live in one place instead of in every visitor's browser; an
    answer can be cached, so the second person to type "New Smyrna" costs
    nothing; and if the service ever has to be swapped or switched off, one
    setting does it rather than a deploy of new JavaScript.

    A geocoder that is down is not an error here. It returns no suggestions and
    says why, and the picker quietly falls back to letting somebody add the
    address in their own words — which was always allowed anyway.
    """
    query = request.GET.get("q", "").strip()
    if len(query) < MIN_QUERY_CHARS:
        return JsonResponse({"results": []})

    # Hashed because a cache key cannot hold arbitrary text, and case-folded
    # because "DeLand" and "deland" are the same question.
    key = "address:" + hashlib.sha256(query.casefold().encode()).hexdigest()[:32]
    if (cached := cache.get(key)) is not None:
        return JsonResponse({"results": cached, "cached": True})

    try:
        places = geocoding.search(query, limit=ADDRESS_RESULT_LIMIT)
    except geocoding.GeocoderUnavailable:
        return JsonResponse({"results": [], "unavailable": True})

    results = [
        {
            "label": place.label,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "city": place.city,
            "postcode": place.postcode,
            "osm_type": place.osm_type,
            "osm_id": place.osm_id,
        }
        for place in places
    ]
    cache.set(key, results, ADDRESS_CACHE_SECONDS)
    return JsonResponse({"results": results})
