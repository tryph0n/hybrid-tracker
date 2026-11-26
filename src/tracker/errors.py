"""Errors that the code raises on purpose with a message for the user."""


class UserError(ValueError):
    """A known problem: the message says what is wrong and how to fix it.

    The command line prints only the message. It is a `ValueError`, so code that
    expects a `ValueError` keeps working.
    """
