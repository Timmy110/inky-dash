"""Routing adapters. Each one normalises its API into `Route`.

TomTom and OSRM are the v1 core (DESIGN.md M5a). HERE, Mapbox, Google and ORS follow the
same interface; their parsing is covered by fixture tests, not yet by live calls.
"""

from __future__ import annotations

from datetime import UTC, datetime

from inkydash.providers.cache import ProviderError
from inkydash.providers.routing.base import (
    LatLon,
    Route,
    RoutingProvider,
    TrafficSection,
    pick_road,
    via_label,
)
from inkydash.providers.routing.polyline import decode_flexible, decode_google


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ====================================================================== TomTom
TOMTOM_MODES = {"car": "car", "bike": "bicycle", "walk": "pedestrian"}
TOMTOM_CATEGORY = {"JAM": "jam", "ROAD_WORK": "roadwork", "ROAD_CLOSURE": "closure"}


def parse_tomtom(j: dict) -> list[Route]:
    out = []
    for r in j.get("routes", []):
        s = r["summary"]
        geom = [(p["latitude"], p["longitude"]) for leg in r.get("legs", []) for p in leg.get("points", [])]
        sections = []
        for sec in r.get("sections", []):
            if sec.get("sectionType") != "TRAFFIC":
                continue
            sections.append(TrafficSection(
                start_idx=sec.get("startPointIndex", 0), end_idx=sec.get("endPointIndex", 0),
                category=TOMTOM_CATEGORY.get(sec.get("simpleCategory", ""), "slow"),
                magnitude=int(sec.get("magnitudeOfDelay", 0) or 0),
                delay_s=sec.get("delayInSeconds"),
            ))
        instr = (r.get("guidance") or {}).get("instructions", [])
        segs = []
        for a, b in zip(instr, instr[1:], strict=False):
            name = pick_road([a.get("street", "")], a.get("roadNumbers") or [])
            segs.append((name, b.get("routeOffsetInMeters", 0) - a.get("routeOffsetInMeters", 0)))
        out.append(Route(
            duration_s=s["travelTimeInSeconds"], distance_m=s["lengthInMeters"], geometry=geom,
            duration_typical_s=s.get("historicTrafficTravelTimeInSeconds"),
            duration_free_flow_s=s.get("noTrafficTravelTimeInSeconds"),
            traffic_delay_s=s.get("trafficDelayInSeconds"),
            traffic_sections=sections, via=via_label(segs), live=True,
        ))
    return out


class TomTom(RoutingProvider):
    name = "tomtom"
    modes = {"car", "bike", "walk"}
    live_traffic = True
    typical_duration = True
    has_traffic_sections = True
    max_alternatives = 5
    attribution = "Traffic © TomTom"

    def _routes(self, a, b, mode, *, depart, arrive, alternatives):
        url = (f"{self.base_url or 'https://api.tomtom.com'}/routing/1/calculateRoute/"
               f"{a[0]:.6f},{a[1]:.6f}:{b[0]:.6f},{b[1]:.6f}/json")
        params = {
            "key": self.key, "travelMode": TOMTOM_MODES[mode], "routeType": "fastest",
            "traffic": "true" if mode == "car" else "false",
            "maxAlternatives": alternatives, "computeTravelTimeFor": "all",
            "sectionType": "traffic", "instructionsType": "coded", "routeRepresentation": "polyline",
        }
        if depart:
            params["departAt"] = _iso(depart)
        routes = parse_tomtom(self.http.get_json(url, params=params, provider=self.name, limit=self.limit))
        if mode != "car":
            for r in routes:
                r.live = False
        return routes


# ====================================================================== OSRM
OSRM_PROFILES = {"car": ("routed-car", "driving"), "bike": ("routed-bike", "bike"), "walk": ("routed-foot", "foot")}


def parse_osrm(j: dict) -> list[Route]:
    if j.get("code") not in (None, "Ok"):
        raise ProviderError(f"osrm: {j.get('code')} {j.get('message', '')}")
    out = []
    for r in j.get("routes", []):
        segs = []
        for leg in r.get("legs", []):
            for st in leg.get("steps", []):
                segs.append((pick_road([st.get("name", "")], [st.get("ref", "")]), st.get("distance", 0)))
        out.append(Route(duration_s=r["duration"], distance_m=r["distance"],
                         geometry=decode_google(r["geometry"], 6), via=via_label(segs), live=False))
    return out


