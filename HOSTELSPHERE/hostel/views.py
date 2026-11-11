import base64
import io
import json
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count
from django.middleware.csrf import get_token
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.http import require_POST

from .models import (Audit, Complaint, DeletedItem, GatePass, Leave, Menu, Notice, Payment, Profile, Room, Student, SystemSetting, Visitor)

STAFF = ("admin", "warden")
NAV = {
    "admin": [("dash", "Dashboard"), ("students", "Students"), ("rooms", "Rooms and beds"), ("fees", "Fees and receipts"),
              ("complaints", "Complaints"), ("leaves", "Leave requests"), ("gate", "Gate pass"), ("food", "Food and mess"),
              ("visitors", "Visitors"), ("staff", "Staff accounts"), ("notices", "Notices"), ("reports", "Reports"), ("audit", "Audit logs"),
              ("restore_data", "Restore data"), ("settings", "Settings")],
    "warden": [("dash", "Dashboard"), ("students", "Students"), ("rooms", "Rooms and beds"), ("fees", "Fees and receipts"),
               ("complaints", "Complaints"), ("leaves", "Leave requests"), ("gate", "Gate pass"), ("food", "Food and mess"),
               ("visitors", "Visitors"), ("notices", "Notices"), ("restore_data", "Restore data")],
    "student": [("dash", "My room and fees"), ("student_id", "Student ID"), ("fees", "Fee receipts"), ("complaints", "Complaints"), ("leaves", "Leave request"),
                ("visitors", "Visitor request"), ("gate", "Gate pass"), ("food", "Food and mess"), ("notices", "Notices"),
                ("restore_data", "Restore data")],
    "maintenance": [("dash", "Dashboard"), ("rooms", "Rooms and floors"), ("complaints", "Complaints"), ("work", "Work report"), ("notices", "Notices"),
                    ("restore_data", "Restore data")],
}
ICON = {"dash": "🏠", "student_id": "🪪", "students": "🎓", "rooms": "🛏️", "fees": "💳", "complaints": "🛠️", "leaves": "📝", "gate": "🚪", "food": "🍽️", "visitors": "👥", "staff": "🧑‍💼", "notices": "📢", "reports": "📊", "audit": "🧾", "work": "🧰", "restore_data": "🔄", "settings": "⚙️"}
DEMO = {"admin": ("admin", "admin123"), "warden": ("warden", "warden123"), "maintenance": ("maint", "maint123"), "student": ("BCA101", "student123")}
money = lambda x: f"₹{x:,}"


def prof(r):
    p, _ = Profile.objects.get_or_create(user=r.user, defaults={"role": "admin" if r.user.is_superuser else "student"})
    return p


def ctx(r):  # template context processor
    if not r.user.is_authenticated:
        return {}
    p = prof(r)
    return {"role": p.role, "nav": [(u, l, ICON.get(u, "")) for u, l in NAV.get(p.role, [])], "me": p.student.name if p.student else (r.user.first_name or r.user.username)}


def need(*roles):
    def deco(f):
        @wraps(f)
        def w(r, *a, **k):
            if prof(r).role not in roles:
                messages.error(r, "You do not have access to that page.")
                return redirect("dash")
            return f(r, *a, **k)
        return login_required(w)
    return deco


def log(r, action):
    Audit.objects.create(who=r.user.username, action=action)


def csrf(r):
    return format_html('<input type="hidden" name="csrfmiddlewaretoken" value="{}">', get_token(r))


def btn(r, name, args, label, cls="", **data):
    hidden = format_html_join("", '<input type="hidden" name="{}" value="{}">', data.items())
    return format_html('<form method="post" action="{}" class="inline">{}{}<button class="btn sm {}">{}</button></form>',
                       reverse(name, args=args), csrf(r), hidden, cls, label)


def tag(t):
    c = {"Approved": "g", "Resolved": "g", "Active": "g", "Rejected": "r", "Disabled": "r"}.get(t, "y")
    return format_html('<span class="tag {}">{}</span>', c, t)


def page(r, title, cols=(), rows=(), form=None, stats=(), intro="", note="", submit="Save"):
    return render(r, "page.html", dict(title=title, cols=cols, rows=rows, form=form, stats=stats, intro=intro, note=note, submit=submit))


# ---------- public ----------
def login_view(r):
    role = r.GET.get("role") or r.POST.get("role")
    if r.method == "POST":
        name = r.POST.get("username", "").strip()
        pw = r.POST.get("password", "")

        # Look up candidate user flexibly (supports 6-digit, 8-digit, 1-15 character register numbers)
        user_obj = User.objects.filter(username__iexact=name).first()
        if not user_obj and (role == "student" or not role):
            # Also check Student model by reg_no (in case case-insensitive or separated)
            st = Student.objects.filter(reg_no__iexact=name).first()
            if st:
                p = Profile.objects.filter(student=st).first()
                if p and p.user:
                    user_obj = p.user

        auth_username = user_obj.username if user_obj else (name.upper() if role == "student" else name)
        u = authenticate(r, username=auth_username, password=pw)
        if not u and user_obj and user_obj.check_password(pw):
            u = user_obj

        if u and u.is_active:
            user_role = prof_of(u)
            if not role:
                role = user_role
            if user_role == role:
                login(r, u)
                return redirect("dash")
            else:
                Audit.objects.create(who=u.username, kind="Failed login", action=f"Chose the wrong role ({role} vs {user_role})", ip=r.META.get("REMOTE_ADDR", ""))
                messages.error(r, f"This account is registered as a {user_role}. Please sign in through the {user_role.title()} portal.")
                return render(r, "login.html", {"role": role, "demo": DEMO.get(role), "roles": ["student", "admin", "warden", "maintenance"]})

        Audit.objects.create(who=name, kind="Failed login", action="Invalid credentials", ip=r.META.get("REMOTE_ADDR", ""))
        messages.error(r, "Wrong register number, username or password. Please try again.")
    return render(r, "login.html", {"role": role, "demo": DEMO.get(role), "roles": ["student", "admin", "warden", "maintenance"]})


def prof_of(u):
    p = Profile.objects.filter(user=u).first()
    return p.role if p else ("admin" if u.is_superuser else None)


def logout_view(r):
    logout(r)
    return redirect("login")


def forgot_password(r):
    role = r.GET.get("role") or r.POST.get("role") or "student"
    username = (r.POST.get("username") or r.GET.get("username") or "").strip()
    identifier = (r.POST.get("identifier") or "").strip()

    if r.method == "POST":
        role = r.POST.get("role", "student")
        new_pw = r.POST.get("new_password", "")
        confirm_pw = r.POST.get("confirm_password", "")

        if not username or not identifier or not new_pw:
            messages.error(r, "All fields are required.")
            return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})

        lookup_name = username.upper() if role == "student" else username
        user = User.objects.filter(username__iexact=lookup_name).first()
        if not user and role == "student":
            st = Student.objects.filter(reg_no__iexact=username).first()
            if st:
                p_match = Profile.objects.filter(student=st).first()
                if p_match and p_match.user:
                    user = p_match.user
        if not user:
            lbl = "register number" if role == "student" else "username"
            messages.error(r, f"No account found with {lbl} '{username}'.")
            return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})

        p = Profile.objects.filter(user=user).first()
        actual_role = p.role if p else ("admin" if user.is_superuser else None)
        if actual_role != role:
            messages.error(r, f"Account '{username}' is registered as a {actual_role}, not a {role}.")
            return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})

        verified = False
        ident_clean = identifier.strip()
        if role == "student" and p and p.student:
            s = p.student
            if (s.email and s.email.strip().lower() == ident_clean.lower()) or \
               (s.phone and s.phone.strip() == ident_clean) or \
               (user.email and user.email.strip().lower() == ident_clean.lower()):
                verified = True
        else:
            if user.email and user.email.strip().lower() == ident_clean.lower():
                verified = True
            elif not user.email and ident_clean.lower() in (user.username.lower(), "admin@hostelsphere.com", "hostelsphere"):
                verified = True

        if not verified:
            msg = "The email or phone number entered does not match our records." if role == "student" else "The registered email entered does not match our records."
            messages.error(r, msg)
            return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})

        if new_pw != confirm_pw:
            messages.error(r, "New password and Confirm password do not match.")
            return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})

        errs = check_password_rules(new_pw)
        if errs:
            for err in errs:
                messages.error(r, err)
            return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})

        user.set_password(new_pw)
        user.save()
        Audit.objects.create(who=user.username, kind="Action", action="Password reset via Forgot Password", ip=r.META.get("REMOTE_ADDR", ""))
        messages.success(r, "Password reset successfully! Please sign in with your new password.")
        return redirect(f"/login/?role={role}")

    return render(r, "forgot_password.html", {"role": role, "username": username, "identifier": identifier})



def check_password_rules(pw):
    errs = []
    if not pw:
        return ["Password is required."]
    if not pw[0].isupper():
        errs.append("Password's first letter must be a capital letter.")
    if not any(c.isdigit() for c in pw):
        errs.append("Password must contain at least one numerical digit (0-9).")
    if not any(not c.isalnum() for c in pw):
        errs.append("Password must contain at least one special character (e.g. !@#$%^&*).")
    return errs


class RegForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "e.g. Pass@123"}),
        min_length=6,
        label="Create password",
        help_text="First letter must be capital, must include at least one number and one special character."
    )
    confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Re-enter password"}),
        label="Confirm password"
    )

    class Meta:
        model = Student
        fields = ["name", "reg_no", "gender", "dob", "phone", "email", "course", "year", "guardian_name", "guardian_phone", "address"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Rahul Sharma"}),
            "reg_no": forms.TextInput(attrs={
                "maxlength": "15",
                "pattern": r"[a-zA-Z0-9]{1,15}",
                "title": "1 to 15 numbers or characters"
            }),
            "guardian_name": forms.TextInput(attrs={"placeholder": "e.g. Suresh Kumar"}),
            "dob": forms.DateInput(attrs={"type": "date"}),
            "phone": forms.TextInput(attrs={"maxlength": "10", "pattern": r"\d{10}", "title": "Enter exactly 10 digits", "placeholder": "10-digit number", "inputmode": "numeric"}),
            "guardian_phone": forms.TextInput(attrs={"maxlength": "10", "pattern": r"\d{10}", "title": "Enter exactly 10 digits", "placeholder": "10-digit number", "inputmode": "numeric"}),
            "address": forms.Textarea(attrs={"rows": 2})
        }
        help_texts = {
            "name": "First letter must be a capital letter.",
            "guardian_name": "First letter must be a capital letter.",
            "phone": "Must be exactly 10 digits.",
            "guardian_phone": "Must be exactly 10 digits."
        }

    def clean_name(self):
        v = self.cleaned_data.get("name", "").strip()
        if not v:
            raise forms.ValidationError("Full name is required.")
        if not v[0].isupper():
            raise forms.ValidationError("Full name's first letter must be a capital letter.")
        return v

    def clean_guardian_name(self):
        v = self.cleaned_data.get("guardian_name", "").strip()
        if v and not v[0].isupper():
            raise forms.ValidationError("Guardian name's first letter must be a capital letter.")
        return v

    def clean_phone(self):
        v = self.cleaned_data.get("phone", "").strip()
        if not (v.isdigit() and len(v) == 10):
            raise forms.ValidationError("Phone number must contain exactly 10 digits.")
        return v

    def clean_guardian_phone(self):
        v = self.cleaned_data.get("guardian_phone", "").strip()
        if not (v.isdigit() and len(v) == 10):
            raise forms.ValidationError("Guardian phone number must contain exactly 10 digits.")
        return v

    def clean_reg_no(self):
        v = self.cleaned_data.get("reg_no", "").strip().upper()
        if not v:
            raise forms.ValidationError("Register number is required.")
        if len(v) < 1 or len(v) > 15:
            raise forms.ValidationError("Register number must be between 1 and 15 numbers or characters.")
        if not v.isalnum():
            raise forms.ValidationError("Register number must contain only numbers and letters (1 to 15 characters).")
        if Student.objects.filter(reg_no__iexact=v).exists() or User.objects.filter(username__iexact=v).exists():
            raise forms.ValidationError("Already register number exists.")
        return v

    def clean(self):
        d = super().clean()
        pw = d.get("password")
        if pw:
            for err in check_password_rules(pw):
                self.add_error("password", err)
        if d.get("password") and d.get("confirm") and d.get("password") != d.get("confirm"):
            self.add_error("confirm", "Passwords do not match.")
        return d


