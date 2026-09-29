import ipaddress
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.conf import settings

def generate_qr_token(attendance_session):
    """
    Generates a secure, timestamped signature containing the session ID and current salt.
    """
    signer = TimestampSigner()
    # Package the session ID and the current rotating salt
    data = f"{attendance_session.id}:{attendance_session.qr_salt}"
    return signer.sign(data)


def verify_qr_token(token_str, max_age=1800):
    """
    Verifies that:
    1. The signature is mathematically valid (uses settings.SECRET_KEY).
    2. The token is not expired (max_age 1800 seconds = 30 minutes).
    3. The token contains the active session ID.
    Supports raw signatures, URL-encoded tokens, full URLs, and unquoted strings.
    """
    if not token_str:
        return None, "رمز التحضير غير موجود"

    import urllib.parse
    token_str = str(token_str).strip().strip('"').strip("'")
    token_str = urllib.parse.unquote(token_str)

    # Extract token query param if a full URL was scanned
    if "token=" in token_str:
        try:
            parsed = urllib.parse.urlparse(token_str)
            qs = urllib.parse.parse_qs(parsed.query)
            if "token" in qs and qs["token"]:
                token_str = urllib.parse.unquote(qs["token"][0])
        except Exception:
            import re
            m = re.search(r'token=([^&]+)', token_str)
            if m:
                token_str = urllib.parse.unquote(m.group(1))

    # Clean any trailing whitespace or trailing slashes
    token_str = token_str.rstrip("/")

    signer = TimestampSigner()
    try:
        # Load model lazily to avoid circular imports
        from .models import AttendanceSession

        # Verify timestamp signature (valid for max_age seconds, default 30 minutes)
        try:
            unsigned_data = signer.unsign(token_str, max_age=max_age)
        except (BadSignature, SignatureExpired):
            # Also try without max_age if signature expired slightly or unquoting was needed
            unsigned_data = signer.unsign(urllib.parse.unquote(token_str), max_age=max_age)

        parts = unsigned_data.split(":")
        session_id = int(parts[0])

        attendance_session = AttendanceSession.objects.get(id=session_id, is_active=True)
        return attendance_session, None

    except SignatureExpired:
        return None, "انتهت صلاحية الرمز (يرجى مسح الرمز الجديد المتجدد)"
    except BadSignature:
        return None, "توقيع الرمز غير صالح"
    except (ValueError, IndexError, AttendanceSession.DoesNotExist):
        return None, "جلسة التحضير غير موجودة أو غير نشطة"


def is_ip_in_subnet(ip_str, subnet_str):
    """
    Checks if a client IP belongs to an allowed CIDR subnet (e.g. 192.168.4.0/24).
    """
    if not ip_str or not subnet_str:
        return True  # If no subnet restriction is configured, bypass checking
        
    try:
        # Normalize local IPv6-mapped IPv4 addresses (like ::ffff:192.168.1.1)
        if "::ffff:" in ip_str:
            ip_str = ip_str.replace("::ffff:", "")
        elif ip_str == "::1":
            ip_str = "127.0.0.1"
            
        ip = ipaddress.ip_address(ip_str)
        subnet = ipaddress.ip_network(subnet_str)
        return ip in subnet
    except ValueError:
        return False


def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """
    يحسب المسافة الجغرافية بالأمتار بين نقطتين (خط العرض وخط الطول) باستخدام صيغة هافرسين (Haversine Formula).
    Calculates great-circle distance between two GPS coordinates in meters.
    """
    import math
    try:
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        delta_phi = math.radians(float(lat2) - float(lat1))
        delta_lambda = math.radians(float(lon2) - float(lon1))

        # Earth's mean radius in meters
        R = 6371000.0

        a = (math.sin(delta_phi / 2.0) ** 2 +
             math.cos(phi1) * math.cos(phi2) * (math.sin(delta_lambda / 2.0) ** 2))
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

        return R * c
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def normalize_arabic_text(text):
    """
    يطبّع النصوص والأسماء العربية لإلغاء الفروقات الشائعة في الإملاء:
    1. توحيد الهمزات: [أ إ آ ٱ] -> ا
    2. توحيد التاء المربوطة والهاء: ة -> ه
    3. توحيد الياء والألف المقصورة: ى -> ي
    4. حذف التشكيل والحركات (الفتحة، الضمة، الكسرة، السكون، التنوين، الشدة)
    5. حذف التطويل (الكشيدة: ـ)
    6. إزالة المسافات الزائدة
    """
    import re
    if not text:
        return ""
    text = str(text).strip()
    
    # Remove Tashkeel (diacritics)
    tashkeel_pattern = re.compile(r'[\u064B-\u065F\u0670]')
    text = re.sub(tashkeel_pattern, '', text)
    
    # Remove Tatweel (Kashida)
    text = re.sub(r'\u0640', '', text)
    
    # Normalize Alef forms
    text = re.sub(r'[إأآٱ]', 'ا', text)
    
    # Normalize Taa Marbuta to Haa
    text = re.sub(r'ة', 'ه', text)
    
    # Normalize Alef Maqsura to Yaa
    text = re.sub(r'ى', 'ي', text)

    # Normalize multiple whitespaces
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text.lower()


def find_matching_student(query, class_section):
    """
    يبحث عن الطالب في الشعبة الدراسية المحددة:
    1. محاولة المطابقة المباشرة بالرقم الجامعي (student_id)
    2. محاولة المطابقة الدقيقة بالاسم العربي بعد التطبيع (Normalized Name)
    3. محاولة المطابقة الجزئية الذكية للأسماء المركبة (مثلاً إذا كتب الطالب اسمه الثلاثي)
    """
    if not query or not class_section:
        return None, "يرجى إدخال الاسم أو الرقم الجامعي"
        
    query_clean = str(query).strip()
    norm_query = normalize_arabic_text(query_clean)
    
    # All students enrolled in this section
    students = class_section.students.select_related("user").all()
    
    # 1. Match by student_id
    by_id = students.filter(student_id__iexact=query_clean).first()
    if by_id:
        return by_id, None

    # 2. Match by normalized name
    exact_matches = []
    fuzzy_matches = []
    
    query_tokens = [t for t in norm_query.split() if len(t) > 1]
    
    for std in students:
        full_name = std.user.get_full_name() or std.user.username
        norm_full_name = normalize_arabic_text(full_name)
        
        # Exact normalized match
        if norm_full_name == norm_query:
            exact_matches.append(std)
            continue
            
        # Token containment: all query tokens exist in the student's name
        if query_tokens and len(query_tokens) >= 2:
            target_tokens = norm_full_name.split()
            if all(q_tok in target_tokens for q_tok in query_tokens):
                fuzzy_matches.append(std)
                
    if len(exact_matches) == 1:
        return exact_matches[0], None
    elif len(exact_matches) > 1:
        return None, "يوجد أكثر من طالب بنفس الاسم المطابق في هذه الشعبة. يرجى إدخال الرقم الجامعي للتحديد بدقة."
        
    if len(fuzzy_matches) == 1:
        return fuzzy_matches[0], None
    elif len(fuzzy_matches) > 1:
        return None, "يوجد تشابه بين عدة أسماء في الشعبة. يرجى إدخال اسمك الثلاثي كاملاً أو كتابة رقمك الجامعي."
        
    return None, f"لم يتم العثور على طالب باسم '{query_clean}' في كشف هذه الشعبة ({class_section.name}). تأكد من كتابة اسمك الثلاثي كما هو مسجل أو أدخل رقمك الجامعي."


