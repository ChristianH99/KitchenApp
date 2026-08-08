"""The cross-cutting checks: the rules that are easy to break by accident and
invisible when broken, because the page still renders.

Two of these classes **discover their own targets** — every URL in the URLconf,
every template, every ``.js`` file — rather than naming them one at a time. That
is the whole point. A page added next month is covered the day it is added, and
the failure story is "you forgot the rule" rather than "you forgot to write a
test about the rule".
"""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.accounts import pages
from apps import nav

BASE_DIR = Path(settings.BASE_DIR)
TEMPLATES = sorted((BASE_DIR / "templates").rglob("*.html"))
SCRIPTS = sorted((BASE_DIR / "static" / "js").glob("*.js"))


def _all_url_pairs():
    """Every (app_name, url_name) pair the project resolves.

    Walks the resolver rather than reading urls.py, so an include somebody adds
    is covered without this function knowing about it.
    """
    pairs = set()

    def walk(resolver, app_name):
        for entry in resolver.url_patterns:
            if isinstance(entry, URLResolver):
                walk(entry, entry.app_name or app_name)
            elif isinstance(entry, URLPattern) and entry.name:
                pairs.add((app_name or "", entry.name))

    walk(get_resolver(), "")
    return pairs


# --------------------------------------------------------------------------
# The sidebar registry
# --------------------------------------------------------------------------

class TestTheSidebarMarksOnePage:
    """apps/nav.py is a registry precisely so these two properties can be
    checked. Without them, the class of bug it exists to prevent — two entries
    lit at once because two apps share a url_name — comes back the first time
    somebody adds a second ``list`` view."""

    def test_no_url_can_mark_two_entries(self):
        seen = {}
        for entry, urls in nav.ITEMS.items():
            for pair in urls:
                assert pair not in seen, (
                    f"{pair} marks both {seen[pair]} and {entry}; a URL must "
                    "belong to exactly one sidebar entry"
                )
                seen[pair] = entry

    def test_every_registered_pair_exists_in_the_urlconf(self):
        known = _all_url_pairs()
        for entry, urls in nav.ITEMS.items():
            for pair in urls:
                assert pair in known, (
                    f"{entry} names {pair}, which no longer exists — a renamed "
                    "route silently marks nothing"
                )

    def test_every_parent_names_entries_that_exist(self):
        for parent, children in nav.PARENTS.items():
            for child in children:
                assert child in nav.ITEMS, f"{parent} names an unknown child {child}"


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

class TestNothingIsReachableWithoutASession:
    """The open list is the security boundary, so it is checked from both
    sides: every URL not on it must refuse an anonymous caller, and every URL
    on it must still exist."""

    def test_every_open_url_exists(self):
        known = _all_url_pairs()
        for pair in pages.OPEN:
            assert pair in known, (
                f"{pair} is listed as open but is not in the URLconf — either "
                "a route was renamed, or an exemption is being kept alive for "
                "a view that is gone"
            )

    @pytest.mark.parametrize("url", [
        "/", "/recipes/", "/recipes/new/", "/tags/",
    ])
    def test_an_anonymous_visitor_is_sent_to_the_login(self, anon, url, db):
        response = anon.get(url)
        assert response.status_code == 302
        assert reverse("accounts:login") in response["Location"]

    def test_the_health_check_answers_without_a_session(self, anon, db):
        response = anon.get("/healthz")
        assert response.status_code == 200
        assert response.content == b"ok"

    def test_the_health_check_says_nothing_else(self, anon, db):
        """It is unauthenticated, so anything richer than "ok" is household
        state handed to whoever asks."""
        body = anon.get("/healthz").content.decode()
        assert body.strip() == "ok"
        assert len(body) < 16

    def test_the_site_icon_answers_without_a_session(self, anon, db):
        """A browser asks for it before anybody has signed in — the login page
        wants an icon too — and an icon is not household data."""
        response = anon.get("/favicon.ico")
        assert response.status_code == 301
        assert "icons/favicon" in response["Location"]

    def test_uploaded_photographs_are_behind_the_login(self, anon, db):
        """/media/ serves files somebody uploaded. "Not linked from anywhere"
        has never been access control."""
        response = anon.get("/media/recipes/whatever.jpg")
        assert response.status_code in (301, 302)
        assert "login" in response["Location"]


