from django.contrib.auth import views as auth_views


class LoginView(auth_views.LoginView):
    """Company login page (templates/registration/login.html).

    "在这台电脑上保持登录" unchecked → the session ends when the browser closes.
    """

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        if not self.request.POST.get("remember"):
            self.request.session.set_expiry(0)
        return response
