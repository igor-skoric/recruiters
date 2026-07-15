from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator

from departments.models import AccessLevel


def user_in_department(user, department_slug: str) -> bool:
    if not user.is_authenticated:
        return False
    return user.department.slug == department_slug


def user_has_role(user, role_slug: str) -> bool:
    if not user.is_authenticated:
        return False
    return user.role.slug == role_slug


def user_has_read_access(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role.access_level in {AccessLevel.READ, AccessLevel.FULL}


def user_has_full_access(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.role.access_level == AccessLevel.FULL


def permission_denied_response(request: HttpRequest) -> HttpResponse:
    from django.shortcuts import render

    return render(request, "403.html", status=403)


def department_required(department_slug: str) -> Callable:
    def decorator(view_func: Callable) -> Callable:
        @login_required
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not user_in_department(request.user, department_slug):
                return permission_denied_response(request)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def role_required(role_slug: str) -> Callable:
    def decorator(view_func: Callable) -> Callable:
        @login_required
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not user_has_role(request.user, role_slug):
                return permission_denied_response(request)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def read_access_required(view_func: Callable | None = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        @login_required
        @wraps(func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not user_has_read_access(request.user):
                return permission_denied_response(request)
            return func(request, *args, **kwargs)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator


def full_access_required(view_func: Callable | None = None) -> Callable:
    def decorator(func: Callable) -> Callable:
        @login_required
        @wraps(func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            if not user_has_full_access(request.user):
                return permission_denied_response(request)
            return func(request, *args, **kwargs)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator


class LoginRequiredMixin:
    @method_decorator(login_required)
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class DepartmentRequiredMixin(LoginRequiredMixin):
    department_slug: str = ""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]
        if not user_in_department(request.user, self.department_slug):
            return permission_denied_response(request)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class RoleRequiredMixin(LoginRequiredMixin):
    role_slug: str = ""

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]
        if not user_has_role(request.user, self.role_slug):
            return permission_denied_response(request)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class ReadAccessRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]
        if not user_has_read_access(request.user):
            return permission_denied_response(request)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class FullAccessRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]
        if not user_has_full_access(request.user):
            return permission_denied_response(request)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]
