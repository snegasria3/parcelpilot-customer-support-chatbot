from zoneinfo import ZoneInfo


def test_assessment_timezone_is_available_on_all_supported_platforms():
    timezone = ZoneInfo("Asia/Kolkata")
    assert timezone.key == "Asia/Kolkata"