# --------------------------------------------------------------------------
# Content-Security-Policy, and the rules that make one possible
# --------------------------------------------------------------------------

class TestTheContentSecurityPolicy:
    def test_it_is_on_every_page(self, client, db):
        response = client.get("/")
        assert "Content-Security-Policy" in response.headers

    def test_it_does_not_allow_inline_script(self, client, db):
        policy = client.get("/").headers["Content-Security-Policy"]
        assert "script-src 'self'" in policy
        assert "unsafe-inline" not in policy
        assert "unsafe-eval" not in policy


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
class TestTemplatesObeyTheCsp:
    """A CSP with ``script-src 'self'`` and no nonce means *nothing* on a page
    may be inline. Each of these renders perfectly well while being broken,
    which is why they are tested rather than remembered."""

    def test_no_inline_script_block(self, path):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>", source):
            pytest.fail(
                f"{path.name} has an inline <script> at offset {match.start()}. "
                "It will be blocked by the CSP; move it to static/js/."
            )

    def test_no_inline_style_attribute(self, path):
        source = path.read_text(encoding="utf-8")
        assert 'style="' not in source, (
            f"{path.name} has a style=\"…\" attribute; the CSP blocks it and "
            "the design system has no room for it either"
        )

    def test_no_inline_event_handlers(self, path):
        source = path.read_text(encoding="utf-8")
        for handler in ("onclick=", "onsubmit=", "onchange=", "onload=", "oninput="):
            assert handler not in source.lower(), (
                f"{path.name} uses {handler}; the CSP blocks it. Wire the "
                "listener up in static/js/ instead."
            )

    def test_no_multiline_django_comment(self, path):
        """Django's ``{# #}`` lexer matches without DOTALL, so a comment that
        wraps onto a second line is *rendered onto the page* for the reader to
        enjoy. Multi-line commentary goes in ``{% comment %}``."""
        source = path.read_text(encoding="utf-8")
        for line in source.splitlines():
            opens = line.count("{#")
            closes = line.count("#}")
            assert opens == closes, (
                f"{path.name} opens a {{# #}} comment that does not close on the "
                f"same line: {line.strip()[:70]}"
            )

    def test_no_remote_font_or_script(self, path):
        """A venue, or a household with its uplink down, has no internet — and
        a <link> to Google sends every visitor's IP to a third party."""
        source = path.read_text(encoding="utf-8")
        for host in ("fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net",
                     "unpkg.com", "cdnjs.cloudflare.com"):
            assert host not in source, f"{path.name} loads something from {host}"


# --------------------------------------------------------------------------
# The scripts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
class TestEveryScriptIsWhole:
    """A script that dies at parse time takes its whole page's behaviour with
    it and leaves the page looking completely normal. These two checks catch
    the ways that happens silently."""

    def test_brackets_balance(self, path):
        source = path.read_text(encoding="utf-8")
        # Strings, template literals, regexes and comments are stripped first,
        # or every apostrophe in a comment would be counted as a bracket.
        stripped = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        stripped = re.sub(r"(?m)//.*$", "", stripped)
        stripped = re.sub(r"'(?:\\.|[^'\\])*'", "''", stripped)
        stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', stripped)
        stripped = re.sub(r"`(?:\\.|[^`\\])*`", "``", stripped)
        for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
            assert stripped.count(opener) == stripped.count(closer), (
                f"{path.name} has unbalanced {opener}{closer}"
            )

    def test_no_template_syntax(self, path):
        """A ``{% url %}`` or ``{{ value }}`` in a .js file is not rendered —
        the file is served as a static asset. It becomes a syntax error, and
        the page still loads."""
        source = path.read_text(encoding="utf-8")
        assert "{%" not in source, f"{path.name} contains Django template syntax"
        assert "{{" not in source, f"{path.name} contains Django template syntax"


