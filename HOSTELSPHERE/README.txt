HOSTEL SPHERE (HTML, CSS, JavaScript front end; Python Django back end; MySQL database)

SETUP (Windows, Mac or Linux)
1. Install Python 3.10+ and MySQL 8.
2. Create the database:   mysql -u root -p < setup.sql
3. In this folder:        pip install -r requirements.txt
4. Tell the app your MySQL password (Windows: set DB_PASSWORD=yourpassword | Mac/Linux: export DB_PASSWORD=yourpassword)
   Optional: DB_USER (default root), DB_HOST (127.0.0.1), DB_PORT (3306), DB_NAME (hostel_sphere)
5. Create the tables:     python manage.py makemigrations hostel
                          python manage.py migrate
6. Run:                   python manage.py runserver
7. Open http://127.0.0.1:8000

To try it without MySQL, set USE_SQLITE=1 before steps 5 and 6.

LOGINS (demo data is created automatically on the first migrate): admin / admin123, warden / warden123, maint / maint123, students BCA101, BCA118, MCA007, BSC044 / student123
Students register at /register/ and log in with their register number. Change the default passwords after first login.
Admin and warden edit the food menu on the Food and mess page (today is highlighted). Admin can edit all data at /admin/ (log in with the admin account).
Complaint photos are saved in the media/ folder.