class Osrm(RoutingProvider):
    name = "osrm"
    modes = {"car", "bike", "walk"}
    max_alternatives = 2
    needs_key = False
    attribution = "Routing © OSRM / FOSSGIS, © OpenStreetMap"

    def _routes(self, a, b, mode, *, depart, arrive, alternatives):
        base = (self.base_url or "https://routing.openstreetmap.de").rstrip("/")
        prefix, profile = OSRM_PROFILES[mode]
        # FOSSGIS serves one OSRM per profile under /routed-<x>/; a self-hosted OSRM has no prefix.
        root = f"{base}/{prefix}" if "openstreetmap.de" in base else base
        url = f"{root}/route/v1/{profile}/{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
        params = {"overview": "full", "geometries": "polyline6", "steps": "true",
                  "alternatives": str(alternatives) if alternatives else "false"}
        return parse_osrm(self.http.get_json(url, params=params, provider=self.name, limit=self.limit,
                                             min_interval_s=1.1))


# ====================================================================== OpenRouteService
ORS_PROFILES = {"car": "driving-car", "bike": "cycling-regular", "walk": "foot-walking"}


def parse_ors(j: dict) -> list[Route]:
    out = []
    for r in j.get("routes", []):
        s = r.get("summary", {})
        segs = [(st.get("name", "") if st.get("name", "-") != "-" else "", st.get("distance", 0))
                for seg in r.get("segments", []) for st in seg.get("steps", [])]
        out.append(Route(duration_s=s.get("duration", 0), distance_m=s.get("distance", 0),
                         geometry=decode_google(r["geometry"], 5), via=via_label(segs), live=False))
    return out


class Ors(RoutingProvider):
    name = "ors"
    modes = {"car", "bike", "walk"}
    max_alternatives = 2  # target_count ≤ 3 includes the main route
    attribution = "Routing © openrouteservice.org, © OpenStreetMap"

    def _routes(self, a, b, mode, *, depart, arrive, alternatives):
        url = f"{self.base_url or 'https://api.openrouteservice.org'}/v2/directions/{ORS_PROFILES[mode]}/json"
        body: dict = {"coordinates": [[a[1], a[0]], [b[1], b[0]]], "instructions": True}
        if alternatives:
            body["alternative_routes"] = {"target_count": alternatives + 1, "share_factor": 0.6,
                                          "weight_factor": 1.6}
        r = self.http.request("POST", url, provider=self.name, limit=self.limit, json=body,
                              headers={"Authorization": self.key or ""})
        return parse_ors(r.json())


# ====================================================================== HERE v8
HERE_MODES = {"car": "car", "bike": "bicycle", "walk": "pedestrian"}


def parse_here(j: dict) -> list[Route]:
    out = []
    for r in j.get("routes", []):
        geom: list[LatLon] = []
        dur = base = typ = length = 0.0
        has_typ = True
        sections: list[TrafficSection] = []
        segs: list[tuple[str, float]] = []
        for sec in r.get("sections", []):
            offset = len(geom)
            pts = decode_flexible(sec["polyline"])
            if geom and pts and pts[0] == geom[-1]:
                pts = pts[1:]
                offset -= 1
            geom.extend(pts)
            s = sec.get("summary", {})
            dur += s.get("duration", 0)
            base += s.get("baseDuration", s.get("duration", 0))
            length += s.get("length", 0)
            if "typicalDuration" in s:
                typ += s["typicalDuration"]
            else:
                has_typ = False
            spans = sec.get("spans", [])
            for i, sp in enumerate(spans):
                start = offset + sp.get("offset", 0)
                end = offset + (spans[i + 1]["offset"] if i + 1 < len(spans) else len(pts))
                names = [n.get("value", "") for n in sp.get("names", [])]
                numbers = [n.get("value", "") for n in sp.get("routeNumbers", [])]
                segs.append((pick_road(names, numbers), sp.get("length", 0)))
                ds = sp.get("dynamicSpeedInfo") or {}
                ts, bs = ds.get("trafficSpeed"), ds.get("baseSpeed")
                if ts is not None and bs and ts < bs * 0.65:
                    ratio = ts / bs
                    mag = 3 if ratio < 0.25 else 2 if ratio < 0.45 else 1
                    span_len = sp.get("length", 0)
                    delay = span_len / max(ts, 0.5) - span_len / bs if span_len else None
                    if sections and sections[-1].end_idx >= start and sections[-1].magnitude == mag:
                        sections[-1].end_idx = end
                        if delay and sections[-1].delay_s is not None:
                            sections[-1].delay_s += delay
                    else:
                        sections.append(TrafficSection(start, end, "jam", mag, delay))
        out.append(Route(duration_s=dur, distance_m=length, geometry=geom,
                         duration_typical_s=typ if has_typ else None, duration_free_flow_s=base,
                         traffic_delay_s=max(0.0, dur - base), traffic_sections=sections,
                         via=via_label(segs), live=True))
    return out


