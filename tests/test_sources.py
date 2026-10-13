from rx.sources.intervals import activity_from_icu, wellness_from_icu
from rx.sources.garmin import activity_from_garmin, wellness_for
from rx.sports import BIKE, SKI, WALK, normalize


def test_sport_normalization():
    assert normalize("MountainBikeRide") == BIKE
    assert normalize("mountain_biking") == BIKE
    assert normalize("BackcountrySki") == SKI
    assert normalize("hiking") == WALK


def test_intervals_mapping():
    a = activity_from_icu({"id": "i9", "start_date_local": "2026-10-12T14:01:00",
                           "type": "MountainBikeRide", "moving_time": 7300,
                           "average_heartrate": 141, "max_heartrate": 176})
    assert a.id == "icu:i9" and a.date == "2026-10-12" and a.sport == BIKE and a.moving_s == 7300
    w = wellness_from_icu({"id": "2026-10-12", "hrv": 58.0, "restingHR": 49, "sleepScore": 83})
    assert (w.hrv, w.rhr, w.sleep_score) == (58.0, 49, 83)


def test_garmin_mapping():
    a = activity_from_garmin({"activityId": 1, "startTimeLocal": "2026-10-12 14:01:00",
                              "activityType": {"typeKey": "backcountry_skiing"}, "duration": 3600})
    assert a.date == "2026-10-12" and a.sport == SKI

    class Fake:
        def get_hrv_data(self, d): return {"hrvSummary": {"lastNightAvg": 61}}
        def get_sleep_data(self, d): return {"dailySleepDTO": {"sleepTimeSeconds": 27000,
                                             "sleepScores": {"overall": {"value": 88}}},
                                             "restingHeartRate": 48}
        def get_rhr_day(self, d): raise RuntimeError("not on this device")
        def get_training_readiness(self, d): return [{"score": 74}]
    w, raw = wellness_for(Fake(), "2026-10-12")
    assert (w.hrv, w.sleep_score, w.rhr, w.readiness) == (61, 88, 48, 74)
    assert "error" in raw["rhr"]
