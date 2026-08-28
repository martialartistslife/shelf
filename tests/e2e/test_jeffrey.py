"""Deterministic E2E coverage for the hidden Jeffrey duck easter egg."""
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import insert_item

pytestmark = pytest.mark.e2e


def _force_random(page, value=0.05):
    page.add_init_script(
        f"""
        window.__jeffreyRandomCalls = 0;
        Math.random = function () {{
            window.__jeffreyRandomCalls += 1;
            return {value};
        }};
        """
    )


def test_jeffrey_forced_appearance_is_one_button(live_server, authed_page):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")

    expect(authed_page.locator("#jeffrey-duck")).to_have_count(1)
    assert authed_page.evaluate("window.__jeffreyRandomCalls") == 1
    assert authed_page.locator("#jeffrey-duck").get_attribute("type") == "button"
    assert authed_page.locator("#jeffrey-duck").get_attribute("aria-label") == "Jeffrey the duck"
    assert authed_page.locator("#jeffrey-duck span").get_attribute("aria-hidden") == "true"


def test_jeffrey_can_appear_on_each_allowlisted_page(live_server, authed_page):
    item_id = insert_item(
        live_server["data_dir"],
        title="Jeffrey Allowlist Probe",
        media_type="book",
        isbn="9780000000318",
    )
    _force_random(authed_page)

    for path in ["/browse", "/item/" + str(item_id), "/series", "/discover", "/stats"]:
        authed_page.goto(f"{live_server['url']}{path}")
        expect(authed_page.locator("#jeffrey-duck")).to_have_count(1)


def test_jeffrey_forced_nonappearance_rolls_once(live_server, authed_page):
    _force_random(authed_page, value=0.99)
    authed_page.goto(f"{live_server['url']}/browse")

    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0)
    assert authed_page.evaluate("window.__jeffreyRandomCalls") == 1


def test_jeffrey_is_excluded_from_ineligible_page(live_server, authed_page):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/settings")

    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0)
    assert authed_page.evaluate("window.__jeffreyRandomCalls") == 0


def test_jeffrey_mobile_target_is_at_least_44_pixels(live_server, authed_page):
    authed_page.set_viewport_size({"width": 360, "height": 640})
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/stats")

    duck = authed_page.locator("#jeffrey-duck")
    box = duck.bounding_box()
    assert box is not None
    assert box["width"] >= 44
    assert box["height"] >= 44
    assert 0 <= box["x"] <= 360 - box["width"]
    assert 0 <= box["y"] <= 640 - box["height"]


def test_jeffrey_avoids_interactive_collision(live_server, authed_page):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")

    duck = authed_page.locator("#jeffrey-duck")
    expect(duck).to_have_count(1)
    collisions = authed_page.evaluate(
        """
        (function () {
            var duck = document.getElementById('jeffrey-duck').getBoundingClientRect();
            var blockers = document.querySelectorAll(
                'a[href],button:not(#jeffrey-duck),input,select,textarea,form,label,nav,' +
                '[tabindex]:not([tabindex="-1"]),[role="button"],[role="link"],video,canvas'
            );
            return Array.from(blockers).filter(function (element) {
                var style = getComputedStyle(element);
                var rect = element.getBoundingClientRect();
                if (style.display === 'none' || style.visibility === 'hidden' ||
                    Number(style.opacity) === 0 || rect.width === 0 || rect.height === 0) {
                    return false;
                }
                return duck.left - 8 < rect.right && duck.right + 8 > rect.left &&
                    duck.top - 8 < rect.bottom && duck.bottom + 8 > rect.top;
            }).map(function (element) { return element.tagName; });
        })()
        """
    )
    assert collisions == []


@pytest.mark.parametrize("activation", ["pointer", "Enter", "Space"])
def test_jeffrey_activation_removes_it(live_server, authed_page, activation):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")
    duck = authed_page.locator("#jeffrey-duck")
    expect(duck).to_have_count(1)

    if activation == "pointer":
        duck.click()
    else:
        duck.press(activation)

    expect(duck).to_have_count(0, timeout=1_500)


def test_jeffrey_reduced_motion_skips_transform_animation(live_server, authed_page):
    authed_page.emulate_media(reduced_motion="reduce")
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")

    result = authed_page.evaluate(
        """
        (function () {
            var duck = document.getElementById('jeffrey-duck');
            duck.click();
            return {
                animation: getComputedStyle(duck).animationName,
                transform: getComputedStyle(duck).transform
            };
        })()
        """
    )
    assert result["animation"] == "none"
    assert result["transform"] == "none"
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0, timeout=1_500)


def test_jeffrey_audio_failure_still_removes_without_page_error(live_server, authed_page):
    _force_random(authed_page)
    authed_page.add_init_script(
        "window.AudioContext = function () { throw new Error('Audio blocked'); };"
    )
    page_errors = []

    def capture_page_error(error):
        page_errors.append(error)

    authed_page.on("pageerror", capture_page_error)
    authed_page.goto(f"{live_server['url']}/browse")

    authed_page.locator("#jeffrey-duck").click()
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0, timeout=1_500)
    assert page_errors == []


def test_browse_htmx_change_removes_without_reroll(live_server, authed_page):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(1)
    calls_before_swap = authed_page.evaluate("window.__jeffreyRandomCalls")

    with authed_page.expect_response(lambda response: "/api/search?" in response.url):
        authed_page.locator("select[name=sort]").select_option("title_asc")

    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0)
    assert authed_page.evaluate("window.__jeffreyRandomCalls") >= calls_before_swap


def test_jeffrey_modal_activity_removes_it_without_reroll(live_server, authed_page):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(1)

    authed_page.evaluate(
        """
        var modal = document.createElement('div');
        modal.className = 'fixed inset-0';
        modal.setAttribute('data-jeffrey-blocker', 'true');
        modal.style.cssText = 'display:block;position:fixed;inset:0;z-index:200;';
        document.body.appendChild(modal);
        """
    )
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0)


def test_store_mode_has_no_jeffrey_assets_or_behavior(live_server, authed_page):
    requests = []
    authed_page.on("request", lambda request: requests.append(request.url))
    authed_page.goto(f"{live_server['url']}/store")

    assert "jeffrey" not in authed_page.content().lower()
    assert not [url for url in requests if "jeffrey" in url.lower()]
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0)


def test_beforeprint_removes_jeffrey(live_server, authed_page):
    _force_random(authed_page)
    authed_page.goto(f"{live_server['url']}/browse")
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(1)

    authed_page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    expect(authed_page.locator("#jeffrey-duck")).to_have_count(0)
