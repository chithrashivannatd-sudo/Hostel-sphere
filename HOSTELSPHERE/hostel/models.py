from django.contrib.auth.models import User
from django.db import models

FEES = {1: 130000, 2: 110000, 3: 90000, 4: 70000}  # annual fee by room sharing
GENDER = [("Male", "Male"), ("Female", "Female")]
COURSES = [(c, c) for c in ["BCA", "BCom", "BSc", "BA", "MCA", "MCom", "MSc"]]


class Room(models.Model):
    number = models.CharField(max_length=10, unique=True)
    gender = models.CharField("For", max_length=6, choices=GENDER)
    capacity = models.IntegerField("Sharing", choices=[(k, f"{k} sharing") for k in FEES])
    floor = models.IntegerField(default=1, choices=[(0, "Ground floor"), (1, "1st floor"), (2, "2nd floor"), (3, "3rd floor")])

    def __str__(self):
        return f"Room {self.number}"


class Student(models.Model):
    reg_no = models.CharField("Register number", max_length=20, unique=True)
    name = models.CharField("Full name", max_length=100)
    gender = models.CharField(max_length=6, choices=GENDER)
    dob = models.DateField("Date of birth")
    phone = models.CharField(max_length=10)
    email = models.EmailField()
    course = models.CharField(max_length=10, choices=COURSES)
    year = models.IntegerField(choices=[(i, i) for i in range(1, 5)])
    guardian_name = models.CharField(max_length=100)
    guardian_phone = models.CharField(max_length=10)
    address = models.TextField("Home address")
    room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.SET_NULL)

    def fee(self):
        if not self.room:
            return 0
        try:
            val = SystemSetting.get(f"fee_{self.room.capacity}")
            if val:
                return int(val)
        except Exception:
            pass
        return FEES.get(self.room.capacity, 0)

    def paid(self):
        return sum(p.amount for p in self.payment_set.all())

    def __str__(self):
        return f"{self.name} ({self.reg_no})"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=12)  # admin, warden, student, maintenance
    student = models.ForeignKey(Student, null=True, blank=True, on_delete=models.CASCADE)


class Payment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    amount = models.IntegerField()
    date = models.DateField(auto_now_add=True)


class Complaint(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    title = models.CharField("What is the problem?", max_length=150)
    details = models.TextField()
    photo = models.ImageField("Photo (optional)", upload_to="complaints/", blank=True)
    status = models.CharField(max_length=20, default="Open")
    created = models.DateTimeField(auto_now_add=True)


class Leave(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    dates = models.CharField(max_length=100)
    reason = models.TextField()
    status = models.CharField(max_length=20, default="Pending")
    created = models.DateTimeField(auto_now_add=True)


class GatePass(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    out_at = models.DateTimeField(auto_now_add=True)
    in_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="Out")  # Out, Approved, Needs warden, Needs admin, Rejected
    reason = models.CharField(max_length=200, blank=True)


class Notice(models.Model):
    title = models.CharField(max_length=100)
    message = models.TextField()
    audience = models.CharField("Send to", max_length=60)  # comma list: All, Students, Wardens, Maintenance
    created = models.DateTimeField(auto_now_add=True)


class Visitor(models.Model):
    name = models.CharField("Visitor name", max_length=100)
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    relation = models.CharField(max_length=50)
    status = models.CharField(max_length=20, default="Approved")  # Pending, Approved, Rejected
    time_in = models.DateTimeField(auto_now_add=True)
    time_out = models.DateTimeField(null=True, blank=True)


class Menu(models.Model):
    day = models.CharField(max_length=12)
    breakfast = models.CharField(max_length=100)
    lunch = models.CharField(max_length=100)
    dinner = models.CharField(max_length=100)


class Audit(models.Model):
    who = models.CharField(max_length=100)
    action = models.CharField(max_length=250)
    kind = models.CharField(max_length=15, default="Action")  # Login, Logout, Failed login, Action
    ip = models.CharField(max_length=45, blank=True)
    at = models.DateTimeField(auto_now_add=True)


class DeletedItem(models.Model):
    model_name = models.CharField(max_length=30)  # Room, Student, Complaint, Notice, Visitor, Leave
    label = models.CharField(max_length=150)
    details = models.TextField(blank=True)
    data = models.TextField()
    deleted_by = models.CharField(max_length=100, blank=True)
    student_reg = models.CharField(max_length=30, blank=True)
    deleted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.model_name}: {self.label}"


class SystemSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.TextField()
    category = models.CharField(max_length=30, default="general")  # general, student, warden, maintenance
    description = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.key}: {self.value}"

    @classmethod
    def get(cls, key, default=""):
        obj = cls.objects.filter(key=key).first()
        return obj.value if obj else default

    @classmethod
    def set(cls, key, value, category="general", description=""):
        cls.objects.update_or_create(key=key, defaults={"value": str(value), "category": category, "description": description})