class Here(RoutingProvider):
    name = "here"
    modes = {"car", "bike", "walk"}
    live_traffic = True
    typical_duration = True
    has_traffic_sections = True
    max_alternatives = 6
    attribution = "Traffic © HERE"

    def _routes(self, a, b, mode, *, depart, arrive, alternatives):
        params = {
            "apiKey": self.key, "transportMode": HERE_MODES[mode],
            "origin": f"{a[0]:.6f},{a[1]:.6f}", "destination": f"{b[0]:.6f},{b[1]:.6f}",
            "return": "summary,polyline,typicalDuration",
            "spans": "dynamicSpeedInfo,names,routeNumbers,length",
            "alternatives": alternatives,
            "departureTime": _iso(depart) if depart else "any" if mode != "car" else _iso(datetime.now(UTC)),
        }
        routes = parse_here(self.http.get_json(f"{self.base_url or 'https://router.hereapi.com'}/v8/routes",
                                               params=params, provider=self.name, limit=self.limit))
        if mode != "car":
            for r in routes:
                r.live = False
        return routes


# ====================================================================== Mapbox
MAPBOX_PROFILES = {"car": "driving-traffic", "bike": "cycling", "walk": "walking"}
MAPBOX_CONGESTION = {"moderate": 1, "heavy": 2, "severe": 3}


def parse_mapbox(j: dict) -> list[Route]:
    if j.get("code") not in (None, "Ok"):
        raise ProviderError(f"mapbox: {j.get('code')} {j.get('message', '')}")
    out = []
    for r in j.get("routes", []):
        geom = decode_google(r["geometry"], 6)
        sections: list[TrafficSection] = []
        segs: list[tuple[str, float]] = []
        idx = 0
        for leg in r.get("legs", []):
            for st in leg.get("steps", []):
                segs.append((pick_road([st.get("name", "")], [st.get("ref", "")]), st.get("distance", 0)))
            cong = (leg.get("annotation") or {}).get("congestion", [])
            for i, level in enumerate(cong):
                mag = MAPBOX_CONGESTION.get(level, 0)
                if mag >= 2:
                    a, b = idx + i, idx + i + 1
                    if sections and sections[-1].end_idx == a and sections[-1].magnitude == mag:
                        sections[-1].end_idx = b
                    else:
                        sections.append(TrafficSection(a, b, "jam", mag))
            idx += len(cong)
        out.append(Route(duration_s=r["duration"], distance_m=r["distance"], geometry=geom,
                         traffic_sections=sections, via=via_label(segs), live=True))
    return out


