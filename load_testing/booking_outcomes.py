def is_expected_business_conflict(
    status_code: int,
    error_code: str,
) -> bool:
    """Identify an expected concurrent seat-booking conflict."""

    return (
        status_code == 409
        and error_code == "SEAT_UNAVAILABLE"
    )