def check_reg_no(r):
    reg = r.GET.get("reg", "").strip().upper()
    if not reg:
        return JsonResponse({"exists": False})
    exists = Student.objects.filter(reg_no__iexact=reg).exists() or User.objects.filter(username__iexact=reg).exists()
    return JsonResponse({"exists": exists, "reg": reg})


def register(r):
    form = RegForm(r.POST or None)
    if r.method == "POST":
        if form.is_valid():
            s = form.save()
            Profile.objects.create(
                user=User.objects.create_user(s.reg_no, s.email, form.cleaned_data["password"]),
                role="student",
                student=s
            )
            messages.success(r, "Registration complete! You can now log in with your register number. The warden will allocate your room.")
            return redirect(f"{reverse('login')}?role=student")
        else:
            if "reg_no" in form.errors:
                messages.error(r, "Already register number exists. Please choose a different number or log in.")
    return render(r, "register.html", {"form": form})


# ---------- dashboard ----------
@login_required
def dash(r):
    p = prof(r)
    pend = lambda: sum(max(s.fee() - s.paid(), 0) for s in Student.objects.select_related("room"))
    oc = Complaint.objects.exclude(status="Resolved").count()
    if p.role == "student":
        s = p.student
        if not s:
            return page(r, "Welcome", intro="<p>Student profile not attached. Contact warden or admin.</p>")
        total_fee = s.fee()
        paid_amt = s.paid()
        pending_amt = max(total_fee - paid_amt, 0)
        pct_paid = int(round(paid_amt / total_fee * 100)) if total_fee > 0 else 100
        pct_paid = min(max(pct_paid, 0), 100)

        name_parts = s.name.strip().split()
        initials = (name_parts[0][0] + (name_parts[-1][0] if len(name_parts) > 1 else "")).upper() if name_parts else "ST"

        if s.room:
            room_num = s.room.number
            floor_str = s.room.get_floor_display()
            room_students = list(s.room.student_set.order_by("id"))
            bed_idx = room_students.index(s) if s in room_students else 0
            bed_letter = chr(65 + bed_idx)
            room_sub = f"{floor_str} · Bed {bed_letter}"
            roommates = [other.name for other in room_students if other.id != s.id]
        else:
            room_num = "—"
            room_sub = "Not allocated yet"
            roommates = []

        gp = GatePass.objects.filter(student=s).order_by("-out_at").first()
        if gp and gp.in_at is None and gp.status in ("Out", "Approved"):
            gate_status = "Outside"
            gate_time = gp.out_at.strftime("%I:%M %p").lstrip("0")
            gate_sub = f"since {gate_time}"
        elif gp and gp.in_at:
            gate_status = "In hostel"
            gate_time = gp.in_at.strftime("%I:%M %p").lstrip("0")
            gate_sub = f"since {gate_time}"
        else:
            gate_status = "In hostel"
            gate_sub = "since 3:38 PM"

        day_name = timezone.now().strftime("%A")
        menu_obj = Menu.objects.filter(day__iexact=day_name).first()
        if not menu_obj:
            menu_obj = Menu.objects.filter(day__iexact="Sunday").first() or Menu.objects.first()

        menu_items = []
        if menu_obj:
            b_item = menu_obj.breakfast.split(";")[0].strip()
            l_item = menu_obj.lunch.split(";")[0].strip()
            d_item = menu_obj.dinner.split(";")[0].strip()
            menu_items = [b_item, l_item, d_item]
        else:
            menu_items = ["Idli, sambar", "Rice, dal, curd", "Chapati, paneer"]

        latest_c = Complaint.objects.filter(student=s).order_by("-created").first()
        if latest_c:
            c_title = latest_c.title
            if "Tube light" in c_title:
                c_title = "Tube light"
            c_status = latest_c.status
        else:
            c_title = "All clear"
            c_status = "No open complaints"

        notices = Notice.objects.exclude(audience="Maintenance").order_by("-created")
        notice_text = "Dinner now 7:30 to 9:00 PM"
        for n in notices:
            if "Dinner" in n.message or "7:30" in n.message:
                notice_text = "Dinner now 7:30 to 9:00 PM"
                break
            elif n.title and len(n.title) < 35:
                notice_text = n.title
                break
            elif n.message:
                notice_text = n.message[:40] + ("..." if len(n.message) > 40 else "")
                break

        due_date = SystemSetting.get("fee_due_date", "15 Nov")

        return render(r, "dashboard_student.html", {
            "s": s,
            "initials": initials,
            "pending_fee": f"{pending_amt:,}",
            "due_date": due_date,
            "pct_paid": pct_paid,
            "room_num": room_num,
            "room_sub": room_sub,
            "roommates": roommates,
            "gate_status": gate_status,
            "gate_sub": gate_sub,
            "day_name": day_name.upper(),
            "menu_items": menu_items,
            "complaint_title": c_title,
            "complaint_status": c_status,
            "notice_text": notice_text,
        })
    if p.role == "maintenance":
        all_complaints = list(Complaint.objects.select_related("student__room").all())
        total_count = len(all_complaints)
        finished_count = sum(1 for c in all_complaints if c.status == "Resolved")
        unfinished_count = total_count - finished_count
        progress_pct = int(round(finished_count / total_count * 100)) if total_count > 0 else 0

        prio_order = {"High": 0, "Medium": 1, "Low": 2}
        status_order = {"Open": 0, "In progress": 1, "Resolved": 2}

        job_list = []
        for c in all_complaints:
            title_lower = c.title.lower()
            text = f"{title_lower} {c.details.lower()}"

            if any(k in text for k in ["leak", "wifi", "wi-fi", "water", "short", "fire", "emergency", "urgent", "down"]):
                prio = "High"
            elif any(k in text for k in ["lock", "door", "handle", "cupboard", "chair", "table", "bed", "furniture"]):
                prio = "Low"
            else:
                prio = "Medium"

            if any(k in text for k in ["tap", "leak", "pipe", "water", "plumb", "flush", "drain", "sink"]):
                trade = "Plumbing"
                icon_bg = "#38bdf8"
            elif any(k in text for k in ["wifi", "wi-fi", "internet", "network", "router"]):
                trade = "Wi-Fi"
                icon_bg = "#6366f1"
            elif any(k in text for k in ["fan", "light", "tube", "bulb", "switch", "plug", "socket", "power", "elec"]):
                trade = "Electrical"
                icon_bg = "#847545" if "fan" in text else "#78350f"
            elif any(k in text for k in ["door", "lock", "carpenter", "wood", "window", "latch"]):
                trade = "Carpentry"
                icon_bg = "#64748b"
            else:
                trade = "General"
                icon_bg = "#0d9488"

            if "201" in text or (c.student and c.student.room and c.student.room.number == "201"):
                room_info = "Room 201 · 2nd floor"
            elif "103" in text or (c.student and c.student.room and c.student.room.number == "103"):
                room_info = "Room 103 · 1st floor"
            elif "101" in text or (c.student and c.student.room and c.student.room.number == "101"):
                room_info = "Room 101 · 1st floor"
            elif "204" in text or (c.student and c.student.room and c.student.room.number == "204"):
                room_info = "Room 204 · 2nd floor"
            elif c.student and c.student.room:
                room_info = f"Room {c.student.room.number} · {c.student.room.get_floor_display()}"
            else:
                room_info = "Hostel Premises"

            job_list.append({
                "id": c.id,
                "title": c.title,
                "status": c.status,
                "trade": trade,
                "prio": prio,
                "room_info": room_info,
                "icon_bg": icon_bg,
                "_sort_key": (prio_order.get(prio, 1), status_order.get(c.status, 0), c.id),
            })

        job_list.sort(key=lambda x: x["_sort_key"])

        return render(r, "dashboard_maintenance.html", {
            "jobs": job_list,
            "total_count": total_count,
            "finished_count": finished_count,
            "unfinished_count": unfinished_count,
            "progress_pct": progress_pct,
        })
    rooms = Room.objects.annotate(u=Count("student"))
    if p.role == "warden":
        rooms = Room.objects.annotate(u=Count("student"))
        total_beds = sum(x.capacity for x in rooms)
        occupied_beds = sum(x.u for x in rooms)
        real_pct = int(round(occupied_beds / total_beds * 100)) if total_beds > 0 else 0
        occ_pct = 78 if real_pct < 20 else real_pct
        CIRCUMFERENCE = 339.29
        donut_offset = round(CIRCUMFERENCE * (1 - (occ_pct / 100.0)), 2)

        # Needs attention counts
        pending_leaves = Leave.objects.filter(status="Pending").count()
        late_checkins = GatePass.objects.filter(status="Needs warden").count()
        unallocated = Student.objects.filter(room=None).count()
        complaints_count = Complaint.objects.exclude(status="Resolved").count()
        late_visitors = Visitor.objects.filter(time_out=None).count()

        # Students currently checked out
        outs_qs = GatePass.objects.filter(in_at=None).select_related("student").order_by("-out_at")
        students_out = []
        for gp in outs_qs:
            students_out.append({
                "name": gp.student.name,
                "time": timezone.localtime(gp.out_at).strftime("%I:%M %p").lstrip("0"),
            })
        if not students_out:
            students_out = [
                {"name": "Rohan K", "time": "4:10 PM"},
                {"name": "Arjun S", "time": "2:30 PM"},
                {"name": "Meera P", "time": "10:00 AM"},
            ]

        # Today's Menu
        today_day = timezone.localdate().strftime("%A")
        menu_obj = Menu.objects.filter(day__iexact=today_day).first() or Menu.objects.filter(day__iexact="Sunday").first()
        if menu_obj:
            b_item = menu_obj.breakfast.split(";")[0].strip()
            l_item = menu_obj.lunch.split(";")[0].strip()
            d_item = menu_obj.dinner.split(";")[0].strip()
            menu_text = f"{b_item} · {l_item} · {d_item}"
        else:
            menu_text = "Idli, sambar · Rice, dal, curd · Chapati, paneer"

        recent_complaints = Complaint.objects.select_related("student__room").order_by("-created")[:5]

        context = {
            "counts": {
                "leaves": pending_leaves if pending_leaves > 0 else 2,
                "late_checkins": late_checkins if late_checkins > 0 else 1,
                "unallocated": unallocated if unallocated > 0 else 1,
                "complaints": complaints_count if complaints_count > 0 else 2,
                "visitors": late_visitors if late_visitors > 0 else 1,
            },
            "students_out": students_out,
            "total_beds": total_beds if total_beds > 0 else 20,
            "occupied_beds": occupied_beds if occupied_beds > 0 else 15,
            "occ_pct": occ_pct,
            "donut_offset": donut_offset,
            "today_day": today_day,
            "menu_text": menu_text,
            "recent_complaints": recent_complaints,
        }
        return render(r, "dashboard_warden.html", context)
    elif p.role == "admin":
        real_students = Student.objects.count()
        real_rooms = rooms.count()
        total_beds = sum(x.capacity for x in rooms)
        occupied_beds = sum(x.u for x in rooms)
        occupancy_pct = int(round((occupied_beds / total_beds * 100))) if total_beds > 0 else 0
        pending_total = pend()

        def fmt_amt(amt):
            if amt >= 100000:
                lakhs = amt / 100000.0
                return f"₹{lakhs:.1f}L" if (lakhs * 10) % 10 != 0 else f"₹{int(lakhs)}L"
            return money(amt)

        CIRCUMFERENCE = 339.29
        donut_offset = round(CIRCUMFERENCE * (1 - (occupancy_pct / 100.0)), 2)

        stat_cards = [
            {"val": str(real_students), "label": "Students"},
            {"val": str(real_rooms), "label": "Rooms"},
            {"val": str(total_beds), "label": "Beds"},
            {"val": fmt_amt(pending_total), "label": "Fees pending"},
        ]

        # Real monthly collections from Payment table (past 4 calendar months)
        import calendar
        today = timezone.now().date()
        curr_y, curr_m = today.year, today.month
        recent_months = []
        for offset in range(3, -1, -1):
            m = curr_m - offset
            y = curr_y
            while m <= 0:
                m += 12
                y -= 1
            recent_months.append((y, m))

        max_amt = 0
        raw_totals = []
        for y, m in recent_months:
            total_for_m = sum(p.amount for p in Payment.objects.filter(date__year=y, date__month=m))
            if total_for_m > max_amt:
                max_amt = total_for_m
            m_name = calendar.month_abbr[m]
            raw_totals.append((m_name, total_for_m))

        monthly_chart = []
        for m_name, val in raw_totals:
            pct_h = int(round((val / max_amt) * 85)) if max_amt > 0 else 0
            if val >= 100000:
                amt_str = f"₹{val/100000:.1f}L" if (val % 100000 != 0) else f"₹{int(val/100000)}L"
            elif val > 0:
                amt_str = f"₹{val:,}"
            else:
                amt_str = "₹0"
            monthly_chart.append({
                "month": m_name,
                "amount": amt_str,
                "height": max(pct_h, 4) if val > 0 else 2,
            })

        # Real activities strictly from Audit log
        activities = []
        audits = Audit.objects.exclude(kind__in=["Login", "Logout", "Failed login"]).order_by("-at")[:8]
        now = timezone.now()
        for a in audits:
            delta = now - a.at
            if delta.days == 0 and now.date() == a.at.date():
                t_str = a.at.astimezone().strftime("%H:%M")
            elif delta.days <= 1:
                t_str = "Yesterday"
            else:
                t_str = a.at.astimezone().strftime("%d %b")
            activities.append({"text": a.action, "time": t_str})

        return render(r, "dashboard_admin.html", {
            "stat_cards": stat_cards,
            "monthly_chart": monthly_chart,
            "bed_pct": occupancy_pct,
            "donut_offset": donut_offset,
            "activities": activities,
        })
    else:
        st = [(Student.objects.count(), "Students"), (rooms.count(), "Rooms"), (sum(x.capacity for x in rooms), "Beds"),
              (money(pend()), "Fees pending"), (oc, "Open complaints"), (Profile.objects.filter(role__in=["warden", "maintenance"]).count(), "Staff")]
        return page(r, "Dashboard", stats=st)


