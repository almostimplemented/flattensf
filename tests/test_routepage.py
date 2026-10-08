"""End-to-end test of the route page (the shareable map).

Drives ``outputs/sf_flat_route_finder.html`` in a headless browser: the page
must load without errors, route its default trip, answer searches for an
intersection, an address and a park, and produce a route family whose ends
are what the slider labels promise -- the left end is the true shortest
path and the right end has the least climbing. One member is also checked
against Python under the same scaled weights, so the slider positions
cannot drift from the analysis.

Skipped unless Playwright, a Chromium build and a built page are present.
"""
from __future__ import annotations

import glob
import os

import pytest

from sf_flat_routes.config import PROCESSED_DIR
from sf_flat_routes.viz_interactive import SIMPLE_HTML, SITE_INDEX

playwright = pytest.importorskip("playwright.sync_api",
                                 reason="playwright is not installed")


def _chromium() -> str | None:
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    os.path.expanduser(
                        "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


pytestmark = [
    pytest.mark.skipif(not SIMPLE_HTML.exists(),
                       reason="route page not built; run `python -m sf_flat_routes map`"),
    pytest.mark.skipif(not (PROCESSED_DIR / "edges_directed.parquet").exists(),
                       reason="processed data not built"),
]

_SEARCHES = ["24th & mission", "1234 valencia", "golden gate park", "ferry building",
             "church st and 24th st", "coit tower", "ocean beach", "caltrain",
             "geary blvd & 25th ave", "25th avenue and geary boulevard", "cabrillo st & 38th ave",
             "geary blvd"]

_SCRIPT = """(queries) => {
    const fam = App.family, g = App.graph;
    const alphas = [0, 14, 120];
    const members = fam.unique.map(u => ({
        id: u.id, distance_m: u.stats.distance_m, gain: u.stats.elev_gain_m,
        arcs: u.arcs.map(a => [g.arcEdge[a], (g.arcFlags[a] & 4) ? 1 : 0]),
        costs: alphas.map(a => g.pathCost(u.arcs, App.state.mode, App.weights(a))),
    }));
    const search = {};
    for (const q of queries) search[q] = App.index.search(q).map(r => [r.name, r.kind]);
    const sl = document.getElementById('sl');
    sl.value = 0; sl.dispatchEvent(new Event('input'));
    const atZero = App.shown.id;
    sl.value = 1; sl.dispatchEvent(new Event('input'));
    const atOne = App.shown.id;
    sl.value = 0.5; sl.dispatchEvent(new Event('input'));
    const half = App.shown.id;
    return {
        from: App.state.from, to: App.state.to, members, alphas,
        search, atZero, atOne, half, hash: location.hash,
        places: App.index.places.length, intersections: App.index.intersections.length,
        hasAddresses: !!App.index.addr,
        frontier: { solutions: App._search.solutions.length, labels: App._search.labels,
                    expanded: App._search.expanded, truncated: App._search.truncated },
        snap: (() => { const p = App.pointAt(-122.58, 37.76); const g = App.graph;
            return p ? { node: p.node, pinLon: p.lon, pinLat: p.lat,
                         nodeLon: g.nodeLon(p.node), nodeLat: g.nodeLat(p.node) } : { node: -1 }; })(),
    };
}"""


@pytest.fixture(scope="module")
def page_results():
    from playwright.sync_api import sync_playwright
    errors: list = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=_chromium(),
                                         args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:                            # pragma: no cover
            pytest.skip(f"no usable Chromium: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and "fonts.g" not in m.text
                and "ERR_" not in m.text else None)
        page.goto(SIMPLE_HTML.resolve().as_uri(), wait_until="load", timeout=240_000)
        page.wait_for_function(
            "window.App && App.family && !App.family.partial"
            " && !document.getElementById('result').hidden",
            timeout=240_000)
        out = page.evaluate(_SCRIPT, _SEARCHES)
        # change the destination without touching the slider: the line on
        # the map must be the new trip's, not the old one's
        page.evaluate("""() => {
            const hit = App.index.search('coit tower')[0];
            window._hit = hit;
            App.setPoint('to', App.pointAt(hit.lon, hit.lat, hit.name), false);
            App.recompute('auto');
        }""")
        page.wait_for_function("App.family && !App.family.partial", timeout=120_000)
        out["retarget"] = page.evaluate("""() => {
            const hit = window._hit, before = 0;
            const u = App.shown, line = App._line.getLatLngs();
            const end = line[line.length - 1];
            return { before, after: line.length, sameAsShown: line.length === u.latlngs.length,
                     member: App.family.unique.includes(u),
                     endsAtCoit: Math.abs(end.lat - hit.lat) < 0.004 && Math.abs(end.lng - hit.lon) < 0.004 };
        }""")
        # A-to-B bike distance is physical even when a shared link retains
        # the calm preference, which now applies only to loops.
        page.evaluate("""() => {
            document.querySelector('#mode button[data-v=bike]').click();
            App.setPoint('from', App.pointAt(-122.4375, 37.7747, 'Divisadero & Hayes'), false);
            App.setPoint('to', App.pointAt(-122.4435, 37.8060, 'Marina Green'), false);
            App.recompute('auto');
        }""")
        page.wait_for_function("App.family && !App.family.partial", timeout=120_000)
        streets = """() => {
            const g = App.graph, u = App.family.shortest, km = {};
            for (const a of u.arcs) {
                const n = App.geom.edgeInfo(g.arcEdge[a]).name || '?';
                km[n] = (km[n] || 0) + g.arcLen[a] / g.DM;
            }
            return { streets: km, distance_m: u.stats.distance_m, stress_m: u.stats.stress_m,
                     calm: App.state.calm, token: App.token(), n: App.family.unique.length,
                     rowHidden: document.getElementById('calmrow').hidden,
                     monotone: App.family.unique.every((m, i, arr) => i === 0
                        || (m.stats[App.lenKey()] >= arr[i - 1].stats[App.lenKey()] - 1e-6
                            && m.stats.elev_gain_m <= arr[i - 1].stats.elev_gain_m + 1e-6)) };
        }"""
        out["calm_on"] = page.evaluate(streets)
        page.evaluate("""() => {
            App.state.calm = false;
            document.getElementById('calm').checked = false;
            App.recompute(false);
        }""")
        page.wait_for_function("App.family && !App.family.partial && !App.state.calm", timeout=120_000)
        out["calm_off"] = page.evaluate(streets)
        # loop mode: the engine's loops, then the toggle, then a shared link
        out["loops"] = page.evaluate("""() => {
            const g = App.graph, src = App.nearestNode(-122.42713, 37.75972);
            const s = g.loops(src, 'walk', { targetM: 5 * 1609.344 });
            while (!s.step(1e9)) {}
            const tail = g.reverse().tail;
            return { src, ms: s.ms, accepted: s.accepted.length, median: s.medianGain,
              loops: s.loops.map(r => ({ length: r.length, gain: r.gain, overlap: r.overlap, round: r.round,
                first: tail[r.arcs[0]], last: g.head[r.arcs[r.arcs.length - 1]],
                connected: r.arcs.every((a, i) => i === 0 || tail[a] === g.head[r.arcs[i - 1]]) })) };
        }""")
        out["outback"] = page.evaluate("""() => {
            const g = App.graph, src = App.nearestNode(-122.4435, 37.8060);   // Marina Green
            const run = (opts) => { const s = g.loops(src, 'walk', Object.assign({ targetM: 4 * 1609.344 }, opts));
              while (!s.step(1e9)) {} return s.loops.map(r => ({ gain: r.gain, kind: r.kind, overlap: r.overlap, length: r.length })); };
            return { loops: run({}), ob: run({ outBack: true }) };
        }""")
        page.evaluate("""() => {
            document.querySelector('#mode button[data-v=walk]').click();
            App.setPoint('from', App.pointAt(-122.4113, 37.7604, 'Trick Dog'), false);
            App.setPoint('to', App.pointAt(-122.4435, 37.8060, 'Marina Green'), false);
            App.recompute('auto');
        }""")
        page.wait_for_function("App.family && !App.family.partial", timeout=120_000)
        page.click("#loopbtn")
        page.wait_for_function("App.family && App.family.loop", timeout=60_000)
        page.wait_for_timeout(400)
        out["loop_ui"] = page.evaluate("""() => ({
            looping: document.getElementById('card').classList.contains('looping'),
            toWidth: document.getElementById('tofield').getBoundingClientRect().width,
            pressed: document.getElementById('loopbtn').getAttribute('aria-pressed'),
            slMax: +document.getElementById('sl').max, hash: location.hash,
            stats: App.shown.stats.distance_m, target: App.family.targetM,
            delta: document.getElementById('delta').textContent,
            markers: App.markers.getLayers().length })""")
        page.click("#loopbtn")
        page.wait_for_function("App.family && !App.family.loop && !App.family.partial", timeout=120_000)
        page.wait_for_timeout(400)
        out["loop_off"] = page.evaluate("""() => ({ to: document.getElementById('to').value,
            toWidth: document.getElementById('tofield').getBoundingClientRect().width,
            hash: location.hash, slMax: +document.getElementById('sl').max })""")
        # a shared loop link reopens the same loop
        page.goto(SIMPLE_HTML.resolve().as_uri() + "#l~-122.42713~37.75972~w~3.5~1~Dolores_20Park",
                  wait_until="load", timeout=240_000)
        page.reload(wait_until="load", timeout=240_000)
        page.wait_for_function("window.App && App.family && App.family.loop", timeout=240_000)
        # the GPX track and the units toggle
        out["gpx"] = page.evaluate("""() => { const g = App.gpx(); const pts = g.match(/<trkpt /g) || [];
            const eles = [...g.matchAll(/<ele>([-\\d.]+)<\\/ele>/g)].map(m => +m[1]);
            return { points: pts.length, latlngs: App.shown.latlngs.length, name: (g.match(/<name>(.*?)<\\/name>/) || [])[1],
              eleOk: eles.every(Number.isFinite), eleMin: Math.min(...eles), eleMax: Math.max(...eles),
              header: g.startsWith('<?xml version="1.0"'), closed: g.trim().endsWith('</gpx>') }; }""")
        page.click("#units button[data-u=km]"); page.wait_for_timeout(300)
        out["units_km"] = page.evaluate("""() => ({ dist: document.getElementById('v_dist').textContent, climb: document.getElementById('v_climb').textContent,
            delta: document.getElementById('delta').textContent, slMax: document.getElementById('sl').max, slpos: document.getElementById('slpos').textContent,
            end1: document.getElementById('end1').textContent, mi: App.state.loopMi, stored: localStorage.getItem('flattensf.units') })""")
        page.click("#units button[data-u=mi]"); page.wait_for_timeout(300)
        out["units_mi"] = page.evaluate("""() => ({ dist: document.getElementById('v_dist').textContent, slMax: document.getElementById('sl').max,
            stored: localStorage.getItem('flattensf.units') })""")
        out["loop_link"] = page.evaluate("""() => ({ loop: App.state.loop, mi: App.state.loopMi,
            idx: App.state.loopIdx, from: document.getElementById('from').value,
            n: App.family.unique.length, slVal: +document.getElementById('sl').value })""")
        browser.close()
    return out, errors


def test_loops_close_on_themselves_and_are_flat(page_results):
    out, _ = page_results
    r = out["loops"]
    assert r["accepted"] >= 10 and len(r["loops"]) >= 2, r
    target = 5 * 1609.344
    for lp in r["loops"]:
        assert lp["first"] == r["src"] and lp["last"] == r["src"] and lp["connected"], lp
        assert abs(lp["length"] - target) <= 0.12 * target, lp
        assert lp["overlap"] <= 0.3 and lp["round"] >= 0.2, lp
    # the flattest loop climbs well under what a typical loop from here does
    assert r["loops"][0]["gain"] < 0.75 * r["median"], r
    assert r["ms"] < 5000, r


def test_out_and_backs_are_offered_only_when_allowed_and_are_flatter(page_results):
    out, _ = page_results
    r = out["outback"]
    assert all(lp["kind"] != "outback" for lp in r["loops"])
    best = r["ob"][0]
    # from the Marina the flattest run is the promenade there and back
    # (overlap counts the second pass over a street, so there and back is 0.5)
    assert best["kind"] == "outback" and best["overlap"] > 0.4, best
    assert best["gain"] < 0.5 * r["loops"][0]["gain"], (best, r["loops"][0])
    assert abs(best["length"] - 4 * 1609.344) <= 0.25 * 1609.344


def test_the_gpx_track_carries_every_point_with_an_elevation(page_results):
    out, _ = page_results
    g = out["gpx"]
    assert g["header"] and g["closed"], g
    assert g["points"] == g["latlngs"] > 100, g
    assert g["eleOk"] and 0 <= g["eleMin"] < g["eleMax"] < 300, g
    assert "loop from" in g["name"], g


def test_units_switch_to_kilometres_and_back(page_results):
    out, _ = page_results
    km, mi = out["units_km"], out["units_mi"]
    assert km["dist"].endswith("km") and km["climb"].endswith("m") and " m of climbing" in km["delta"], km
    # the loop slider runs 2 to 24 km in km mode; the target itself is unchanged
    assert km["slMax"] == "24" and km["end1"] == "24 km" and km["slpos"].endswith("km loop"), km
    assert km["stored"] == "km"
    assert mi["dist"].endswith("mi") and mi["slMax"] == "15" and mi["stored"] == "mi", mi


def test_the_loop_button_folds_the_destination_away_and_back(page_results):
    out, errors = page_results
    on, off, link = out["loop_ui"], out["loop_off"], out["loop_link"]
    assert not errors, errors[:4]
    assert on["looping"] and on["pressed"] == "true" and on["toWidth"] < 2
    assert on["slMax"] == 15 and on["hash"].startswith("#l~")
    assert on["markers"] == 1
    assert abs(on["stats"] - on["target"]) <= 0.15 * on["target"]
    assert "loop" in on["delta"]
    assert off["to"] == "Marina Green" and off["toWidth"] > 100
    assert off["slMax"] == 1 and off["hash"].startswith("#t~")
    assert link["loop"] and link["mi"] == 3.5 and link["from"] == "Dolores Park"
    assert link["slVal"] == 3.5 and link["idx"] == min(1, link["n"] - 1)


def test_bike_trip_distance_is_physical_regardless_of_saved_calm_preference(page_results):
    out, _ = page_results
    on, off = out["calm_on"], out["calm_off"]
    assert on["calm"] and not off["calm"]
    assert on["rowHidden"] and off["rowHidden"]
    assert on["token"].split("~")[5] == "b" and off["token"].split("~")[5] == "bx"
    assert off["streets"].get("Divisadero Street", 0) > 3000
    assert on["streets"] == off["streets"]
    assert on["distance_m"] == off["distance_m"]
    assert on["stress_m"] == off["stress_m"]
    # the family stays a frontier in the units it was searched in
    assert on["monotone"] and off["monotone"]
    assert on["n"] >= 2 and off["n"] >= 2


def test_the_page_loads_and_routes_its_default_trip(page_results):
    out, errors = page_results
    assert not errors, errors[:4]
    assert out["from"] and out["to"]
    assert len(out["members"]) >= 2, "the default trip should offer a real choice"
    f = out["frontier"]
    assert f["solutions"] >= 2 and not f["truncated"], f
    assert f["labels"] < 4_000_000, f


def test_search_finds_intersections_addresses_and_places(page_results):
    out, _ = page_results
    s = out["search"]
    assert out["intersections"] > 5000 and out["places"] > 5000 and out["hasAddresses"]
    assert s["24th & mission"][0] == ["24th Street & Mission Street", "intersection"]
    assert s["church st and 24th st"][0][1] == "intersection"
    assert s["1234 valencia"][0] == ["1234 Valencia St", "address"]
    assert s["golden gate park"][0] == ["Golden Gate Park", "park"]
    assert s["coit tower"][0] == ["Coit Tower", "viewpoint"]
    assert s["ocean beach"][0] == ["Ocean Beach", "beach"]
    assert any(n == "Caltrain" and k == "station" for n, k in s["caltrain"])
    assert any("Ferry Building" in n for n, _ in s["ferry building"])
    # abbreviations and full words match the same corners
    assert s["geary blvd & 25th ave"][0] == ["25th Avenue & Geary Boulevard", "intersection"]
    assert s["25th avenue and geary boulevard"][0] == ["25th Avenue & Geary Boulevard", "intersection"]
    assert s["cabrillo st & 38th ave"][0] == ["38th Avenue & Cabrillo Street", "intersection"]
    assert any(k == "intersection" and "Geary Boulevard" in n for n, k in s["geary blvd"])


def test_the_slider_ends_are_the_shortest_and_the_flattest(page_results):
    out, _ = page_results
    m = out["members"]
    first, last = m[0], m[-1]
    assert first["distance_m"] <= min(u["distance_m"] for u in m) + 1e-6
    assert last["gain"] <= min(u["gain"] for u in m) + 1e-6
    assert out["atZero"] == first["id"] and out["atOne"] == last["id"]
    assert 0 < out["half"] < len(m) - 1
    # the family is deduplicated: no two members share an arc sequence
    seqs = [tuple(map(tuple, u["arcs"])) for u in m]
    assert len(set(seqs)) == len(seqs)
    # and no member is dominated by another on both counts
    for a in m:
        for b in m:
            if a is b:
                continue
            assert not (b["distance_m"] <= a["distance_m"] - 1e-6
                        and b["gain"] <= a["gain"] - 1e-6), (a, b)


def test_the_family_is_monotone_in_distance_and_climbing(page_results):
    """Sliding right never shortens the route and never adds climbing: the
    defining property of a distance/climbing frontier sorted by distance,
    and the behaviour the slider's end labels promise."""
    out, _ = page_results
    seq = out["members"]
    for a, b in zip(seq, seq[1:]):
        assert b["distance_m"] >= a["distance_m"] - 1e-6
        assert b["gain"] <= a["gain"] + 1e-6


def test_a_new_trip_replaces_the_drawn_route_at_once(page_results):
    """Changing an endpoint redraws without the slider being touched."""
    out, _ = page_results
    r = out["retarget"]
    assert r["member"] and r["sameAsShown"] and r["endsAtCoit"], r


def test_a_click_in_the_bay_snaps_to_the_nearest_corner(page_results):
    """The pin and the route start must agree, even for a click far
    outside the street network."""
    out, _ = page_results
    r = out["snap"]
    assert r["node"] >= 0
    assert abs(r["pinLon"] - r["nodeLon"]) < 1e-9 and abs(r["pinLat"] - r["nodeLat"]) < 1e-9
    # the Pacific, 6 km west of Ocean Beach, lands on the western shore
    assert r["nodeLon"] > -122.52 and abs(r["nodeLat"] - 37.76) < 0.03


def test_the_share_link_carries_the_trip(page_results):
    out, _ = page_results
    h = out["hash"]
    # one bare token that survives any host or chat client
    assert h.startswith("#t~") and "~0.500~" in h
    assert all(c.isalnum() or c in "._~-" for c in h[1:]), h


def test_the_frontier_contains_every_weighted_optimum(page_results):
    """Every route a weighted sum length + alpha * climbing would choose is
    a frontier point, so for each alpha the family's best member must cost
    no more, under Python's own evaluation, than Python's route for that
    alpha between the same two nodes. This pins the browser's frontier
    search to the analysis's cost model."""
    from sf_flat_routes.config import ROUTING_PROFILES, with_alpha
    from sf_flat_routes.pipeline import build_context
    from sf_flat_routes.routing import route
    from sf_flat_routes.utils import configure_gdal_for_proxy

    out, _ = page_results
    configure_gdal_for_proxy()
    ctx = build_context(modes=("walk",))
    graph = ctx.graphs["walk"]
    t = graph.table
    key = {(int(e), d): i for i, (e, d) in enumerate(zip(t["edge_id"], t["direction"]))}
    first = out["members"][0]
    arcs0 = [key[(e, "rev" if r else "fwd")] for e, r in first["arcs"]]
    src = t["from_node"].iloc[arcs0[0]]
    dst = t["to_node"].iloc[arcs0[-1]]
    for k, alpha in enumerate(out["alphas"]):
        w = with_alpha(ROUTING_PROFILES["shortest"], alpha)
        cost = graph.build_costs(w)
        py_arcs, _ = route(graph, src, dst, w, arc_cost=cost)
        py_cost = float(cost[py_arcs].sum())
        best = min(out["members"], key=lambda u: u["costs"][k])
        arcs = [key[(e, "rev" if r else "fwd")] for e, r in best["arcs"]]
        js_cost = float(cost[arcs].sum())
        # the search merges frontier points within 0.5 m of climbing and
        # tolerates 10 cm per node inside; quantisation adds ~5 cm per arc
        tol = alpha * (0.5 + 0.1 * len(arcs)) + 0.05 * len(arcs) + 1.0
        assert js_cost <= py_cost + tol, (alpha, js_cost, py_cost, tol)


# ------------------------------------------------------------- the site
@pytest.fixture(scope="module")
def served_site():
    """The static site over HTTP, as GitHub Pages serves it."""
    import functools
    import http.server
    import threading

    if not SITE_INDEX.exists():
        pytest.skip("site not built")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(SITE_INDEX.parent))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_the_site_loads_its_graph_over_http(served_site):
    from playwright.sync_api import sync_playwright
    errors: list = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(executable_path=_chromium(),
                                         args=["--no-sandbox", "--disable-gpu"])
        except Exception as exc:                            # pragma: no cover
            pytest.skip(f"no usable Chromium: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text)
                if m.type == "error" and "fonts.g" not in m.text
                and "ERR_" not in m.text else None)
        failed: list = []
        page.on("requestfailed", lambda r: failed.append(r.url)
                if served_site in r.url else None)
        page.goto(served_site, wait_until="load", timeout=240_000)
        page.wait_for_function(
            "window.App && App.family && !document.getElementById('result').hidden",
            timeout=240_000)
        out = page.evaluate("""() => ({
            inline: !!window.DATA.bundle, url: window.DATA.bundle_url,
            hillshade: window.DATA.hillshade && window.DATA.hillshade.url,
            shade: !!document.querySelector('img.hillshade') && document.querySelector('img.hillshade').naturalWidth,
            routes: App.family.unique.length, status: document.getElementById('status').textContent,
        })""")
        browser.close()
    assert not errors, errors[:4]
    assert not failed, failed
    assert not out["inline"] and out["url"].startswith("data/graph-")
    assert out["hillshade"].startswith("data/hillshade-") and out["shade"] > 1000
    assert out["routes"] >= 2
    import re
    index = SITE_INDEX.read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="([^"]+)"', index)
    local = [r for r in refs if not r.startswith("http")]
    assert any(re.match(r"app-[0-9a-f]{10}\.js$", r) for r in local), local
    for name in local + [".nojekyll", out["url"], out["hillshade"]]:
        assert (SITE_INDEX.parent / name).exists(), name
