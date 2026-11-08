from django.apps import AppConfig
from django.db.models.signals import post_migrate


def seed(sender, **kwargs):
    from django.contrib.auth.models import User
    from .models import Menu, Profile, Room
    if User.objects.exists():
        return
    for u, p, role, name in [("admin", "admin123", "admin", "Admin"), ("warden", "warden123", "warden", "Warden"),
                             ("maint", "maint123", "maintenance", "Maintenance staff")]:
        user = User.objects.create_user(u, password=p, first_name=name, is_staff=role == "admin", is_superuser=role == "admin")
        Profile.objects.create(user=user, role=role)
    for n, g, c in [("101", "Male", 2), ("102", "Male", 3), ("103", "Male", 4), ("104", "Male", 1),
                    ("201", "Female", 2), ("202", "Female", 3), ("203", "Female", 4), ("204", "Female", 1)]:
        Room.objects.create(number=n, gender=g, capacity=c, floor=int(n[0]))
    for d, b, l, di in [("Monday", "Idli, sambar", "Rice, dal, curd", "Chapati, paneer curry"),
                        ("Tuesday", "Dosa, chutney", "Veg pulao, raita", "Rice, rasam, egg curry"),
                        ("Wednesday", "Upma", "Rice, sambar, poriyal", "Chapati, mixed veg"),
                        ("Thursday", "Poori, bhaji", "Rice, sambar, beans", "Chapati, dal tadka"),
                        ("Friday", "Pongal, chutney", "Lemon rice, curd", "Rice, fish or veg curry"),
                        ("Saturday", "Bread, jam, banana", "Biryani, raita", "Dosa, sagu"),
                        ("Sunday", "Aloo paratha", "Special meals", "Chapati, kurma")]:
        Menu.objects.create(day=d, breakfast=b, lunch=l, dinner=di)
    from .models import Complaint, Leave, Notice, Payment, Student, Visitor
    rm = {x.number: x for x in Room.objects.all()}
    for reg, name, g, dob, co, yr, gn, ad, room in [
            ("BCA101", "Annapurna L", "Female", "2005-03-14", "BCA", 3, "Lakshmi", "Mysuru", "201"),
            ("BCA118", "Rohan K", "Male", "2004-11-02", "BCom", 2, "Kiran", "Hassan", "101"),
            ("MCA007", "Arjun S", "Male", "2002-07-21", "MCA", 1, "Suresh", "Mandya", "103"),
            ("BSC044", "Meera P", "Female", "2006-01-09", "BSc", 1, "Latha", "Tumakuru", None)]:
        s = Student.objects.create(reg_no=reg, name=name, gender=g, dob=dob, phone="9876500001", email=reg.lower() + "@example.com",
                                   course=co, year=yr, guardian_name=gn, guardian_phone="9876500011", address=ad, room=rm.get(room))
        Profile.objects.create(user=User.objects.create_user(reg, password="student123", first_name=name), role="student", student=s)
    a, ro = Student.objects.get(reg_no="BCA101"), Student.objects.get(reg_no="BCA118")
    Payment.objects.create(student=a, amount=36700); Payment.objects.create(student=a, amount=36700); Payment.objects.create(student=ro, amount=110000)
    Complaint.objects.create(student=a, title="Tube light flickering", details="The light in room 201 flickers at night.", status="In progress")
    Complaint.objects.create(student=Student.objects.get(reg_no="MCA007"), title="Wi-Fi down on floor 2", details="No signal since morning.")
    Leave.objects.create(student=a, dates="28 Oct to 2 Nov", reason="Family function")
    Notice.objects.create(title="Mess timing change", message="Dinner is now 7:30 to 9:00 PM from Monday.", audience="All")
    Notice.objects.create(title="Fee due date", message="Second instalment is due on 15 Nov.", audience="Students")
    Visitor.objects.create(name="S. Kumar", student=ro, relation="Father")
    Notice.objects.create(title="Room inspection on Friday", message="Wardens: check all rooms before 5 PM.", audience="Wardens")
    Notice.objects.create(title="Water heater check", message="Maintenance: inspect heaters in the girls wing.", audience="Maintenance")


def _ip(request):
    return request.META.get("REMOTE_ADDR", "") if request else ""


def on_login(sender, request, user, **kw):
    from .models import Audit, Profile
    p = Profile.objects.filter(user=user).first()
    Audit.objects.create(who=user.username, kind="Login", action=f"Signed in as {p.role if p else 'admin'}", ip=_ip(request))


def on_logout(sender, request, user, **kw):
    from .models import Audit
    if user:
        Audit.objects.create(who=user.username, kind="Logout", action="Signed out", ip=_ip(request))


def on_fail(sender, credentials, request=None, **kw):
    from .models import Audit
    Audit.objects.create(who=str(credentials.get("username", "?"))[:100], kind="Failed login", action="Incorrect username or password", ip=_ip(request))


class HostelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hostel"

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
        post_migrate.connect(seed, sender=self)
        user_logged_in.connect(on_login)
        user_logged_out.connect(on_logout)
        user_login_failed.connect(on_fail)
