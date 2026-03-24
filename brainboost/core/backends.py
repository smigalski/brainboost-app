from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate with username first, then fallback to email."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None

        user = super().authenticate(
            request,
            username=username,
            password=password,
            **kwargs,
        )
        if user is not None:
            return user

        user_model = get_user_model()
        candidates = user_model._default_manager.filter(email__iexact=username)
        for candidate in candidates:
            if candidate.check_password(password) and self.user_can_authenticate(candidate):
                return candidate
        return None
