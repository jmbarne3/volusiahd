import hashlib
import re

from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from . import geocoding
from . import questions as q
from .addresses import DEBOUNCE_MS, MIN_QUERY_CHARS, parse_coordinate
from .forms import ProgramReferralForm, ProgramRegistrationForm
from .models import (
    GRADE_LEVEL_NAMES,
    GRADE_LEVELS,
    GRADE_SEARCH_TERMS,
    WEEKDAY_NAMES,
    WEEKDAYS,
    Category,
    Page,
    Program,
    Tag,
)

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


# Words that would make a search fail without telling it anything. "Art class"
# should find an art program that never calls itself a class.
SEARCH_FILLER_WORDS = frozenset(
    {"a", "an", "and", "the", "for", "of", "in", "on", "to", "with"}
    | {"class", "classes", "program", "programs"}
)


def _search_words(query):
    """The words in a search worth matching on.

    Hyphens and apostrophes stay inside a word, so "pre-k" and "K-8" survive.
    Two-word grade terms are kept together — "high school" is one term, not
    "high" and "school" — because "high" alone means something else entirely.
    A search made only of filler ("the classes") keeps its words rather than
    quietly becoming no search at all.
    """
    tokens = re.findall(r"[\w'-]+", query.lower())
    words, index = [], 0
    while index < len(tokens):
        pair = " ".join(tokens[index : index + 2])
        if pair in GRADE_SEARCH_TERMS:
            words.append(pair)
            index += 2
        else:
            words.append(tokens[index])
            index += 1
    return [w for w in words if w not in SEARCH_FILLER_WORDS] or words


def _matches_word(word):
    """Everywhere one search word can turn up — and, for a grade term, the levels
    it means. A program with no levels ticked is not matched by a grade term:
    the sidebar shows those under every level so they are never hidden, but a
    search for "teens" should return programs that said they take teens."""
    levels = GRADE_SEARCH_TERMS.get(word, [])
    by_level = Q()
    for level in levels:
        by_level |= Q(grade_levels__contains=level)
    return by_level | (
        Q(name__icontains=word)
        | Q(host_name__icontains=word)
        | Q(short_description__icontains=word)
        | Q(description__icontains=word)
        | Q(class_names__icontains=word)
        # Both spellings of a place: what the program typed and what it
        # resolved to. Somebody searching "Ormond" should find a program
        # that wrote "the church on Granada" and resolved to Ormond Beach.
        | Q(locations__query__icontains=word)
        | Q(locations__label__icontains=word)
        | Q(locations__city__icontains=word)
        # Tags are not listed anywhere, so the search box is the main way
        # anyone reaches one. Typing "Lego" has to find the Lego programs.
        | Q(tags__name__icontains=word)
    )


# What the browser's own location is called, in the box and in the summary. The
# script writes it into the box, so the template hands it the same words.
YOUR_LOCATION = "Your location"

DISTANCE_CHOICES = [5, 10, 25, 50]
DEFAULT_DISTANCE = 25


def _chosen_place(request):
    """Where a family asked to search near, as the filter reads it.

    Coordinates come first and need nobody's help: "Use my location" and a pick
    from the suggestion list both send them, so neither costs a geocoder call.
    Only a place typed and submitted without picking is looked up here, through
    the same cache the suggestion list uses.

    Coordinates are rounded to two places — about a kilometre — before anything
    else sees them. A filtered list is a link people share, and nobody's home
    address should travel inside one.
    """
    near = request.GET.get("near", "").strip()[:120]
    try:
        within = int(request.GET.get("within", DEFAULT_DISTANCE))
    except ValueError:
        within = DEFAULT_DISTANCE
    if within not in DISTANCE_CHOICES:
        within = DEFAULT_DISTANCE

    place = {"near": near, "within": within, "point": None, "place_problem": ""}
    if not near:
        return place

    latitude = parse_coordinate(request.GET.get("lat"), 90)
    longitude = parse_coordinate(request.GET.get("lng"), 180)
    if latitude is not None and longitude is not None:
        place["point"] = (round(latitude, 2), round(longitude, 2))
        return place

    if near == YOUR_LOCATION:
        place["place_problem"] = (
            "We could not tell where you are. Allow location access, or type a town."
        )
        return place

    try:
        results, _ = _places_for(near)
    except geocoding.GeocoderUnavailable:
        place["place_problem"] = (
            f"We could not look up “{near}” just now, so this list is not "
            "filtered by distance. Try again in a minute."
        )
        return place
    if not results:
        place["place_problem"] = (
            f"We could not find “{near}” on the map. Try a town name or a street address."
        )
        return place

    place["point"] = (round(results[0]["latitude"], 2), round(results[0]["longitude"], 2))
    return place


