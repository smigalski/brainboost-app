from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameModelBackend(ModelBackend):
    """Use email in the WebApp and retain username only for Django admin."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None

        is_admin_login = request is not None and request.path.startswith("/admin/")
        if is_admin_login or request is None:
            user = super().authenticate(
                request,
                username=username,
                password=password,
                **kwargs,
            )
            if user is not None:
                return user

        user_model = get_user_model()
        candidates = user_model._default_manager.filter(email__iexact=username.strip())
        authenticated_user = None
        for candidate in candidates:
            if candidate.check_password(password) and self.user_can_authenticate(candidate):
                # Existing accounts may share an email address. Only sign in
                # when the email/password pair identifies one active account.
                if authenticated_user is not None:
                    return None
                authenticated_user = candidate
        return authenticated_user
