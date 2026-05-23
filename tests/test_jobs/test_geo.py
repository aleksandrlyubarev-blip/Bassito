from bassito_jobs.geo import GeoFilter, MIGDAL_HAEMEK, haversine_km, normalize_city, passes


def test_haversine_known_distance():
    # Migdal HaEmek → Haifa is roughly 25 km.
    d = haversine_km(MIGDAL_HAEMEK, (32.794, 34.9896))
    assert 18 < d < 32


def test_normalize_city_picks_longer_match():
    assert normalize_city("Tel Aviv-Yafo Israel") == "tel aviv"
    assert normalize_city("Haifa, Israel") == "haifa"
    assert normalize_city("Yokneam Illit") == "yokneam"
    assert normalize_city("San Francisco") is None


def test_passes_within_radius():
    g = GeoFilter(radius_km=60)
    assert passes("Haifa, Israel", "Acme", g) is True
    assert passes("Yokneam", "Acme", g) is True
    assert passes("Tel Aviv", "Acme", g) is False  # ~85 km from Migdal HaEmek
    assert passes("Eilat", "Acme", g) is False


def test_passes_extra_allow():
    g = GeoFilter(radius_km=30, extra_allow=["tel aviv"])
    assert passes("Tel Aviv, Israel", "Acme", g) is True


def test_passes_flex_haifa_always_in():
    g = GeoFilter(radius_km=1, flex_haifa=True)
    assert passes("Haifa, Israel", "Flex Ltd", g) is True
    assert passes("Ofakim", "Flex", g) is True
    # Non-Flex still subject to radius.
    assert passes("Haifa", "Acme", g) is False


def test_passes_remote():
    assert passes("Remote, Israel", "Acme", GeoFilter(remote_ok=True)) is True
    assert passes("Remote, Israel", "Acme", GeoFilter(remote_ok=False)) is False