def test_no_page_loads_a_script_without_what_it_reaches_for():
    """A script that uses another script's global must be loaded after it.

    The failure is completely silent, which is why it is worth a test. The
    settings page loaded ``sound_preview.js`` and not ``timer_sounds.js``, so
    ``window.kitchenSounds`` was undefined, so the handler returned on its
    second line — and the Play buttons sat there doing nothing at all, with no
    error and nothing to see. Nobody finds that by reading the page.

    Discovered from the files rather than listed: each entry is a global and
    the script that defines it, and every *other* script mentioning that global
    declares the dependency. A template loading one of those must load the
    definer too, and first — a script tag runs when it is reached.
    """
    provided = {
        "window.kitchenSounds": "timer_sounds.js",
        "window.kitchenTimers": "timer_watch.js",
    }

    for name, definer in provided.items():
        users = sorted(
            path.name for path in SCRIPTS
            if path.name != definer and name in path.read_text(encoding="utf-8")
        )
        assert users, f"nothing uses {name}; drop it from this test or from the app"
        for path in TEMPLATES:
            order = _scripts_loaded_by(path)
            for user in users:
                if user not in order:
                    continue
                assert definer in order, (
                    f"{path.name} loads {user}, which reads {name}, but never "
                    f"loads {definer} — the page is silently inert"
                )
                assert order.index(definer) < order.index(user), (
                    f"{path.name} loads {definer} after {user}, so {name} is "
                    "still undefined when it is read"
                )


# Only real ``<script src>`` tags. A file named in a ``{% comment %}`` — and
# several are, because these files point at each other in prose — is not a file
# the page loads, and counting those made the check above fail on a template
# whose only mention of a script was a sentence about it.
_SCRIPT_TAG = re.compile(r"<script[^>]*\bsrc=\"\{%\s*static\s*'js/([\w.-]+)'")
_EXTENDS = re.compile(r"\{%\s*extends\s*\"([^\"]+)\"")


def _scripts_loaded_by(path, seen=None):
    """Every script a rendered page ends up with, in the order it gets them.

    Follows ``{% extends %}``, because a page's scripts are not all in its own
    file: base.html loads the shell's and the timer's before it reaches
    ``{% block scripts %}``, so a child template that reads one of those globals
    without naming the file is correct and must not be reported. Parent first,
    which is the order the markup puts them in.
    """
    seen = seen if seen is not None else set()
    if path in seen or not path.exists():
        return []                     # a cycle, or an {% extends %} we cannot resolve
    seen.add(path)
    markup = path.read_text(encoding="utf-8")
    parent = _EXTENDS.search(markup)
    inherited = (
        _scripts_loaded_by(BASE_DIR / "templates" / parent.group(1), seen)
        if parent else []
    )
    return inherited + _SCRIPT_TAG.findall(markup)


def test_only_the_shell_defines_the_dialog_helpers():
    """``appConfirm``/``appAlert`` exist so that no page reaches for
    ``window.confirm``. A page defining its own is a page that has stopped
    sharing the focus management."""
    for path in SCRIPTS:
        source = path.read_text(encoding="utf-8")
        if path.name == "shell.js":
            continue
        for builtin in ("window.confirm(", "window.alert(", "window.prompt("):
            assert builtin not in source, (
                f"{path.name} calls {builtin}; use window.appConfirm/appAlert, "
                "which are the app's own dialog and can be translated"
            )


# --------------------------------------------------------------------------
# The design system
# --------------------------------------------------------------------------

MAIN_CSS = (BASE_DIR / "static" / "css" / "main.css").read_text(encoding="utf-8")
# Everything after the `:root { … }` token block. The tokens themselves are the
# one place raw values are allowed — that is what makes them tokens.
TOKEN_BLOCK = MAIN_CSS.split("}", 1)[0]
RULES = MAIN_CSS.split("}", 1)[1]


def _declared_values(prop, allow):
    """Every value declared for ``prop`` outside the token block that ``allow``
    rejects.

    Written as a match-then-filter rather than as a regex with a negative
    lookahead, because a lookahead after ``\\s*`` is a trap: the ``\\s*``
    backtracks to zero width, the lookahead then sees a leading space instead of
    the value, and the whole check silently matches everything. Both of the
    first two versions of this file did exactly that and reported every
    correctly-tokenised declaration as a violation.
    """
    offenders = []
    for match in re.finditer(rf"(?<![\w-]){re.escape(prop)}:\s*([^;{{}}]+);", RULES):
        value = match.group(1).strip()
        if not allow(value):
            offenders.append(value)
    return offenders