class Mapbox(RoutingProvider):
    name = "mapbox"
    modes = {"car", "bike", "walk"}
    live_traffic = True
    has_traffic_sections = True
    max_alternatives = 2
    attribution = "Traffic © Mapbox"

    def _routes(self, a, b, mode, *, depart, arrive, alternatives):
        base = self.base_url or "https://api.mapbox.com"
        coords = f"{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
        params = {"access_token": self.key, "geometries": "polyline6", "overview": "full", "steps": "true",
                  "alternatives": "true" if alternatives else "false"}
        if mode == "car":
            params["annotations"] = "congestion"
        if depart and mode == "car" and depart > datetime.now(UTC):
            params["depart_at"] = depart.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
        url = f"{base}/directions/v5/mapbox/{MAPBOX_PROFILES[mode]}/{coords}"
        routes = parse_mapbox(self.http.get_json(url, params=params, provider=self.name, limit=self.limit))
        if mode != "car":
            for r in routes:
                r.live = False
            return routes
        # No "usual" time: a free-flow request (profile driving, no traffic) is the baseline.
        try:
            ff = parse_mapbox(self.http.get_json(f"{base}/directions/v5/mapbox/driving/{coords}",
                                                 params={"access_token": self.key, "overview": "false"},
                                                 provider=self.name, limit=self.limit))
            if ff:
                for r in routes:
                    r.duration_free_flow_s = ff[0].duration_s
                    r.traffic_delay_s = max(0.0, r.duration_s - ff[0].duration_s)
        except ProviderError:
            pass
        return routes


# ====================================================================== Google Routes
GOOGLE_MODES = {"car": "DRIVE", "bike": "BICYCLE", "walk": "WALK", "transit": "TRANSIT"}
GOOGLE_SPEED = {"SLOW": 1, "TRAFFIC_JAM": 3}


def _secs(v: str | None) -> float | None:
    return float(v.rstrip("s")) if v else None


def parse_google(j: dict) -> list[Route]:
    out = []
    for r in j.get("routes", []):
        geom = decode_google(r["polyline"]["encodedPolyline"], 5)
        sections = []
        for iv in (r.get("travelAdvisory") or {}).get("speedReadingIntervals", []):
            mag = GOOGLE_SPEED.get(iv.get("speed", "NORMAL"), 0)
            if mag:
                sections.append(TrafficSection(iv.get("startPolylinePointIndex", 0),
                                               iv.get("endPolylinePointIndex", 0),
                                               "jam" if mag == 3 else "slow", mag))
        dur = _secs(r.get("duration")) or 0.0
        static = _secs(r.get("staticDuration"))
        desc = r.get("description") or None
        out.append(Route(duration_s=dur, distance_m=r.get("distanceMeters", 0), geometry=geom,
                         duration_free_flow_s=static,
                         traffic_delay_s=max(0.0, dur - static) if static else None,
                         traffic_sections=sections, via=desc, live=True))
    return out


class Google(RoutingProvider):
    name = "google"
    modes = {"car", "bike", "walk", "transit"}
    live_traffic = True
    has_traffic_sections = True
    max_alternatives = 3
    attribution = "Traffic © Google"

    def _routes(self, a, b, mode, *, depart, arrive, alternatives):
        body: dict = {
            "origin": {"location": {"latLng": {"latitude": a[0], "longitude": a[1]}}},
            "destination": {"location": {"latLng": {"latitude": b[0], "longitude": b[1]}}},
            "travelMode": GOOGLE_MODES[mode],
            "computeAlternativeRoutes": bool(alternatives),
            "polylineQuality": "HIGH_QUALITY",
        }
        if mode == "car":
            body["routingPreference"] = "TRAFFIC_AWARE"
            body["extraComputations"] = ["TRAFFIC_ON_POLYLINE"]
        if depart and depart > datetime.now(UTC):
            body["departureTime"] = _iso(depart)
        fields = ("routes.duration,routes.staticDuration,routes.distanceMeters,routes.polyline.encodedPolyline,"
                  "routes.description,routes.travelAdvisory.speedReadingIntervals")
        r = self.http.request("POST", f"{self.base_url or 'https://routes.googleapis.com'}/directions/v2:computeRoutes",
                              provider=self.name, limit=self.limit, json=body,
                              headers={"X-Goog-Api-Key": self.key or "", "X-Goog-FieldMask": fields})
        routes = parse_google(r.json())
        if mode != "car":
            for rt in routes:
                rt.live = False
        return routes


ADAPTERS: dict[str, type[RoutingProvider]] = {
    "tomtom": TomTom, "here": Here, "mapbox": Mapbox, "google": Google, "osrm": Osrm, "ors": Ors,
}
