from django.contrib import admin
from . import models

for m in (models.Room, models.Student, models.Profile, models.Payment, models.Complaint, models.Leave,
          models.GatePass, models.Notice, models.Visitor, models.Menu, models.Audit):
    admin.site.register(m)
