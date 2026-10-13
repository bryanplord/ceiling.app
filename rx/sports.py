"""Normalize sport names from every source into a few buckets the engine reasons about."""

BIKE = "bike"
RUN = "run"
WALK = "walk"
SKI = "ski"
OTHER = "other"

_MAP = {
    # intervals.icu (Strava-style names)
    "ride": BIKE, "mountainbikeride": BIKE, "gravelride": BIKE, "virtualride": BIKE,
    "ebikeride": BIKE, "emountainbikeride": BIKE, "velomobile": BIKE,
    "run": RUN, "trailrun": RUN, "virtualrun": RUN,
    "walk": WALK, "hike": WALK,
    "backcountryski": SKI, "nordicski": SKI, "alpineski": SKI, "snowshoe": SKI,
    # Garmin typeKey names
    "cycling": BIKE, "mountain_biking": BIKE, "road_biking": BIKE, "gravel_cycling": BIKE,
    "indoor_cycling": BIKE, "virtual_ride": BIKE, "e_bike_mountain": BIKE,
    "running": RUN, "trail_running": RUN, "treadmill_running": RUN, "street_running": RUN,
    "walking": WALK, "hiking": WALK, "casual_walking": WALK, "speed_walking": WALK,
    "backcountry_skiing": SKI, "backcountry_skiing_snowboarding_ws": SKI,
    "resort_skiing": SKI, "resort_skiing_snowboarding_ws": SKI,
    "skate_skiing_ws": SKI, "cross_country_skiing_ws": SKI,
}

LABEL = {BIKE: "Ride", RUN: "Run", WALK: "Walk", SKI: "Ski tour", OTHER: "Session"}


def normalize(raw: str | None) -> str:
    if not raw:
        return OTHER
    return _MAP.get(raw.strip().lower().replace(" ", ""), _MAP.get(raw.strip().lower(), OTHER))