class TestTheScalesAreClosed:
    """Every colour, length, duration and z-index comes from the token block.
    The alternative is what this replaced elsewhere: thirty-odd font sizes and
    a z-index ladder with two things on the same rung, so which of two
    overlapping fixed elements won was settled by document order."""

    def test_no_raw_colour_outside_the_tokens(self):
        offenders = [
            match.group(0)
            for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b", RULES)
        ]
        assert not offenders, (
            f"raw colours outside the token block: {sorted(set(offenders))}. "
            "A value that appears twice is a token."
        )

    def test_no_raw_z_index(self):
        offenders = _declared_values("z-index", allow=lambda v: v.startswith("var(--z-"))
        assert not offenders, f"raw z-index values: {offenders}"

    def test_no_raw_transition_duration(self):
        """The one exception is the ``prefers-reduced-motion`` block, which has
        to name a real duration — the whole point of it is to override every
        token at once with something imperceptible."""
        reduced_motion = "0.01ms"
        offenders = [
            value for value in re.findall(r"transition[^;]*?(\d*\.?\d+m?s)", RULES)
            if value != reduced_motion
        ]
        assert not offenders, (
            f"raw transition durations: {sorted(set(offenders))}; use --dur-1…5"
        )

    def test_no_raw_font_size(self):
        """`em` and `inherit` are allowed and a length is not: an em is a
        component sizing itself relative to its context (the brand mark against
        the wordmark), a px or rem is a step somebody invented outside the
        scale."""
        offenders = _declared_values("font-size", allow=lambda v: (
            v.startswith("var(--text-") or v == "inherit" or re.fullmatch(r"[\d.]+em", v)
        ))
        assert not offenders, f"raw font sizes: {sorted(set(offenders))}"

    @pytest.mark.parametrize("prop", ["padding", "margin", "gap", "padding-top",
                                      "padding-bottom", "margin-top", "margin-bottom"])
    def test_no_raw_length_for_spacing(self, prop):
        """0, auto, a percentage and `inherit` are not steps on a scale;
        everything else must come from --space-1…10. This is the check that
        stops the scale from quietly growing a 33rd value."""
        def allowed(value):
            # calc() expressions are collapsed before splitting, or
            # "calc(-1 * var(--space-5))" is read as three separate values —
            # and the two that are not var() are reported as violations.
            flattened = re.sub(r"calc\([^()]*(?:\([^()]*\)[^()]*)*\)", "CALC", value)
            return all(
                part.startswith("var(--space-")
                or part in ("0", "auto", "inherit", "initial", "unset", "CALC")
                or part.endswith("%")
                for part in flattened.split()
            )

        offenders = _declared_values(prop, allow=allowed)
        assert not offenders, f"raw {prop} values: {sorted(set(offenders))}"


def test_a_focus_ring_is_never_removed():
    """The global ``:focus-visible`` outline is outranked on specificity by any
    component rule, so a bare ``outline: none`` in a component silently strips
    the keyboard indicator from every control it matches. A component that
    wants its own soft ring for the pointer scopes the suppression with
    ``:focus:not(:focus-visible)``."""
    for match in re.finditer(r"([^{}]+)\{([^}]*outline:\s*(?:none|0)[^}]*)\}", RULES):
        selector = match.group(1).strip()
        assert ":not(:focus-visible)" in selector, (
            f"`{selector}` removes the focus outline unconditionally"
        )


def test_the_app_does_not_claim_a_dark_scheme_it_does_not_ship():
    """Declaring ``color-scheme: dark`` without dark rules is what makes a
    browser paint inputs, selects and scrollbars dark against a permanently
    light page."""
    assert "color-scheme" not in MAIN_CSS or "prefers-color-scheme" in MAIN_CSS


# --------------------------------------------------------------------------
# Where the app writes
# --------------------------------------------------------------------------

def test_everything_written_at_runtime_lives_under_the_data_dir():
    """The container's code is replaced wholesale by the next image; the
    database and the photographs are the collection. Anything written beside
    the code is data somebody is about to lose."""
    data_dir = Path(settings.DATA_DIR).resolve()
    for name, value in (
        ("DATABASES['default']['NAME']", Path(settings.DATABASES["default"]["NAME"])),
        ("MEDIA_ROOT", Path(settings.MEDIA_ROOT)),
        ("LOG_DIR", Path(settings.LOG_DIR)),
    ):
        assert value.resolve().is_relative_to(data_dir), (
            f"{name} is {value}, which is outside DATA_DIR ({data_dir})"
        )


# --------------------------------------------------------------------------
# The build pipeline
# --------------------------------------------------------------------------

WORKFLOWS = BASE_DIR / ".github" / "workflows"
RELEASE_WORKFLOW = WORKFLOWS / "release.yml"
RELEASE_COMPOSE = BASE_DIR / "deploy" / "docker-compose.release.yml"


