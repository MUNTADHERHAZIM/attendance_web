from rest_framework import serializers
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import StudentProfile, TeacherProfile, GuardianProfile

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "phone", "role", "role_display", "avatar"]
        read_only_fields = ["id", "role"]


class StudentProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    institution_name = serializers.CharField(source="institution.name", read_only=True)

    class Meta:
        model = StudentProfile
        fields = ["id", "user", "student_id", "institution", "institution_name", "birth_date", "rfid_card"]


class TeacherProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    institution_name = serializers.CharField(source="institution.name", read_only=True)

    class Meta:
        model = TeacherProfile
        fields = ["id", "user", "teacher_id", "institution", "institution_name", "specialization"]


class GuardianProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = GuardianProfile
        fields = ["id", "user", "address"]


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims
        token["username"] = user.username
        token["role"] = user.role
        token["role_display"] = user.get_role_display()
        token["full_name"] = user.get_full_name() or user.username
        
        # Add profile IDs for convenience in client applications
        if user.is_student() and hasattr(user, "student_profile"):
            token["student_id"] = user.student_profile.student_id
            token["profile_id"] = user.student_profile.id
            token["institution_id"] = user.student_profile.institution_id
        elif user.is_teacher() and hasattr(user, "teacher_profile"):
            token["teacher_id"] = user.teacher_profile.teacher_id
            token["profile_id"] = user.teacher_profile.id
            token["institution_id"] = user.teacher_profile.institution_id

        return token