def _offers_virtual(active_category=None, active_tag=None, **_):
    """Whether this listing offers the "Virtual options available" filter.

    On the all-programs page, and on the category pages whose programs are asked
    the question. Not on a tag page, which mixes headings, and not under a
    heading where every answer would be "in person".
    """
    if active_tag:
        return False
    return active_category is None or active_category.slug in q.headings_asking("delivery")


def _narrow(request, programs, *, offers_virtual=True):
    """Apply the search box and every filter the query string carries.

    Every filter is a no-op when handed nothing, so this runs unconditionally and
    an absent parameter means "do not narrow by this". Each one is also read back
    into the context, because a filter you cannot see is applied to you is worse
    than no filter at all.
    """
    chosen = {
        "q": request.GET.get("q", "").strip(),
        # In week order, whatever order they were ticked in, and only real days,
        # so the chips and the checkboxes read back exactly what was applied.
        "days": [code for code, _ in WEEKDAYS if code in request.GET.getlist("day")],
        "faith": request.GET.get("faith", "").strip(),
        "grades": [code for code, _ in GRADE_LEVELS if code in request.GET.getlist("grade")],
        # Ignored where the filter is not offered, so an old link carrying it
        # cannot narrow a list by a control the page does not show.
        "online": offers_virtual and request.GET.get("online") == "1",
        "directpay": request.GET.get("directpay") == "1",
        "tags": [slug.strip() for slug in request.GET.getlist("tag") if slug.strip()],
        **_chosen_place(request),
    }

    # Every word has to turn up somewhere, but not together and not in order:
    # "spanish beginners" finds "Beginners Spanish", and "lego deltona" finds a
    # Lego club whose address is in Deltona. One filter per word, rather than
    # one Q, so each word gets its own join and can match a different tag or
    # location from the others.
    for word in _search_words(chosen["q"]):
        programs = programs.filter(_matches_word(word))

    programs = (
        programs.meeting_on(chosen["days"])
        .with_faith_basis(chosen["faith"])
        .at_grade_levels(chosen["grades"])
        .tagged(chosen["tags"])
    )
    if chosen["online"]:
        programs = programs.online()
    if chosen["directpay"]:
        programs = programs.direct_pay()
    return programs, chosen


def _clean_params(request, chosen):
    """The query string as this page should repeat it in its own links.

    Coordinates go back out rounded, or not at all, never as they arrived: a
    link built by echoing the request would carry somebody's exact location
    straight past the rounding in `_chosen_place`.
    """
    params = request.GET.copy()
    for key in ["lat", "lng"]:
        params.pop(key, None)
    if chosen["point"]:
        params["lat"], params["lng"] = (str(value) for value in chosen["point"])
    return params


def _without(request, chosen, *keys, drop=None):
    """This page's address with some filters taken off, for a chip's remove link.

    `keys` removes a parameter outright. `drop` is a (key, value) pair for the
    ones that repeat — a day, a tag — and removes that one value, leaving the
    rest of the parameter alone.
    """
    params = _clean_params(request, chosen)
    for key in keys:
        params.pop(key, None)
    if drop is not None:
        key, value = drop
        params.setlist(key, [kept for kept in params.getlist(key) if kept != value])
    query = params.urlencode()
    return f"{request.path}?{query}" if query else request.path


