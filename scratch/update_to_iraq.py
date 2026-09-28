import os
import sys
import django

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.accounts.models import User, StudentProfile, TeacherProfile, GuardianProfile
from apps.academics.models import Institution, AcademicYear

def update_to_iraq():
    print("Updating database to Iraqi data...")

    # 1. Update Institution
    inst = Institution.objects.first()
    if inst:
        inst.name = "جامعة بغداد - كلية علوم الحاسوب وتكنولوجيا المعلومات"
        inst.address = "بغداد، جمهورية العراق"
        inst.allowed_wifi_ssid = "UOB_Students_WiFi"
        inst.save()
        print(f"Updated Institution: {inst.name}, {inst.address}")

    # 2. Update Academic Year
    ay = AcademicYear.objects.first()
    if ay:
        ay.name = "2025/2026"
        ay.save()
        print(f"Updated Academic Year: {ay.name}")

    # 3. Update Teacher
    try:
        t_user = User.objects.get(username="teacher1")
        t_user.first_name = "حيدر"
        t_user.last_name = "الساعدي"
        t_user.email = "teacher1@uobaghdad.edu.iq"
        t_user.phone = "07724978301"
        t_user.save()
        print(f"Updated Teacher user: {t_user.get_full_name()} ({t_user.phone})")
    except User.DoesNotExist:
        pass

    # 4. Update Guardian
    try:
        g_user = User.objects.get(username="parent1")
        g_user.first_name = "علي"
        g_user.last_name = "الشمري"
        g_user.phone = "07812345678"
        g_user.save()
        
        g_prof = GuardianProfile.objects.filter(user=g_user).first()
        if g_prof:
            g_prof.address = "بغداد - الكرادة"
            g_prof.save()
        print(f"Updated Guardian: {g_user.get_full_name()}")
    except User.DoesNotExist:
        pass

    # 5. Update Students
    iraqi_students = {
        "student1": ("علي", "الكعبي", "07712345671"),
        "student2": ("كرار", "الزبيدي", "07712345672"),
        "student3": ("مصطفى", "الجابري", "07712345673"),
        "student4": ("حسين", "العبيدي", "07712345674"),
        "student5": ("يوسف", "السامرائي", "07712345675"),
    }
    for uname, (fn, ln, ph) in iraqi_students.items():
        try:
            s_user = User.objects.get(username=uname)
            s_user.first_name = fn
            s_user.last_name = ln
            s_user.email = f"{uname}@uobaghdad.edu.iq"
            s_user.phone = ph
            s_user.save()
            print(f"Updated Student: {s_user.get_full_name()} ({s_user.phone})")
        except User.DoesNotExist:
            pass

    # 6. Update Admin Users
    try:
        admin0 = User.objects.get(username="admin0")
        admin0.first_name = "Muntadher"
        admin0.last_name = "Hazim"
        admin0.phone = "07724978301"
        admin0.save()
        print("Updated admin0 user with Muntadher Hazim details.")
    except User.DoesNotExist:
        pass

    try:
        ksu_admin = User.objects.get(username="ksu_admin")
        ksu_admin.first_name = "منتظر"
        ksu_admin.last_name = "حازم"
        ksu_admin.phone = "07724978301"
        ksu_admin.email = "admin@uobaghdad.edu.iq"
        ksu_admin.save()
        print("Updated ksu_admin user to Iraqi developer admin.")
    except User.DoesNotExist:
        pass

    print("All updates to Iraq and Developer MUNTADHER HAZIM completed successfully!")

if __name__ == "__main__":
    update_to_iraq()
