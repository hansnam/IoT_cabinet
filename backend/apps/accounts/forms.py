"""Form HTML chỉ kiểm dữ liệu; mutation được thực hiện trong service."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.policies.roles import ALL_ROLES, ROLE_ADMIN, get_user_role
from apps.accounts.services.auth import authenticate_credentials


class CabinetLoginForm(AuthenticationForm):
    def clean(self):
        username, password = self.cleaned_data.get("username"), self.cleaned_data.get("password")
        if username and password:
            if self.request is None:
                raise ValidationError("Không xác định được yêu cầu đăng nhập.")
            self.user_cache, error, self.retry_after = authenticate_credentials(self.request, username, password)
            if error or not self.user_cache or get_user_role(self.user_cache) != ROLE_ADMIN or not self.user_cache.is_staff:
                raise ValidationError("Không đăng nhập được. Kiểm tra tài khoản hoặc thử lại sau.")
        return self.cleaned_data


class ManagedUserForm(forms.ModelForm):
    role = forms.ChoiceField(choices=[(r, r) for r in ALL_ROLES])
    temporary_password = forms.CharField(
        label="Mật khẩu tạm (để trống nếu không reset)", required=False, strip=False,
        widget=forms.PasswordInput, min_length=12, max_length=128,
    )
    confirm_password = forms.CharField(label="Nhập lại mật khẩu tạm", required=False, strip=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("username", "full_name", "email", "role", "status", "temporary_password", "confirm_password")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:
            self.fields["username"].disabled = True
            self.fields["role"].initial = get_user_role(self.instance)
        else:
            self.fields["temporary_password"].required = True
            self.fields["confirm_password"].required = True

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()

    def clean(self):
        data = super().clean() or {}
        password = data.get("temporary_password", "")
        if password != data.get("confirm_password", ""):
            self.add_error("confirm_password", "Mật khẩu xác nhận không khớp.")
        if password:
            candidate = User(username=data.get("username", ""), full_name=data.get("full_name", ""), email=data.get("email"))
            try:
                validate_password(password, user=candidate)
            except ValidationError as exc:
                self.add_error("temporary_password", exc)
        return data


class CabinetPasswordForm(PasswordChangeForm):
    # Chỉ kiểm khớp hai ô mới ở form; old-password/validators/limiter do service05 kiểm.
    def clean_old_password(self):
        return self.cleaned_data["old_password"]

    def clean(self):
        self.validate_passwords("new_password1", "new_password2")
        return self.cleaned_data
