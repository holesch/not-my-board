import re

TIME_UNIT_TO_SECONDS = {
    "seconds": 1,
    "minutes": 60,
    "hours": 60 * 60,
    "days": 24 * 60 * 60,
    "weeks": 7 * 24 * 60 * 60,
}


def parse_time(time_string):
    if not time_string:
        raise RuntimeError("Time is an empty string")

    time_pattern = (
        r"(?:(?P<weeks>\d+)w)?"
        r"(?:(?P<days>\d+)d)?"
        r"(?:(?P<hours>\d+)h)?"
        r"(?:(?P<minutes>\d+)m)?"
        r"(?:(?P<seconds>\d+)s?)?"
    )

    match = re.fullmatch(time_pattern, time_string)
    if match is None:
        raise RuntimeError("Invalid time format")

    total_seconds = 0
    for unit, value in match.groupdict().items():
        if value:
            total_seconds += int(value) * TIME_UNIT_TO_SECONDS[unit]

    return total_seconds