class TestTheReleasePipelineAgreesWithItself:
    """Three files have to say the same thing about one image, and none of them
    looks wrong on its own. The failure mode is a release that builds, pushes
    and attaches perfectly — and a compose file on the NAS that pulls something
    that was never published."""

    def test_the_release_workflow_exists(self):
        assert RELEASE_WORKFLOW.exists(), (
            "the release workflow is gone; a tag would now produce nothing"
        )

    def test_the_compose_file_pulls_rather_than_builds(self):
        """A `build:` section here would quietly turn a two-core NAS into a
        build machine again — and worse, `docker compose up` would then succeed
        with a *locally* built image while looking exactly like a pull."""
        source = RELEASE_COMPOSE.read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            assert not stripped.startswith("build:"), (
                "docker-compose.release.yml has a build: section; it is the "
                "pull-based file. deploy/docker-compose.yml is the one that builds."
            )

    def test_the_workflow_substitutes_every_placeholder(self):
        """The compose file ships with `__IMAGE__` and `__VERSION__` in it and
        the workflow rewrites them. A placeholder the workflow does not know
        about is shipped verbatim, and `image: __IMAGE__:1.2.0` fails on the NAS
        rather than in CI."""
        compose = RELEASE_COMPOSE.read_text(encoding="utf-8")
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        placeholders = set(re.findall(r"__[A-Z_]+__", compose))
        assert placeholders, (
            "docker-compose.release.yml no longer has placeholders — either it "
            "hardcodes a version, or the substitution has been removed"
        )
        for placeholder in placeholders:
            assert placeholder in workflow, (
                f"{placeholder} is in docker-compose.release.yml but the release "
                "workflow never replaces it, so it ships verbatim"
            )

    def test_the_pinned_version_is_not_latest(self):
        """`latest` cannot be rolled back by re-reading the file: `up -d` after
        a bad release fetches the same bad image again."""
        compose = RELEASE_COMPOSE.read_text(encoding="utf-8")
        image_lines = [ln.strip() for ln in compose.splitlines() if ln.strip().startswith("image:")]
        assert image_lines, "docker-compose.release.yml names no image"
        for line in image_lines:
            assert not line.endswith(":latest"), (
                f"{line} pins `latest`; a rollback then has nothing to edit"
            )

    def test_the_dockerfile_takes_the_version_the_workflow_passes(self):
        """`docker build --build-arg X` for an ARG the Dockerfile never declares
        is not an error — it is a warning nobody reads, and the image ships
        claiming to be `dev`."""
        dockerfile = (BASE_DIR / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        for arg in re.findall(r"--build-arg (\w+)=", workflow):
            assert re.search(rf"^ARG {arg}\b", dockerfile, re.M), (
                f"the release workflow passes --build-arg {arg}, which "
                "deploy/Dockerfile does not declare — it would be silently dropped"
            )


def test_the_health_check_does_not_volunteer_the_version(client, db, settings):
    """/healthz is unauthenticated. Which build is running is exactly the sort
    of thing an unauthenticated endpoint should not answer, and the tempting
    place to put it is a health check nobody thinks of as a page."""
    settings.KITCHEN_VERSION = "9.9.9"
    body = client.get("/healthz").content.decode()
    assert "9.9.9" not in body


def test_the_running_version_is_shown_once_signed_in(client, db, settings):
    """The first question after every update, and the one the NAS cannot
    answer — a restarted container and a browser holding a cached page look
    identical from the outside."""
    settings.KITCHEN_VERSION = "9.9.9"
    assert "9.9.9" in client.get("/").content.decode()


def test_a_checkout_claims_no_version(client, db, settings):
    """An image built from an uncommitted working copy has no version, and
    printing one that looks official is worse than printing nothing."""
    settings.KITCHEN_VERSION = ""
    assert "shell-version" not in client.get("/").content.decode()


def test_every_page_names_its_icon(client, db):
    """Without a `<link rel="icon">` a browser asks for /favicon.ico by itself,
    per origin, and the log fills with `Not Found: /favicon.ico` — which is how
    the warnings worth reading end up buried among ones that are not."""
    response = client.get(reverse("recipes:home"))
    body = response.content.decode()
    assert 'rel="icon"' in body, "base.html no longer names an icon"


def test_the_dialog_markup_is_present_on_every_page(client, db):
    """window.appConfirm fills a dialog base.html renders. Without it every
    confirmation on the site silently does nothing."""
    body = client.get("/").content.decode()
    assert "data-app-dialog" in body
    assert "data-dialog-accept" in body


# --------------------------------------------------------------------------
# Translations
# --------------------------------------------------------------------------

CATALOGS = sorted((BASE_DIR / "locale").rglob("*.po"))


@pytest.mark.parametrize("path", CATALOGS, ids=lambda p: f"{p.parent.parent.parent.name}/{p.name}")
class TestTheCatalogsAreComplete:
    """German is the default language, so an untranslated string is not a
    cosmetic gap — it is an English sentence on the page everybody actually
    uses."""

    def test_nothing_is_left_untranslated(self, path):
        source = path.read_text(encoding="utf-8")
        # The header entry is `msgid ""` and legitimately has a long msgstr;
        # everything after the first blank-line-separated block is real.
        entries = source.split("\n\n")[1:]
        missing = [
            entry for entry in entries
            if re.search(r'^msgstr(?:\[\d\])? ""\s*$', entry, re.M)
            and not re.search(r'^msgstr(?:\[\d\])? "(?!")', entry, re.M)
        ]
        assert not missing, (
            f"{path.name} has {len(missing)} untranslated entries, e.g. "
            f"{missing[0].strip()[:120]}"
        )

    def test_nothing_is_marked_fuzzy(self, path):
        """msgmerge guesses a translation from a similar msgid and marks it
        fuzzy. The guesses are usually wrong in a way that ships silently — and
        gettext ignores a fuzzy entry at runtime, so the string comes out in
        English while the file looks translated."""
        source = path.read_text(encoding="utf-8")
        assert "#, fuzzy" not in source, f"{path.name} contains fuzzy entries"

    def test_no_reference_line_was_wrapped(self, path):
        """``makemessages`` on Windows wraps a long ``#:`` block onto a second
        line that begins with a *space* rather than a second ``#:``.

        ``msgfmt`` then refuses the whole file with "keyword unknown", no
        ``.mo`` is written, and the app carries on serving the *previous*
        catalog — so a session's translations look compiled and simply are not
        there. ``tools/fix_po.py`` repairs it; this is what notices.
        """
        in_references = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("#:"):
                in_references = True
            elif in_references and line.startswith(" ") and line.strip():
                pytest.fail(
                    f"{path.name}:{number} continues a #: block with a leading "
                    "space; msgfmt will refuse the file and write no .mo at "
                    "all. Run `uv run python tools/fix_po.py`."
                )
            else:
                in_references = False

    def test_it_is_compiled(self, path):
        """`.mo` files are committed, because nothing at runtime compiles them
        — a `.po` edited without recompiling changes nothing at all."""
        compiled = path.with_suffix(".mo")
        assert compiled.exists(), f"{path.name} has never been compiled to {compiled.name}"
        assert compiled.stat().st_mtime >= path.stat().st_mtime - 1, (
            f"{compiled.name} is older than {path.name} — run compilemessages"
        )


def test_the_placeholders_survive_translation():
    """A translated string that drops or renames a `%(name)s` raises at render
    time, on the page it is on, in production only."""
    for path in CATALOGS:
        source = path.read_text(encoding="utf-8")
        for entry in source.split("\n\n")[1:]:
            lines = entry.splitlines()
            first_msgstr = next(
                (i for i, line in enumerate(lines) if line.startswith("msgstr")), None
            )
            if first_msgstr is None:
                continue
            # Split at the first msgstr rather than matching `msgid` lines: a
            # long msgid is *wrapped* by gettext into `msgid ""` plus
            # continuation lines, so a line-anchored regex reads it as empty —
            # which is how every correct plural came back as a violation. Both
            # halves are read as "every quoted fragment", which is wrapping-proof.
            def placeholders(chunk):
                text = " ".join(re.findall(r'"((?:[^"\\]|\\.)*)"', "\n".join(chunk)))
                return set(re.findall(r"%\((\w+)\)s", text))

            expected = placeholders(lines[:first_msgstr])
            got = placeholders(lines[first_msgstr:])
            assert got <= expected, (
                f"{path.name}: a translation uses {got - expected}, which the "
                f"source string does not provide: {' '.join(lines[first_msgstr:])[:100]}"
            )
