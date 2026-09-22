from . import google


def google_sign_in(request):
    """Whether to draw the Google button on the admin login page.

    A deployment with no credentials configured — a developer's laptop, say —
    should not show a button that can only fail.
    """
    return {"google_sign_in_enabled": google.is_configured()}