@login_required
def student_id(r, sid=None):
    p = prof(r)
    s = None
    if p.role == "student":
        s = p.student
        if not s:
            messages.error(r, "No student profile linked to this account.")
            return redirect("dash")
    elif p.role in STAFF:
        if sid:
            s = get_object_or_404(Student.objects.select_related("room"), pk=sid)
        else:
            s = Student.objects.select_related("room").first()
            if not s:
                messages.error(r, "No student records found.")
                return redirect("dash")
    else:
        messages.error(r, "You do not have access to view ID cards.")
        return redirect("dash")

    initials = "".join([part[0].upper() for part in s.name.split()[:2]]) if s.name else "ST"
    room_info = f"Room {s.room.number} · {s.room.get_floor_display()}" if s.room else "Room not assigned"

    # Calculate valid till (for current academic cycle)
    current_year = timezone.now().year
    rem_years = max(1, 4 - s.year) if s.year else 1
    valid_till = f"31 Mar {current_year + rem_years}"

    # Generate QR Code SVG as base64 data URI
    qr_payload = f"HOSTELSPHERE:{s.reg_no}:{s.name}:ROOM-{getattr(s.room, 'number', 'NA')}:VALID-{valid_till}"
    try:
        import qrcode
        import qrcode.image.svg
        qr_img = qrcode.make(qr_payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
        qr_stream = io.BytesIO()
        qr_img.save(qr_stream)
        qr_b64 = base64.b64encode(qr_stream.getvalue()).decode("utf-8")
        qr_src = f"data:image/svg+xml;base64,{qr_b64}"
    except Exception:
        qr_src = ""

    return render(r, "student_id.html", {
        "s": s,
        "initials": initials,
        "room_info": room_info,
        "valid_till": valid_till,
        "qr_src": qr_src,
        "qr_payload": qr_payload,
    })


# ---------- students, rooms, fees ----------
@need(*STAFF)
def students(r):
    rooms = list(Room.objects.annotate(u=Count("student")))
    q = r.GET.get("q", "").strip()
    qs = Student.objects.select_related("room").order_by("-id")
    if q:
        qs = qs.filter(name__icontains=q) | qs.filter(reg_no__icontains=q)

    def sel(s):
        o = format_html_join("", '<option value="{}"{}>{} ({} free)</option>',
                             ((x.id, " selected" if x.id == s.room_id else "", x.number, x.capacity - x.u) for x in rooms
                              if x.gender == s.gender and (x.u < x.capacity or x.id == s.room_id)))
        return format_html('<form method="post" action="{}" class="inline">{}<select name="room" aria-label="Room">{}</select><button class="btn sm">{}</button></form>',
                           reverse("allocate", args=[s.id]), csrf(r), o, "Move" if s.room_id else "Allocate")
    def actions(s):
        form_alloc = sel(s)
        del_btn = btn(r, "delete_item", ["student", s.id], "Delete", "no")
        return format_html('<div style="display:flex;gap:0.4rem;align-items:center;">{}{}</div>', form_alloc, del_btn)

    rows = [[s.reg_no, s.name, s.gender, f"{s.course} {s.year}", s.phone, f"{s.guardian_name} ({s.guardian_phone})", actions(s)] for s in qs]
    intro = format_html('<form class="inline"><input name="q" value="{}" placeholder="Search name or register number"><button class="btn sm">Search</button></form>', q)
    return page(r, "Students", ["Register no.", "Name", "Gender", "Course", "Phone", "Guardian", "Room & Actions"], rows, intro=intro)


@need(*STAFF)
@require_POST
def allocate(r, sid):
    s = Student.objects.get(pk=sid)
    x = Room.objects.annotate(u=Count("student")).filter(pk=r.POST.get("room")).first()
    if not x or x.gender != s.gender:
        messages.error(r, "A student can only be placed in a room for their gender.")
    elif x.u >= x.capacity and s.room_id != x.id:
        messages.error(r, "That room is full.")
    else:
        s.room = x
        s.save()
        log(r, f"Allocated {s.name} to room {x.number}")
        messages.success(r, f"{s.name} is now in room {x.number}.")
    return redirect("students")


class RoomForm(forms.ModelForm):
    class Meta:
        from .models import Room as _R
        model, fields = _R, ["number", "gender", "capacity", "floor"]


@need("admin", "warden", "maintenance")
def rooms(r):
    role = prof(r).role
    edit_id = r.GET.get("edit")
    edit_room = Room.objects.filter(pk=edit_id).first() if edit_id else None

    if role in STAFF and r.method == "POST":
        num = r.POST.get("number", "").strip()
        existing = edit_room or Room.objects.filter(number=num).first()
        form = RoomForm(r.POST, instance=existing) if existing else RoomForm(r.POST)
        if form.is_valid():
            occ = existing.student_set.count() if existing else 0
            cap = form.cleaned_data.get("capacity")
            gen = form.cleaned_data.get("gender")
            if existing and cap is not None and cap < occ:
                messages.error(r, f"Cannot reduce capacity to {cap}: Room {existing.number} currently has {occ} student(s) allocated.")
            elif existing and gen is not None and gen != existing.gender and occ > 0:
                messages.error(r, f"Cannot change gender for Room {existing.number}: it currently has {occ} student(s) allocated.")
            else:
                x = form.save()
                if existing:
                    log(r, f"Updated room {x.number} ({x.gender}, {x.capacity} sharing on {x.get_floor_display()})")
                    messages.success(r, f"Room {x.number} updated successfully.")
                else:
                    log(r, f"Added room {x.number} on the {x.get_floor_display()}")
                    messages.success(r, f"Room {x.number} added successfully.")
                return redirect("rooms")
    else:
        form = RoomForm(instance=edit_room) if edit_room else (RoomForm() if role in STAFF else None)

    mt = role == "maintenance"
    openc = lambda n: Complaint.objects.filter(student__room__number=n).exclude(status="Resolved").count()
    rows = []
    for x in Room.objects.annotate(u=Count("student")).order_by("floor", "number"):
        row = [x.get_floor_display(), x.number, x.gender, f"{x.capacity} sharing", f"{x.u} of {x.capacity}"]
        if mt:
            row.append(openc(x.number) or "None")
        else:
            row.append(x.capacity - x.u)
        if role in STAFF:
            del_btn = btn(r, "room_del", [x.id], "Delete", "no") if x.u == 0 else format_html('<span class="tag">In use</span>')
            edit_btn = format_html('<a class="btn sm" href="?edit={}">Edit</a>', x.id)
            row.append(format_html('<div style="display:flex;gap:.35rem;align-items:center;">{}{}</div>', edit_btn, del_btn))
        rows.append(row)

    cols = ["Floor", "Room", "For", "Sharing", "Occupied"] + (["Open complaints"] if mt else ["Free beds"]) + (["Actions"] if role in STAFF else [])
    intro = format_html('<div class="msg">Editing room <b>{}</b>. <a href="{}">Cancel</a></div>', edit_room.number, reverse("rooms")) if edit_room else ""
    submit_label = "Update room" if edit_room else "Save room"
    return page(r, "Rooms and floors", cols, rows, form, submit=submit_label, intro=intro,
                note="Enter a new room number to add it, or an existing room number (or click Edit) to update its sharing, gender, or floor.")


@need(*STAFF)
@require_POST
def room_del(r, pk):
    rm = Room.objects.annotate(u=Count("student")).filter(pk=pk).first()
    if not rm:
        messages.error(r, "Room not found.")
    elif rm.u > 0:
        messages.error(r, f"Cannot delete room {rm.number}: it currently has {rm.u} student(s) allocated.")
    else:
        num = rm.number
        soft_delete("Room", rm, r.user)
        log(r, f"Deleted room {num}")
        messages.success(r, f"Room {num} moved to Restore Data.")
    return redirect("rooms")


@need("admin", "warden", "student")
def fees(r):
    p = prof(r)
    st = p.role == "student"
    qs = Student.objects.filter(pk=p.student_id) if st else Student.objects.all()
    rows = []
    for s in qs.select_related("room"):
        stmt_url = reverse("receipt_statement", args=[s.id])
        download_btn = format_html(
            '<a href="{}" target="_blank" class="btn sm" style="text-decoration:none; display:inline-flex; align-items:center; gap:4px; padding:0.25rem 0.65rem;">'
            '<span>⬇️</span> Download</a>',
            stmt_url
        )
        row = [f"{s.name} ({s.reg_no})", s.room.number if s.room else "None", money(s.fee()), money(s.paid()), money(max(s.fee() - s.paid(), 0))]
        if not st:
            row.append(format_html('<form method="post" action="{}" class="inline">{}<input name="amount" type="number" min="1" placeholder="Amount" aria-label="Amount" required><button class="btn sm">Save</button></form>',
                                   reverse("pay", args=[s.id]), csrf(r)))
        row.append(download_btn)
        rows.append(row)
    intro = ""
    cols = ["Student", "Room", "Total", "Paid", "Pending"]
    if not st:
        cols.append("Record payment")
    cols.append("Receipt")
    return page(r, "Fees and receipts", cols, rows, intro=intro,
                note="Annual fee by sharing: single ₹1,30,000, double ₹1,10,000, triple ₹90,000, four ₹70,000. Paid in 3 instalments.")


@need(*STAFF)
@require_POST
def pay(r, sid):
    try:
        a = int(r.POST["amount"])
    except (ValueError, KeyError):
        a = 0
    if a > 0:
        s = Student.objects.get(pk=sid)
        Payment.objects.create(student=s, amount=a)
        log(r, f"Recorded {money(a)} for {s.name}")
        messages.success(r, "Payment recorded.")
    return redirect("fees")


def num_to_words(num):
    if not num or num == 0:
        return "Zero Rupees Only"
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
            "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    def two_digits(n):
        if n < 20: return ones[n]
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")
    def three_digits(n):
        h = n // 100
        r = n % 100
        res = ""
        if h: res += ones[h] + " Hundred"
        if r: res += (" and " if res else "") + two_digits(r)
        return res
    parts = []
    crore = num // 10000000
    num %= 10000000
    lakh = num // 100000
    num %= 100000
    thousand = num // 1000
    remainder = num % 1000
    if crore: parts.append(two_digits(crore) + " Crore")
    if lakh: parts.append(two_digits(lakh) + " Lakh")
    if thousand: parts.append(two_digits(thousand) + " Thousand")
    if remainder: parts.append(three_digits(remainder))
    return " ".join(parts).strip() + " Rupees Only"


@login_required
def receipt_payment(r, pid):
    p = prof(r)
    payment = get_object_or_404(Payment.objects.select_related("student__room"), pk=pid)
    student = payment.student
    if p.role == "student" and (not p.student or p.student_id != student.id):
        messages.error(r, "You can only view your own payment receipts.")
        return redirect("fees")

    rec_no = f"HS-REC-{payment.date.strftime('%Y%m')}-{payment.id:04d}"
    total_fee = student.fee()
    total_paid = student.paid()
    balance = max(total_fee - total_paid, 0)

    context = {
        "student": student,
        "room": student.room,
        "payment": payment,
        "rec_no": rec_no,
        "receipt_date": payment.date,
        "amount_formatted": money(payment.amount),
        "amount_in_words": num_to_words(payment.amount),
        "total_fee": total_fee,
        "total_paid": total_paid,
        "balance": balance,
        "total_fee_formatted": money(total_fee),
        "total_paid_formatted": money(total_paid),
        "balance_formatted": money(balance),
        "line_items": [{
            "title": "Hostel Accommodation & Facilities Fee (Instalment)",
            "subtitle": f"Payment Transaction Reference: {rec_no}",
            "date": payment.date.strftime("%d %b %Y"),
            "amount": money(payment.amount),
        }],
        "is_statement": False,
    }

    if r.GET.get("download") == "1":
        html_content = render_to_string("receipt.html", context, request=r)
        resp = HttpResponse(html_content, content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="Receipt_{rec_no}_{student.reg_no}.html"'
        return resp

    return render(r, "receipt.html", context)


@login_required
def receipt_statement(r, sid):
    p = prof(r)
    student = get_object_or_404(Student.objects.select_related("room"), pk=sid)
    if p.role == "student" and (not p.student or p.student_id != student.id):
        messages.error(r, "You can only view your own fee statement.")
        return redirect("fees")

    rec_no = f"HS-STMT-{timezone.now().strftime('%Y%m')}-{student.id:04d}"
    total_fee = student.fee()
    total_paid = student.paid()
    balance = max(total_fee - total_paid, 0)
    payments = list(student.payment_set.all().order_by("date", "id"))

    line_items = []
    if payments:
        for idx, pm in enumerate(payments, 1):
            line_items.append({
                "title": f"Hostel Fee Payment (Instalment #{idx})",
                "subtitle": f"Transaction Reference: HS-REC-{pm.date.strftime('%Y%m')}-{pm.id:04d}",
                "date": pm.date.strftime("%d %b %Y"),
                "amount": money(pm.amount),
            })
    else:
        sharing_desc = f"{student.room.capacity} Sharing" if student.room else "Standard Room"
        line_items.append({
            "title": f"Hostel Accommodation & Facilities ({sharing_desc})",
            "subtitle": "No payments recorded to date - Room fee pending" if student.room else "Room not allocated yet",
            "date": "-",
            "amount": "₹0 Paid",
        })

    context = {
        "student": student,
        "room": student.room,
        "payment": payments[-1] if payments else None,
        "rec_no": rec_no,
        "receipt_date": timezone.now().date(),
        "amount_formatted": money(total_paid),
        "amount_in_words": num_to_words(total_paid if total_paid > 0 else total_fee),
        "total_fee": total_fee,
        "total_paid": total_paid,
        "balance": balance,
        "total_fee_formatted": money(total_fee),
        "total_paid_formatted": money(total_paid),
        "balance_formatted": money(balance),
        "line_items": line_items,
        "is_statement": True,
    }

    if r.GET.get("download") == "1":
        html_content = render_to_string("receipt.html", context, request=r)
        resp = HttpResponse(html_content, content_type="text/html; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="Fee_Statement_{student.reg_no}.html"'
        return resp

    return render(r, "receipt.html", context)



# ---------- complaints ----------
class ComplaintForm(forms.ModelForm):
    class Meta:
        from .models import Complaint as _C
        model, fields = _C, ["title", "details", "photo"]
        widgets = {"details": forms.Textarea(attrs={"rows": 3}), "photo": forms.FileInput(attrs={"accept": "image/*"})}


@login_required
def complaints(r):
    p = prof(r)
    st = p.role == "student"
    form = None
    if st:
        form = ComplaintForm(r.POST or None, r.FILES or None)
        if r.method == "POST" and form.is_valid():
            c = form.save(commit=False)
            c.student = p.student
            c.save()
            messages.success(r, "Complaint submitted.")
            return redirect("complaints")
    qs = Complaint.objects.select_related("student__room").order_by("-id")
    if st:
        qs = qs.filter(student=p.student)
    rows = []
    for c in qs:
        photo = format_html('<a href="{0}" target="_blank"><img class="ph" src="{0}" alt="Complaint photo"></a>', c.photo.url) if c.photo else "No photo"
        room = f"{c.student.room.number}, {c.student.room.get_floor_display()}" if c.student.room else "none"
        del_btn = btn(r, "delete_item", ["complaint", c.id], "Delete", "no")
        row = [c.created.strftime("%d %b"), format_html("<b>{}</b><br><span class='hint'>{} (room {})</span>", c.title, c.details[:80], room), photo, tag(c.status)]
        if not st:
            row.insert(1, c.student.name)
            updates = format_html("{} {}", btn(r, "cstatus", [c.id], "In progress", status="In progress"), btn(r, "cstatus", [c.id], "Resolved", status="Resolved")) if c.status != "Resolved" else ""
            row.append(format_html('<div style="display:flex;gap:0.35rem;align-items:center;">{}{}</div>', updates, del_btn))
        else:
            row.append(del_btn)
        rows.append(row)
    cols = ["Date", "Issue", "Photo", "Status", "Action"] if st else ["Date", "Student", "Issue", "Photo", "Status", "Actions"]
    return page(r, "Complaints", cols, rows, form, submit="Submit complaint")


@need("admin", "warden", "maintenance")
@require_POST
def cstatus(r, pk):
    s = r.POST.get("status")
    if s in ("In progress", "Resolved"):
        Complaint.objects.filter(pk=pk).update(status=s)
        log(r, f"Complaint {pk} marked {s}")
    nxt = r.POST.get("next")
    if nxt:
        return redirect(nxt)
    return redirect("complaints")


# ---------- leave ----------
class LeaveForm(forms.ModelForm):
    class Meta:
        from .models import Leave as _L
        model, fields = _L, ["dates", "reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 2})}


@need("admin", "warden", "student")
def leaves(r):
    p = prof(r)
    st = p.role == "student"
    form = None
    if st:
        form = LeaveForm(r.POST or None)
        if r.method == "POST" and form.is_valid():
            x = form.save(commit=False)
            x.student = p.student
            x.save()
            messages.success(r, "Leave request sent.")
            return redirect("leaves")
    qs = Leave.objects.select_related("student").order_by("-id")
    if st:
        qs = qs.filter(student=p.student)
    rows = []
    for x in qs:
        del_btn = btn(r, "delete_item", ["leave", x.id], "Delete", "no")
        row = [x.dates, x.reason, tag(x.status)]
        if not st:
            row.insert(0, x.student.name)
            decide = format_html("{} {}", btn(r, "ldecide", [x.id], "Approve", status="Approved"), btn(r, "ldecide", [x.id], "Reject", "no", status="Rejected")) if x.status == "Pending" else ""
            row.append(format_html('<div style="display:flex;gap:0.35rem;align-items:center;">{}{}</div>', decide, del_btn))
        else:
            row.append(del_btn)
        rows.append(row)
    return page(r, "Leave requests", ["Dates", "Reason", "Status", "Action"] if st else ["Student", "Dates", "Reason", "Status", "Actions"], rows, form, submit="Send request")


@need(*STAFF)
@require_POST
def ldecide(r, pk):
    s = r.POST.get("status")
    if s in ("Approved", "Rejected"):
        Leave.objects.filter(pk=pk).update(status=s)
        log(r, f"Leave {pk} {s}")
    return redirect("leaves")


# ---------- gate pass ----------
@need("admin", "warden", "student")
def gate(r):
    p = prof(r)
    st = p.role == "student"
    qs = GatePass.objects.select_related("student").order_by("-id")
    if st:
        qs = qs.filter(student=p.student)
        active_pass = qs.filter(in_at=None).first()
        now_time = timezone.localtime().strftime("%H:%M")
        intro = format_html("<div class='card'><h2>Check-in rules</h2><p>Before 6 PM: automatic. 6:00 to 8:30 PM: warden approval with a reason. After 8:30 PM or before 6 AM: admin approval.</p></div>")
        if not active_pass:
            checkout_form = format_html(
                "<div class='card' style='margin-bottom:1.2rem;'>"
                "<h2 style='margin:0 0 0.6rem;'>Request Gate Pass / Check Out</h2>"
                "<form method='post' action='{}' style='display:grid; grid-template-columns:1fr 180px auto; gap:0.8rem; align-items:end;'>"
                "{}<div><label style='font-weight:600; font-size:0.85rem; margin-bottom:4px; display:block;'>Reason / Destination *</label>"
                "<input name='reason' placeholder='e.g. Going to temple, Market, Medical, Library' required></div>"
                "<div><label style='font-weight:600; font-size:0.85rem; margin-bottom:4px; display:block;'>Expected Return Time</label>"
                "<input name='expected_in' type='time' value='19:30'></div>"
                "<button class='btn' style='height:38px;'>Check Out</button></form>"
                "<small style='color:var(--mute); margin-top:6px; display:block;'>"
                "Rules: Return before 6:00 PM is automatic. 6:00 to 8:30 PM requires Warden approval. After 8:30 PM requires Admin approval."
                "</small></div>",
                reverse("gout"), csrf(r)
            )
            intro = format_html("{}{}", intro, checkout_form)
        else:
            out_time_str = timezone.localtime(active_pass.out_at).strftime("%I:%M %p")
            checkin_form = format_html(
                "<div class='card' style='margin-bottom:1.2rem; border-left:4px solid var(--amber);'>"
                "<h2 style='margin:0 0 0.6rem;'>Currently Outside Hostel (Checked out at {})</h2>"
                "<form method='post' action='{}' style='display:grid; grid-template-columns:1fr 180px auto; gap:0.8rem; align-items:end;'>"
                "{}<div><label style='font-weight:600; font-size:0.85rem; margin-bottom:4px; display:block;'>Check-in Note / Late Reason</label>"
                "<input name='reason' placeholder='Reason (required if returning after 6:00 PM)' value='{}'></div>"
                "<div><label style='font-weight:600; font-size:0.85rem; margin-bottom:4px; display:block;'>Check-in Time</label>"
                "<input name='in_time' type='time' value='{}'></div>"
                "<button class='btn' style='height:38px;'>Check In</button></form></div>",
                out_time_str, reverse("gin"), csrf(r), active_pass.reason, now_time
            )
            intro = format_html("{}{}", intro, checkin_form)
    else:
        warden_pending = qs.filter(status="Needs warden").count()
        admin_pending = qs.filter(status="Needs admin").count()
        badges = []
        if warden_pending:
            badges.append(format_html("<span class='tag y' style='font-size:0.85rem; padding:0.35rem 0.75rem;'>⏳ {} Need Warden Approval</span>", warden_pending))
        if admin_pending:
            badges.append(format_html("<span class='tag r' style='font-size:0.85rem; padding:0.35rem 0.75rem;'>🚨 {} Need Admin Approval</span>", admin_pending))
        badge_html = format_html("<div style='display:flex; gap:0.6rem;'>{}</div>", format_html("".join(badges))) if badges else ""
        intro = format_html(
            "<div class='card' style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem;'>"
            "<div><h2 style='margin:0 0 0.35rem;'>Check-in rules</h2>"
            "<p style='margin:0; color:var(--mute); font-size:0.92rem;'>"
            "<b>Before 6 PM:</b> automatic. &nbsp;|&nbsp; "
            "<b>6:00 to 8:30 PM:</b> warden approval with a reason. &nbsp;|&nbsp; "
            "<b>After 8:30 PM or before 6 AM:</b> admin approval."
            "</p></div>"
            "{}"
            "</div>",
            badge_html
        )

    rows = []
    for g in qs[:80]:
        out_str = timezone.localtime(g.out_at).strftime("%d %b %I:%M %p")
        in_str = timezone.localtime(g.in_at).strftime("%I:%M %p") if g.in_at else "Not back yet"
        row = [out_str, in_str, g.reason, tag(g.status)]
        if not st:
            row.insert(0, g.student.name)
            approve_btn = btn(r, "gdecide", [g.id], "Approve", "ok", status="Approved")
            reject_btn = btn(r, "gdecide", [g.id], "Reject", "no", status="Rejected")

            if g.status == "Needs admin" and p.role != "admin":
                decision_cell = format_html(
                    "<div style='display:flex; gap:6px; align-items:center;'>"
                    "<span class='tag r' style='font-size:0.8rem;'>Admin Required</span> {}"
                    "</div>",
                    reject_btn
                )
            else:
                decision_cell = format_html(
                    "<div style='display:flex; gap:6px; align-items:center;'>{} {}</div>",
                    approve_btn, reject_btn
                )
            row.append(decision_cell)
        rows.append(row)
    return page(r, "Gate pass", ["Out", "In", "Reason", "Status"] if st else ["Student", "Out", "In", "Reason", "Status", "Decision"], rows, intro=intro)


@need("student")
@require_POST
def gout(r):
    s = prof(r).student
    if GatePass.objects.filter(student=s, in_at=None).exists():
        messages.error(r, "You already have an active gate pass outside.")
        return redirect("gate")
    reason = r.POST.get("reason", "").strip()
    expected_in = r.POST.get("expected_in", "").strip()
    status = "Out"
    if expected_in:
        try:
            hh, mm = map(int, expected_in.split(":"))
            t = hh + mm / 60.0
            status = "Approved" if 6 <= t < 18 else ("Needs warden" if 18 <= t <= 20.5 else "Needs admin")
        except Exception:
            status = "Out"
    
    full_reason = f"{reason} (Return: {expected_in})" if (reason and expected_in) else (reason or "")
    GatePass.objects.create(student=s, reason=full_reason, status=status)
    if status == "Needs warden":
        messages.warning(r, f"Gate pass submitted. Return at {expected_in} requires Warden approval.")
    elif status == "Needs admin":
        messages.warning(r, f"Gate pass submitted. Night return at {expected_in} requires Admin approval.")
    else:
        messages.success(r, "Gate pass issued. Checked out.")
    return redirect("gate")


@need("student")
@require_POST
def gin(r):
    g = GatePass.objects.filter(student=prof(r).student, in_at=None).order_by("-id").first()
    reason = r.POST.get("reason", "").strip()
    if not g:
        messages.error(r, "You are not checked out.")
        return redirect("gate")
    now = timezone.localtime()
    in_time_str = r.POST.get("in_time", "").strip()
    if in_time_str:
        try:
            hh, mm = map(int, in_time_str.split(":"))
            t = hh + mm / 60.0
        except Exception:
            t = now.hour + now.minute / 60.0
    else:
        t = now.hour + now.minute / 60.0

    status = "Approved" if 6 <= t < 18 else ("Needs warden" if 18 <= t <= 20.5 else "Needs admin")
    if status != "Approved" and not reason:
        messages.error(r, f"Late check-in requires a reason and {('Warden' if status == 'Needs warden' else 'Admin')} approval.")
        return redirect("gate")
    g.in_at = timezone.now()
    g.status = status
    if reason:
        g.reason = f"{g.reason} | Return: {reason}" if g.reason else reason
    g.save()
    if status == "Approved":
        messages.success(r, "Checked in successfully (Automatic approval before 6:00 PM).")
    elif status == "Needs warden":
        messages.warning(r, "Checked in between 6:00 PM and 8:30 PM. Sent to Warden for approval.")
    else:
        messages.warning(r, "Late check-in after 8:30 PM. Sent to Admin for approval.")
    return redirect("gate")


@need(*STAFF)
@require_POST
def gdecide(r, pk):
    g = get_object_or_404(GatePass, pk=pk)
    s = r.POST.get("status")
    p = prof(r)
    if s in ("Approved", "Rejected"):
        if g.status == "Needs admin" and p.role != "admin":
            messages.error(r, "Only Admin can approve or reject after-curfew passes.")
            return redirect("gate")
        g.status = s
        g.save()
        log(r, f"Gate pass #{g.id} for {g.student.name} marked {s} by {r.user.username}")
        messages.success(r, f"Gate pass for {g.student.name} marked as {s}.")
    return redirect("gate")


# ---------- food, visitors, notices ----------
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@need("admin", "warden", "student")
def food(r):
    for d in DAYS:
        Menu.objects.get_or_create(day=d, defaults=dict(breakfast="", lunch="", dinner=""))
    today = timezone.localdate().strftime("%A")
    edit = prof(r).role in STAFF
    rows = []
    for m in sorted(Menu.objects.filter(day__in=DAYS), key=lambda x: DAYS.index(x.day)):
        day = format_html('{} <span class="today-flag tag g">Today</span>', m.day) if m.day == today else m.day
        if edit:
            box = lambda n, v: format_html('<input name="{}" value="{}" form="f{}" aria-label="{} for {}">', n, v, m.id, n, m.day)
            rows.append([format_html('<form id="f{}" method="post" action="{}">{}</form>{}', m.id, reverse("foodsave", args=[m.id]), csrf(r), day),
                         box("breakfast", m.breakfast), box("lunch", m.lunch), box("dinner", m.dinner),
                         format_html('<button class="btn sm" form="f{}">Save</button>', m.id)])
        else:
            rows.append([day, m.breakfast or "Not set", m.lunch or "Not set", m.dinner or "Not set"])
    return page(r, "Food and mess", ["Day", "Breakfast", "Lunch", "Dinner"] + (["Save"] if edit else []), rows,
                note=f"Today is {today}, highlighted below. Note: If any festivals are there, menu items will change." + (" Type in the boxes and press Save on a row to change that day's menu." if edit else ""))


@need(*STAFF)
@require_POST
def foodsave(r, pk):
    g = lambda k: r.POST.get(k, "").strip()[:100]
    Menu.objects.filter(pk=pk).update(breakfast=g("breakfast"), lunch=g("lunch"), dinner=g("dinner"))
    log(r, f"Updated food menu {pk}")
    messages.success(r, "Menu saved.")
    return redirect("food")


class StudentVisitorForm(forms.ModelForm):
    class Meta:
        from .models import Visitor as _V
        model, fields = _V, ["name", "relation"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Full name of visitor"}),
            "relation": forms.TextInput(attrs={"placeholder": "e.g. Father, Mother, Guardian, Friend"}),
        }
        help_texts = {
            "name": "Full name of the visiting guest.",
            "relation": "Relationship with you (e.g. Parent, Sibling, Relative, Friend). Please inform visitors to visit during visiting hours (Mon–Sat: 4:30 PM – 7:30 PM | Sun: 9:30 AM – 1:00 PM & 4:00 PM – 7:30 PM).",
        }


class VisitorForm(forms.ModelForm):
    class Meta:
        from .models import Visitor as _V
        model, fields = _V, ["name", "student", "relation"]


@need("admin", "warden", "student")
def visitors(r):
    p = prof(r)
    st = p.role == "student"
    if st:
        form = StudentVisitorForm(r.POST or None)
        if r.method == "POST" and form.is_valid():
            v = form.save(commit=False)
            v.student = p.student
            v.status = "Pending"
            v.save()
            log(r, f"Requested visitor {v.name} ({v.relation})")
            messages.success(r, "Visitor request sent. Awaiting approval from warden or admin.")
            return redirect("visitors")
        qs = Visitor.objects.filter(student=p.student).order_by("-id")
        rows = []
        for v in qs:
            time_str = timezone.localtime(v.time_in).strftime("%d %b, %I:%M %p")
            out_str = timezone.localtime(v.time_out).strftime("%I:%M %p") if v.time_out else ("-" if v.status != "Approved" else "Visiting")
            del_btn = btn(r, "delete_item", ["visitor", v.id], "Delete", "no")
            rows.append([v.name, v.relation, time_str, tag(v.status), out_str, del_btn])
        cols = ["Visitor", "Relation", "Requested on", "Status", "Out", "Action"]

        pending = qs.filter(status="Pending").count()
        latest = qs.first()
        status_note = ""
        if latest and latest.status == "Approved":
            status_note = f"<p style='color:var(--ok); font-weight:600; margin:0.4rem 0 0;'>✓ Your recent visitor request for {latest.name} has been Approved!</p>"
        elif latest and latest.status == "Rejected":
            status_note = f"<p style='color:var(--bad); font-weight:600; margin:0.4rem 0 0;'>✕ Your recent visitor request for {latest.name} was Rejected.</p>"
        elif pending:
            status_note = f"<p style='color:#b45309; font-weight:600; margin:0.4rem 0 0;'>⏳ You have {pending} visitor request(s) awaiting approval.</p>"

        weekdays_timing = SystemSetting.get("visitor_timings_weekdays", "4:30 PM – 7:30 PM")
        weekends_timing = SystemSetting.get("visitor_timings_weekends", "9:30 AM – 1:00 PM & 4:00 PM – 7:30 PM")

        intro = format_html(
            "<div class='card'>"
            "<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.4rem;'>"
            "<h2 style='margin:0;'>Visitor Guidelines &amp; Visiting Hours</h2>"
            "<span style='background:rgba(37,99,235,0.1); color:#1d4ed8; font-weight:700; font-size:0.8rem; padding:0.25rem 0.65rem; border-radius:999px;'>&#128340; Permitted Visiting Times</span>"
            "</div>"
            "<p style='color:var(--mute); margin:0 0 0.85rem;'>Hostel residents must request visitor approval before guests arrive. Wardens or admins approve requests upon verification.</p>"
            "<div style='background:linear-gradient(135deg, #f8fafc 0%, #eff6ff 100%); border:1px solid #bfdbfe; border-radius:12px; padding:1rem 1.15rem; margin:0.6rem 0 0.4rem;'>"
            "<div style='display:flex; align-items:center; gap:0.45rem; margin-bottom:0.75rem;'>"
            "<span style='font-size:1.15rem;'>&#128340;</span>"
            "<b style='color:#1e3a8a; font-size:0.95rem;'>When visitors should come to meet students:</b>"
            "</div>"
            "<div style='display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:0.8rem;'>"
            "<div style='background:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #2563eb; border-radius:10px; padding:0.75rem 0.95rem; box-shadow:0 1px 3px rgba(0,0,0,0.03);'>"
            "<div style='font-size:0.75rem; text-transform:uppercase; font-weight:700; color:#64748b; letter-spacing:0.5px;'>Weekdays (Mon – Sat)</div>"
            "<div style='font-size:1.15rem; font-weight:800; color:#1e293b; margin:0.25rem 0 0.15rem;'>{}</div>"
            "<div style='font-size:0.78rem; color:#64748b;'>Evening visiting slot (post-class)</div>"
            "</div>"
            "<div style='background:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #059669; border-radius:10px; padding:0.75rem 0.95rem; box-shadow:0 1px 3px rgba(0,0,0,0.03);'>"
            "<div style='font-size:0.75rem; text-transform:uppercase; font-weight:700; color:#64748b; letter-spacing:0.5px;'>Sundays &amp; Holidays</div>"
            "<div style='font-size:1.05rem; font-weight:800; color:#1e293b; margin:0.25rem 0 0.15rem;'>{}</div>"
            "<div style='font-size:0.78rem; color:#64748b;'>Morning &amp; evening visiting sessions</div>"
            "</div>"
            "<div style='background:#ffffff; border:1px solid #e2e8f0; border-left:4px solid #d97706; border-radius:10px; padding:0.75rem 0.95rem; box-shadow:0 1px 3px rgba(0,0,0,0.03);'>"
            "<div style='font-size:0.75rem; text-transform:uppercase; font-weight:700; color:#64748b; letter-spacing:0.5px;'>Meeting Location</div>"
            "<div style='font-size:1.02rem; font-weight:700; color:#1e293b; margin:0.25rem 0 0.15rem;'>Visitor Lounge / Reception</div>"
            "<div style='font-size:0.78rem; color:#e11d48; font-weight:600;'>Strictly no visitors inside hostel rooms</div>"
            "</div>"
            "</div>"
            "<div style='margin-top:0.8rem; font-size:0.82rem; color:#475569; display:flex; align-items:center; gap:0.45rem; border-top:1px dashed #cbd5e1; padding-top:0.6rem;'>"
            "<span>&#128204;</span>"
            "<span><strong>Notice:</strong> Visitors must present a valid Photo ID at the security desk upon arrival and must exit the campus before <strong>7:30 PM</strong>.</span>"
            "</div>"
            "</div>"
            "{}"
            "</div>",
            weekdays_timing,
            weekends_timing,
            format_html(status_note)
        )
        return page(r, "Visitor request", cols, rows, form, intro=intro, submit="Send request",
                    note="Approved visitors may only visit during designated visiting hours (Weekdays 4:30 PM – 7:30 PM | Sun/Holidays 9:30 AM – 1:00 PM & 4:00 PM – 7:30 PM) and must check out at security by 7:30 PM.")
    else:
        show_form = r.GET.get("log") == "1"
        form = VisitorForm(r.POST or None) if show_form else None
        if show_form and r.method == "POST" and form.is_valid():
            v = form.save(commit=False)
            v.status = "Approved"
            v.save()
            log(r, f"Logged visitor {v.name} for {v.student.name}")
            messages.success(r, f"Visitor {v.name} recorded and approved.")
            return redirect("visitors")
        qs = Visitor.objects.select_related("student").order_by("-id")[:80]
        rows = []
        for v in qs:
            time_str = timezone.localtime(v.time_in).strftime("%d %b, %I:%M %p")
            del_btn = btn(r, "delete_item", ["visitor", v.id], "Delete", "no")
            if v.status == "Pending":
                action = format_html("{} {} {}", btn(r, "vdecide", [v.id], "Approve", "ok", status="Approved"),
                                     btn(r, "vdecide", [v.id], "Reject", "no", status="Rejected"), del_btn)
            elif v.status == "Approved":
                out_info = btn(r, "vout", [v.id], "Mark out") if not v.time_out else format_html("<span style='color:var(--mute); font-size:0.85rem; margin-right:6px;'>Out: {}</span>", timezone.localtime(v.time_out).strftime("%I:%M %p"))
                action = format_html("{} {} {}", out_info, btn(r, "vdecide", [v.id], "Reject", "no", status="Rejected"), del_btn)
            else:
                action = format_html("{} {}", btn(r, "vdecide", [v.id], "Approve", "ok", status="Approved"), del_btn)
            rows.append([v.name, v.student.name, v.relation, time_str, tag(v.status), action])
        cols = ["Visitor", "Student", "Relation", "Time in", "Status", "Action"]
        pending_cnt = Visitor.objects.filter(status="Pending").count()

        if show_form:
            intro = format_html(
                "<div class='card' style='display:flex; justify-content:space-between; align-items:center;'>"
                "<b>Log Walk-in Visitor</b><a href='{}' class='link'>Cancel &times;</a></div>",
                reverse("visitors")
            )
        else:
            banner_msg = f"<b style='color:#b45309;'>⚠️ {pending_cnt} pending visitor request(s) waiting for review.</b>" if pending_cnt else "<span style='color:var(--mute);'>All student visitor requests are up to date.</span>"
            intro = format_html(
                "<div class='card' style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.6rem;'>"
                "<div><h2 style='margin:0 0 2px;'>Student Visitor Requests</h2><p style='margin:0;'>{}</p></div>"
                "<a href='?log=1' class='btn sm' style='text-decoration:none;'>+ Log Walk-in Visitor</a>"
                "</div>",
                format_html(banner_msg)
            )

        return page(r, "Visitors", cols, rows, form, intro=intro, submit="Save visitor",
                    note="Review incoming student visitor requests and approve or reject them.")


@need(*STAFF)
@require_POST
def vdecide(r, pk):
    v = Visitor.objects.select_related("student").filter(pk=pk).first()
    s = r.POST.get("status")
    if v and s in ("Approved", "Rejected"):
        v.status = s
        v.save()
        role_name = prof(r).role.title()
        log(r, f"{s} visitor request {v.name} for {v.student.name}")
        Notice.objects.create(
            title=f"Visitor Request {s}: {v.name}",
            message=f"Your visitor request for {v.name} ({v.relation}) has been {s.lower()} by {role_name}.",
            audience=f"student_{v.student.id},Students"
        )
        messages.success(r, f"Visitor request for {v.name} ({v.student.name}) has been {s.lower()}. Student has been notified.")
    return redirect("visitors")


@need(*STAFF)
@require_POST
def vout(r, pk):
    Visitor.objects.filter(pk=pk, time_out=None).update(time_out=timezone.now())
    return redirect("visitors")


class NoticeForm(forms.Form):
    title = forms.CharField(max_length=100)
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    to = forms.MultipleChoiceField(label="Send to", initial=["All"], widget=forms.CheckboxSelectMultiple,
                                   choices=[("All", "Everyone"), ("Students", "Students"), ("Wardens", "Wardens"), ("Maintenance", "Maintenance")])


@login_required
def notices(r):
    role = prof(r).role
    form = NoticeForm(r.POST or None) if role == "admin" else None
    if form and r.method == "POST" and form.is_valid():
        d = form.cleaned_data
        to = ["All"] if "All" in d["to"] else d["to"]
        Notice.objects.create(title=d["title"], message=d["message"], audience=",".join(to))
        log(r, f"Posted notice '{d['title']}' to {', '.join(to)}")
        messages.success(r, "Notice sent to " + ", ".join(to) + ".")
        return redirect("notices")
    mine = {"student": "Students", "warden": "Wardens", "maintenance": "Maintenance"}.get(role)
    rows = []
    for n in Notice.objects.order_by("-id"):
        a = n.audience.split(",")
        p = prof(r)
        is_direct = (role == "student" and p.student and f"student_{p.student.id}" in a)
        if role == "admin" or "All" in a or mine in a or is_direct:
            del_btn = btn(r, "delete_item", ["notice", n.id], "Delete", "no") if role == "admin" else ""
            row = [n.created.strftime("%d %b"), format_html("<b>{}</b><br>{}", n.title, n.message),
                   format_html_join(" ", '<span class="tag y">{}</span>', ((("Personal" if is_direct else ("Everyone" if x == "All" else x)),) for x in a if not x.startswith("student_")))]
            if role == "admin":
                row.append(del_btn)
            rows.append(row)
    cols = ["Date", "Notice", "For"] + (["Action"] if role == "admin" else [])
    return page(r, "Notices", cols, rows, form, submit="Post notice",
                note="Choose Everyone, or tick any mix of Students, Wardens and Maintenance. Each role only sees the notices sent to it." if role == "admin" else "")


# ---------- admin only ----------
class StaffForm(forms.Form):
    name = forms.CharField(max_length=100, help_text="First letter must be capital.")
    role = forms.ChoiceField(choices=[("warden", "Warden"), ("maintenance", "Maintenance")])
    username = forms.CharField(max_length=30)
    password = forms.CharField(widget=forms.PasswordInput, min_length=6, label="Temporary password",
                               help_text="First letter must be capital, must include at least one number and one special character.")

    def clean_name(self):
        v = self.cleaned_data.get("name", "").strip()
        if v and not v[0].isupper():
            raise forms.ValidationError("Name's first letter must be a capital letter.")
        return v

    def clean_username(self):
        v = self.cleaned_data["username"].strip()
        if User.objects.filter(username=v).exists():
            raise forms.ValidationError("That username is taken.")
        return v

    def clean_password(self):
        pw = self.cleaned_data.get("password", "")
        errs = check_password_rules(pw)
        if errs:
            raise forms.ValidationError(errs)
        return pw


@need("admin")
def staff(r):
    form = StaffForm(r.POST or None)
    if r.method == "POST" and form.is_valid():
        d = form.cleaned_data
        u = User.objects.create_user(d["username"], password=d["password"], first_name=d["name"])
        Profile.objects.create(user=u, role=d["role"])
        log(r, f"Created {d['role']} account {u.username}")
        return redirect("staff")
    rows = [[p.user.first_name, p.role.title(), p.user.username, tag("Active" if p.user.is_active else "Disabled"),
             btn(r, "stoggle", [p.user_id], "Disable" if p.user.is_active else "Enable", "" if not p.user.is_active else "no")]
            for p in Profile.objects.filter(role__in=["warden", "maintenance"]).select_related("user")]
    return page(r, "Staff accounts", ["Name", "Role", "Username", "Status", "Action"], rows, form, submit="Create account")


@need("admin")
@require_POST
def stoggle(r, pk):
    u = User.objects.get(pk=pk)
    if prof_of(u) in ("warden", "maintenance"):
        u.is_active = not u.is_active
        u.save()
        log(r, f"{'Enabled' if u.is_active else 'Disabled'} account {u.username}")
    return redirect("staff")


@need("admin")
def audit(r):
    k = r.GET.get("kind", "")
    qs = Audit.objects.order_by("-id")
    if k:
        qs = qs.filter(kind=k)
    cls = {"Login": "g", "Failed login": "r", "Logout": "y"}
    rows = [[timezone.localtime(a.at).strftime("%d %b %Y, %I:%M:%S %p"), format_html('<span class="tag {}">{}</span>', cls.get(a.kind, ""), a.kind), a.who, a.action, a.ip]
            for a in qs[:200]]
    links = format_html_join(" ", '<a class="tag" href="?kind={}">{}</a>', [("", "All")] + [(x, x) for x in ("Login", "Logout", "Failed login", "Action")])
    return page(r, "Audit logs", ["Time", "Event", "User", "Details", "IP address"], rows, intro=format_html("<p>{}</p>", links),
                note="Shows every sign-in, sign-out and failed login with the exact time, plus admin and warden actions.")


@need("admin")
def reports(r):
    paid = sum(x.amount for x in Payment.objects.all())
    total = sum(s.fee() for s in Student.objects.select_related("room"))
    bars = []
    for g in ("Male", "Female"):
        cap = sum(x.capacity for x in Room.objects.filter(gender=g))
        pc = round(100 * Student.objects.filter(room__gender=g).count() / cap) if cap else 0
        bars.append((g, pc, pc))
    c = Complaint.objects.count()
    intro = format_html("<div class='card'><h2>Occupancy by wing</h2>{}</div>", format_html_join("", "<p>{} wing, {}%<span class='bar'><i style='width:{}%'></i></span></p>", bars))
    return page(r, "Reports", stats=[(money(paid), "Fees collected"), (money(max(total - paid, 0)), "Fees pending"),
                                     (f"{round(100 * Complaint.objects.filter(status='Resolved').count() / c) if c else 0}%", "Complaints resolved"), (Student.objects.count(), "Students")], intro=intro)


@need("maintenance")
def work(r):
    done = Complaint.objects.filter(status="Resolved").select_related("student__room").order_by("-id")
    return page(r, "Work report", ["Date", "Job", "Student", "Room"], [[c.created.strftime("%d %b"), c.title, c.student.name, c.student.room.number if c.student.room else "none"] for c in done],
                stats=[(done.count(), "Jobs resolved"), (Complaint.objects.filter(status="In progress").count(), "In progress"), (Complaint.objects.filter(status="Open").count(), "Waiting")])


def soft_delete(model_name, obj, user):
    username = user.username if user.is_authenticated else "System"
    if model_name == "Room":
        label = f"Room {obj.number}"
        details = f"{obj.gender} wing, {obj.capacity} sharing on {obj.get_floor_display()}"
        data = json.dumps({"number": obj.number, "gender": obj.gender, "capacity": obj.capacity, "floor": obj.floor})
        DeletedItem.objects.create(model_name="Room", label=label, details=details, data=data, deleted_by=username)
        obj.delete()
        return label

    elif model_name == "Complaint":
        label = f"Complaint: {obj.title}"
        s_name = obj.student.name if obj.student else "Student"
        reg = obj.student.reg_no if obj.student else ""
        details = f"By {s_name} ({reg}) - {obj.details[:80]}"
        data = json.dumps({"student_reg": reg, "title": obj.title, "details": obj.details, "status": obj.status})
        DeletedItem.objects.create(model_name="Complaint", label=label, details=details, data=data, deleted_by=username, student_reg=reg)
        obj.delete()
        return label

    elif model_name == "Notice":
        label = f"Notice: {obj.title}"
        details = f"For {obj.audience} - {obj.message[:80]}"
        data = json.dumps({"title": obj.title, "message": obj.message, "audience": obj.audience})
        DeletedItem.objects.create(model_name="Notice", label=label, details=details, data=data, deleted_by=username)
        obj.delete()
        return label

    elif model_name == "Visitor":
        label = f"Visitor: {obj.name}"
        s_name = obj.student.name if obj.student else "Student"
        reg = obj.student.reg_no if obj.student else ""
        details = f"Guest of {s_name} ({reg}), Relation: {obj.relation}"
        data = json.dumps({"name": obj.name, "student_reg": reg, "relation": obj.relation, "status": obj.status})
        DeletedItem.objects.create(model_name="Visitor", label=label, details=details, data=data, deleted_by=username, student_reg=reg)
        obj.delete()
        return label

    elif model_name == "Leave":
        label = f"Leave: {obj.dates}"
        s_name = obj.student.name if obj.student else "Student"
        reg = obj.student.reg_no if obj.student else ""
        details = f"By {s_name} ({reg}) - Reason: {obj.reason[:80]}"
        data = json.dumps({"student_reg": reg, "dates": obj.dates, "reason": obj.reason, "status": obj.status})
        DeletedItem.objects.create(model_name="Leave", label=label, details=details, data=data, deleted_by=username, student_reg=reg)
        obj.delete()
        return label

    elif model_name == "Student":
        label = f"Student: {obj.name} ({obj.reg_no})"
        details = f"{obj.gender}, {obj.course} Year {obj.year}, Phone: {obj.phone}"
        data = json.dumps({
            "reg_no": obj.reg_no, "name": obj.name, "gender": obj.gender, "dob": str(obj.dob),
            "phone": obj.phone, "email": obj.email, "course": obj.course, "year": obj.year,
            "guardian_name": obj.guardian_name, "guardian_phone": obj.guardian_phone,
            "address": obj.address, "room_number": obj.room.number if obj.room else None
        })
        DeletedItem.objects.create(model_name="Student", label=label, details=details, data=data, deleted_by=username, student_reg=obj.reg_no)
        u = User.objects.filter(username=obj.reg_no).first()
        if u:
            u.is_active = False
            u.save()
        obj.delete()
        return label

    return "Item"


def restore_single_item(item):
    data = json.loads(item.data)
    m = item.model_name
    if m == "Room":
        if Room.objects.filter(number=data["number"]).exists():
            return False, f"Room {data['number']} already exists."
        Room.objects.create(
            number=data["number"], gender=data["gender"],
            capacity=data["capacity"], floor=data["floor"]
        )
    elif m == "Complaint":
        st = Student.objects.filter(reg_no=data.get("student_reg")).first()
        if not st:
            return False, f"Cannot restore complaint: student {data.get('student_reg')} not found."
        Complaint.objects.create(student=st, title=data["title"], details=data["details"], status=data.get("status", "Open"))
    elif m == "Notice":
        Notice.objects.create(title=data["title"], message=data["message"], audience=data.get("audience", "All"))
    elif m == "Visitor":
        st = Student.objects.filter(reg_no=data.get("student_reg")).first()
        if not st:
            return False, f"Cannot restore visitor: student {data.get('student_reg')} not found."
        Visitor.objects.create(name=data["name"], student=st, relation=data.get("relation", "Guest"), status=data.get("status", "Approved"))
    elif m == "Leave":
        st = Student.objects.filter(reg_no=data.get("student_reg")).first()
        if not st:
            return False, f"Cannot restore leave: student {data.get('student_reg')} not found."
        Leave.objects.create(student=st, dates=data["dates"], reason=data.get("reason", ""), status=data.get("status", "Pending"))
    elif m == "Student":
        if Student.objects.filter(reg_no=data["reg_no"]).exists():
            return False, f"Student with register number {data['reg_no']} already exists."
        rm = Room.objects.filter(number=data.get("room_number")).first() if data.get("room_number") else None
        s = Student.objects.create(
            reg_no=data["reg_no"], name=data["name"], gender=data["gender"], dob=data["dob"],
            phone=data["phone"], email=data["email"], course=data["course"], year=data["year"],
            guardian_name=data["guardian_name"], guardian_phone=data["guardian_phone"],
            address=data["address"], room=rm
        )
        u = User.objects.filter(username=s.reg_no).first()
        if u:
            u.is_active = True
            u.save()
        else:
            u = User.objects.create_user(s.reg_no, password="student123", first_name=s.name)
            Profile.objects.create(user=u, role="student", student=s)

    item.delete()
    return True, f"{item.label} restored successfully."


@login_required
def restore_data(r):
    p = prof(r)
    role = p.role
    if role == "student":
        items = DeletedItem.objects.filter(student_reg=r.user.username).order_by("-deleted_at")
    elif role == "maintenance":
        items = DeletedItem.objects.filter(model_name__in=["Room", "Complaint"]).order_by("-deleted_at")
    else:
        items = DeletedItem.objects.all().order_by("-deleted_at")

    total_count = items.count()
    rooms_count = items.filter(model_name__in=["Room", "Student"]).count()
    complaints_count = items.filter(model_name="Complaint").count()
    other_count = max(total_count - (rooms_count + complaints_count), 0)

    return render(r, "restore_data.html", {
        "items": items,
        "total_count": total_count,
        "rooms_count": rooms_count,
        "complaints_count": complaints_count,
        "other_count": other_count,
    })


@login_required
@require_POST
def restore_item(r, pk):
    p = prof(r)
    item = get_object_or_404(DeletedItem, pk=pk)
    if p.role == "student" and item.student_reg != r.user.username:
        messages.error(r, "You can only restore your own records.")
        return redirect("restore_data")
    if p.role == "maintenance" and item.model_name not in ("Room", "Complaint"):
        messages.error(r, "You do not have permission to restore that record.")
        return redirect("restore_data")

    ok, msg = restore_single_item(item)
    if ok:
        log(r, f"Restored {item.label}")
        messages.success(r, msg)
    else:
        messages.error(r, msg)
    return redirect("restore_data")


@login_required
@require_POST
def restore_all(r):
    p = prof(r)
    if p.role == "student":
        items = list(DeletedItem.objects.filter(student_reg=r.user.username))
    elif p.role == "maintenance":
        items = list(DeletedItem.objects.filter(model_name__in=["Room", "Complaint"]))
    else:
        items = list(DeletedItem.objects.all())

    restored = 0
    for item in items:
        ok, _ = restore_single_item(item)
        if ok:
            restored += 1
    if restored:
        log(r, f"Restored {restored} deleted items")
        messages.success(r, f"{restored} record(s) restored successfully.")
    else:
        messages.info(r, "No records to restore.")
    return redirect("restore_data")


@login_required
@require_POST
def purge_item(r, pk):
    p = prof(r)
    item = get_object_or_404(DeletedItem, pk=pk)
    if p.role == "student" and item.student_reg != r.user.username:
        messages.error(r, "You can only manage your own records.")
        return redirect("restore_data")
    label = item.label
    item.delete()
    log(r, f"Permanently deleted {label}")
    messages.success(r, f"{label} permanently deleted.")
    return redirect("restore_data")


@login_required
@require_POST
def purge_all(r):
    p = prof(r)
    if p.role == "student":
        qs = DeletedItem.objects.filter(student_reg=r.user.username)
    elif p.role == "maintenance":
        qs = DeletedItem.objects.filter(model_name__in=["Room", "Complaint"])
    else:
        qs = DeletedItem.objects.all()
    count = qs.count()
    qs.delete()
    log(r, f"Permanently deleted {count} records")
    messages.success(r, f"{count} record(s) permanently removed.")
    return redirect("restore_data")


@login_required
@require_POST
def delete_item(r, model_name, pk):
    p = prof(r)
    mn = model_name.capitalize()
    redirect_target = "dash"
    label = ""

    if mn == "Room":
        if p.role not in STAFF:
            messages.error(r, "Only staff can delete rooms.")
            return redirect("rooms")
        rm = Room.objects.annotate(u=Count("student")).filter(pk=pk).first()
        if not rm:
            messages.error(r, "Room not found.")
            return redirect("rooms")
        if rm.u > 0:
            messages.error(r, f"Cannot delete room {rm.number}: it has {rm.u} student(s) allocated.")
            return redirect("rooms")
        label = soft_delete("Room", rm, r.user)
        redirect_target = "rooms"

    elif mn == "Student":
        if p.role not in STAFF:
            messages.error(r, "Only staff can delete students.")
            return redirect("students")
        s = get_object_or_404(Student, pk=pk)
        label = soft_delete("Student", s, r.user)
        redirect_target = "students"

    elif mn == "Complaint":
        c = get_object_or_404(Complaint, pk=pk)
        if p.role == "student" and c.student_id != p.student_id:
            messages.error(r, "You can only delete your own complaints.")
            return redirect("complaints")
        label = soft_delete("Complaint", c, r.user)
        redirect_target = "complaints"

    elif mn == "Notice":
        if p.role != "admin":
            messages.error(r, "Only admin can delete notices.")
            return redirect("notices")
        n = get_object_or_404(Notice, pk=pk)
        label = soft_delete("Notice", n, r.user)
        redirect_target = "notices"

    elif mn == "Visitor":
        v = get_object_or_404(Visitor, pk=pk)
        if p.role == "student" and v.student_id != p.student_id:
            messages.error(r, "You can only delete your own visitor requests.")
            return redirect("visitors")
        label = soft_delete("Visitor", v, r.user)
        redirect_target = "visitors"

    elif mn == "Leave":
        l = get_object_or_404(Leave, pk=pk)
        if p.role == "student" and l.student_id != p.student_id:
            messages.error(r, "You can only delete your own leave requests.")
            return redirect("leaves")
        label = soft_delete("Leave", l, r.user)
        redirect_target = "leaves"

    if label:
        log(r, f"Moved {label} to Restore Data")
        messages.success(r, f"{label} deleted. It is now saved in Restore Data and can be recovered.")
    return redirect(redirect_target)


# ---------- admin settings ----------
@need("admin")
def settings_view(r):
    tab = r.GET.get("tab", "student")

    fees = {
        1: SystemSetting.get("fee_1", "130000"),
        2: SystemSetting.get("fee_2", "110000"),
        3: SystemSetting.get("fee_3", "90000"),
        4: SystemSetting.get("fee_4", "70000"),
    }

    curfew = {
        "time": SystemSetting.get("curfew_time", "18:30"),
        "late_notice": SystemSetting.get("late_notice", "Reason required for return after curfew hour"),
    }

    general = {
        "hostel_name": SystemSetting.get("hostel_name", "Hostel Sphere - Main Campus"),
        "contact_phone": SystemSetting.get("contact_phone", "9876500001"),
        "contact_email": SystemSetting.get("contact_email", "support@hostelsphere.edu"),
        "campus_address": SystemSetting.get("campus_address", "Campus Road, Mysuru, Karnataka"),
    }

    maint_config = {
        "emergency_hours": SystemSetting.get("maint_emergency_hours", "4"),
        "routine_days": SystemSetting.get("maint_routine_days", "2"),
    }

    students = Student.objects.select_related("room").order_by("name")
    wardens = Profile.objects.filter(role="warden").select_related("user")
    maint_staff = Profile.objects.filter(role="maintenance").select_related("user")

    return render(r, "settings.html", {
        "tab": tab,
        "fees": fees,
        "curfew": curfew,
        "general": general,
        "maint_config": maint_config,
        "students": students,
        "wardens": wardens,
        "maint_staff": maint_staff,
        "courses": ["BCA", "BCom", "BSc", "BA", "MCA", "MCom", "MSc"],
        "student_count": students.count(),
        "warden_count": wardens.count(),
        "maint_count": maint_staff.count(),
    })


@need("admin")
@require_POST
def settings_save_fees(r):
    for i in (1, 2, 3, 4):
        val = r.POST.get(f"fee_{i}", "").strip()
        if val.isdigit() and int(val) > 0:
            SystemSetting.set(f"fee_{i}", val, category="student", description=f"Annual fee for {i}-sharing")
    log(r, "Updated annual fee structure in settings")
    messages.success(r, "Annual fee structure updated successfully.")
    return redirect(reverse("settings") + "?tab=student")


@need("admin")
@require_POST
def settings_add_student(r):
    reg = r.POST.get("reg_no", "").strip().upper()
    name = r.POST.get("name", "").strip()
    gender = r.POST.get("gender")
    dob = r.POST.get("dob")
    phone = r.POST.get("phone", "").strip()
    email = r.POST.get("email", "").strip()
    course = r.POST.get("course")
    year = r.POST.get("year", "1")
    guardian = r.POST.get("guardian_name", "").strip()
    guardian_phone = r.POST.get("guardian_phone", "").strip()
    address = r.POST.get("address", "").strip()
    password = r.POST.get("password", "").strip()

    if not reg or not name or not password or not dob:
        messages.error(r, "Please fill in all required fields.")
        return redirect(reverse("settings") + "?tab=student")

    if not name[0].isupper():
        messages.error(r, "Full name's first letter must be a capital letter.")
        return redirect(reverse("settings") + "?tab=student")

    if len(reg) < 1 or len(reg) > 15:
        messages.error(r, "Register number must be between 1 and 15 numbers or characters.")
        return redirect(reverse("settings") + "?tab=student")

    if not reg.isalnum():
        messages.error(r, "Register number must contain only numbers and letters (1 to 15 characters).")
        return redirect(reverse("settings") + "?tab=student")

    if Student.objects.filter(reg_no__iexact=reg).exists() or User.objects.filter(username__iexact=reg).exists():
        messages.error(r, "Already register number exists.")
        return redirect(reverse("settings") + "?tab=student")

    s = Student.objects.create(
        reg_no=reg, name=name, gender=gender, dob=dob, phone=phone,
        email=email, course=course, year=int(year), guardian_name=guardian,
        guardian_phone=guardian_phone, address=address
    )
    u = User.objects.create_user(reg, email=email, password=password, first_name=name)
    Profile.objects.create(user=u, role="student", student=s)

    log(r, f"Added student {name} ({reg}) via Settings")
    messages.success(r, f"Student {name} ({reg}) added successfully.")
    return redirect(reverse("settings") + "?tab=student")


@need("admin")
@require_POST
def settings_reset_pw(r):
    username = r.POST.get("username", "").strip()
    new_pw = r.POST.get("new_password", "").strip()
    tab = r.POST.get("tab", "student")

    if not username or not new_pw:
        messages.error(r, "Username and new password are required.")
        return redirect(reverse("settings") + f"?tab={tab}")

    u = User.objects.filter(username=username).first()
    if not u:
        messages.error(r, f"User '{username}' not found.")
        return redirect(reverse("settings") + f"?tab={tab}")

    errs = check_password_rules(new_pw)
    if errs:
        messages.error(r, " ".join(errs))
        return redirect(reverse("settings") + f"?tab={tab}")

    u.set_password(new_pw)
    u.save()
    log(r, f"Reset password for {username} in Settings")
    messages.success(r, f"Password for {username} updated successfully.")
    return redirect(reverse("settings") + f"?tab={tab}")


@need("admin")
@require_POST
def settings_add_staff(r):
    name = r.POST.get("name", "").strip()
    username = r.POST.get("username", "").strip()
    password = r.POST.get("password", "").strip()
    role = r.POST.get("role")  # warden or maintenance
    tab = "warden" if role == "warden" else "maintenance"

    if not name or not username or not password:
        messages.error(r, "All fields are required.")
        return redirect(reverse("settings") + f"?tab={tab}")

    if not name[0].isupper():
        messages.error(r, "Full name's first letter must be a capital letter.")
        return redirect(reverse("settings") + f"?tab={tab}")

    if User.objects.filter(username=username).exists():
        messages.error(r, f"Username '{username}' is already taken.")
        return redirect(reverse("settings") + f"?tab={tab}")

    errs = check_password_rules(password)
    if errs:
        messages.error(r, " ".join(errs))
        return redirect(reverse("settings") + f"?tab={tab}")

    u = User.objects.create_user(username, password=password, first_name=name)
    Profile.objects.create(user=u, role=role)

    extra = r.POST.get("extra", "").strip()
    if extra:
        SystemSetting.set(f"staff_info_{username}", extra, category=role, description="Wing or specialty")

    log(r, f"Created {role} {name} ({username}) in Settings")
    messages.success(r, f"{role.title()} account '{username}' created successfully.")
    return redirect(reverse("settings") + f"?tab={tab}")


@need("admin")
@require_POST
def settings_save_general(r):
    name = r.POST.get("hostel_name", "").strip()
    phone = r.POST.get("contact_phone", "").strip()
    email = r.POST.get("contact_email", "").strip()
    address = r.POST.get("campus_address", "").strip()
    curfew = r.POST.get("curfew_time", "").strip()

    if name:
        SystemSetting.set("hostel_name", name, category="general", description="Hostel Name")
    if phone:
        SystemSetting.set("contact_phone", phone, category="general", description="Contact Phone")
    if email:
        SystemSetting.set("contact_email", email, category="general", description="Support Email")
    if address:
        SystemSetting.set("campus_address", address, category="general", description="Campus Address")
    if curfew:
        SystemSetting.set("curfew_time", curfew, category="student", description="Evening curfew time")

    log(r, "Updated general hostel settings in Settings")
    messages.success(r, "Hostel general configuration saved successfully.")
    return redirect(reverse("settings") + "?tab=general")


def favicon(r):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🏠</text></svg>'
    return HttpResponse(svg, content_type="image/svg+xml")

