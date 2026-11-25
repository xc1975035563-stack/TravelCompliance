from app.evaluations import CASES, run_all


def test_evaluation_suite_covers_three_trip_types():
    report = run_all()
    failed = [item for item in report["cases"] if not item["passed"]]
    assert report["total"] >= 25, report["total"]
    assert report["failed"] == 0, failed
    for trip_type in ("onsite", "client_visit", "training"):
        typed = [item for item in report["cases"] if item["trip_type"] == trip_type]
        assert any(item["passed"] and item["expect_ok"] for item in typed), trip_type
        assert any(item["passed"] and not item["expect_ok"] for item in typed), trip_type
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