def _active_chips(request, chosen):
    """Every filter that is narrowing the list, each with a link that removes it.

    Built from what `_narrow` actually applied rather than from the raw query
    string, so a chip never claims a filter that was ignored — an age of "abc"
    filters nothing and gets no chip.
    """
    chips = []
    if chosen["q"]:
        chips.append((f"“{chosen['q']}”", _without(request, chosen, "q")))
    if chosen["near"]:
        where = "you" if chosen["near"] == YOUR_LOCATION else chosen["near"]
        chips.append(
            (
                f"Within {chosen['within']} mi of {where}",
                _without(request, chosen, "near", "lat", "lng", "within"),
            )
        )
    for day in chosen["days"]:
        chips.append((f"Meets {WEEKDAY_NAMES[day]}", _without(request, chosen, drop=("day", day))))
    for level in chosen["grades"]:
        chips.append((GRADE_LEVEL_NAMES[level], _without(request, chosen, drop=("grade", level))))
    if chosen["faith"] in Program.Faith.values:
        chips.append((Program.Faith(chosen["faith"]).label, _without(request, chosen, "faith")))
    if chosen["online"]:
        chips.append(("Virtual options available", _without(request, chosen, "online")))
    if chosen["directpay"]:
        chips.append(("Step Up direct pay", _without(request, chosen, "directpay")))
    names = dict(Tag.objects.filter(slug__in=chosen["tags"]).values_list("slug", "name"))
    for slug in dict.fromkeys(chosen["tags"]):
        chips.append((names.get(slug, slug), _without(request, chosen, drop=("tag", slug))))
    return [{"label": label, "remove_url": url} for label, url in chips]


def _listing_context(request, programs, **extra):
    """Shared context for every page that renders the program listing."""
    offers_virtual = _offers_virtual(**extra)
    programs, chosen = _narrow(request, programs, offers_virtual=offers_virtual)

    # With a place chosen the listing splits in two. Programs in range, nearest
    # first; then the ones a distance cannot be measured to, which a family
    # looking for a tutor who comes to the house still needs to see.
    unplaced = None
    if point := chosen["point"]:
        unplaced = programs.without_a_point().distinct()
        programs = programs.near(*point, chosen["within"]).order_by("distance", "name")

    # Switching category keeps every other filter. The category itself is the
    # link's own path, so it is the one parameter left behind.
    keep = _clean_params(request, chosen)
    keep.pop("category", None)

    filters_active = any(
        chosen[key] for key in ["days", "faith", "grades", "online", "directpay", "tags", "near"]
    )
    if point:
        empty_message = "Nothing in range. Try a wider distance."
    elif filters_active or chosen["q"]:
        empty_message = "Nothing matches. Try removing a filter."
    else:
        empty_message = "Nothing here yet."

    return {
        "programs": programs.distinct(),
        "unplaced": unplaced,
        "active_chips": _active_chips(request, chosen),
        "keep_query": keep.urlencode(),
        "empty_message": empty_message,
        "categories": Category.objects.all(),
        "query": chosen["q"],
        "filters": chosen,
        "filters_active": filters_active,
        "offers_virtual": offers_virtual,
        "weekdays": WEEKDAYS,
        "grade_levels": GRADE_LEVELS,
        "distance_choices": DISTANCE_CHOICES,
        "your_location": YOUR_LOCATION,
        "address_min_chars": MIN_QUERY_CHARS,
        "address_debounce": DEBOUNCE_MS,
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

    try:
        results, cached = _places_for(query)
    except geocoding.GeocoderUnavailable:
        return JsonResponse({"results": [], "unavailable": True})
    return JsonResponse({"results": results, **({"cached": True} if cached else {})})


def _places_for(query):
    """Suggestions for `query` as plain dicts, and whether they came from the cache.

    Shared by the address picker and the location filter, so a place somebody
    was offered while typing costs nothing when the filter looks it up again.
    Raises `GeocoderUnavailable`, which is never cached: a service that is down
    now may well be up for the next person.
    """
    # Hashed because a cache key cannot hold arbitrary text, and case-folded
    # because "DeLand" and "deland" are the same question.
    key = "address:" + hashlib.sha256(query.casefold().encode()).hexdigest()[:32]
    if (cached := cache.get(key)) is not None:
        return cached, True

    places = geocoding.search(query, limit=ADDRESS_RESULT_LIMIT)
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
    return results, False
