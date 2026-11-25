"""The current day, in UTC like the stored event dates."""

import pandas as pd


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def utc_today() -> pd.Timestamp:
    """Midnight of the current UTC day, without a time zone: the type of the event dates."""
    return utc_now().tz_localize(None).normalize()
