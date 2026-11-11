from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path
from hostel import views as v

urlpatterns = [
    path("favicon.ico", v.favicon, name="favicon"),
    path("admin/", admin.site.urls),
    path("", v.dash, name="dash"), path("login/", v.login_view, name="login"), path("logout/", v.logout_view, name="logout"),
    path("register/", v.register, name="register"),
    path("check-reg-no/", v.check_reg_no, name="check_reg_no"),
    path("forgot-password/", v.forgot_password, name="forgot_password"),
    path("students/", v.students, name="students"), path("allocate/<int:sid>/", v.allocate, name="allocate"),
    path("student-id/", v.student_id, name="student_id"), path("student-id/<int:sid>/", v.student_id, name="student_id_detail"),
    path("rooms/", v.rooms, name="rooms"), path("room/<int:pk>/del/", v.room_del, name="room_del"),
    path("fees/", v.fees, name="fees"), path("pay/<int:sid>/", v.pay, name="pay"),
    path("receipt/<int:pid>/", v.receipt_payment, name="receipt_payment"),
    path("receipt/statement/<int:sid>/", v.receipt_statement, name="receipt_statement"),
    path("complaints/", v.complaints, name="complaints"), path("complaint/<int:pk>/", v.cstatus, name="cstatus"),
    path("leaves/", v.leaves, name="leaves"), path("leave/<int:pk>/", v.ldecide, name="ldecide"),
    path("gate/", v.gate, name="gate"), path("gate/out/", v.gout, name="gout"), path("gate/in/", v.gin, name="gin"),
    path("gate/<int:pk>/", v.gdecide, name="gdecide"),
    path("food/", v.food, name="food"), path("food/<int:pk>/", v.foodsave, name="foodsave"),
    path("visitors/", v.visitors, name="visitors"), path("visitor/<int:pk>/", v.vout, name="vout"), path("visitor/<int:pk>/decide/", v.vdecide, name="vdecide"),
    path("notices/", v.notices, name="notices"), path("staff/", v.staff, name="staff"), path("staff/<int:pk>/", v.stoggle, name="stoggle"),
    path("audit/", v.audit, name="audit"), path("reports/", v.reports, name="reports"), path("work/", v.work, name="work"),
    path("restore-data/", v.restore_data, name="restore_data"),
    path("restore/<int:pk>/", v.restore_item, name="restore_item"),
    path("restore/all/", v.restore_all, name="restore_all"),
    path("purge/<int:pk>/", v.purge_item, name="purge_item"),
    path("purge/all/", v.purge_all, name="purge_all"),
    path("delete/<str:model_name>/<int:pk>/", v.delete_item, name="delete_item"),
    path("settings/", v.settings_view, name="settings"),
    path("settings/fee/", v.settings_save_fees, name="settings_save_fees"),
    path("settings/add-student/", v.settings_add_student, name="settings_add_student"),
    path("settings/reset-pw/", v.settings_reset_pw, name="settings_reset_pw"),
    path("settings/add-staff/", v.settings_add_staff, name="settings_add_staff"),
    path("settings/general/", v.settings_save_general, name="settings_save_general"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
