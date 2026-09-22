"""Callbacks used by the Unfold sidebar. See volusiahd/unfold.py."""


def is_superuser(request):
    return request.user.is_active and request.user.is_superuser
