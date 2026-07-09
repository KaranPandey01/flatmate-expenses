from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from ledger import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.dashboard, name='dashboard'),
    path('person/<int:person_id>/', views.person_detail, name='person_detail'),
    path('import-report/', views.import_report, name='import_report'),
    path('login/', auth_views.LoginView.as_view(template_name='ledger/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
]
