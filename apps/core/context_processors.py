from .permissions import get_user_role


def user_role(request):
    if request.user.is_authenticated:
        return {"user_role": get_user_role(request.user)}
    return {}
