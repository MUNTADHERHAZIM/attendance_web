import hmac
import secrets
from io import BytesIO

from django.http import HttpResponse
from PIL import Image, ImageDraw, ImageFont

from .models import SystemSetting


CAPTCHA_SETTING_KEY = "ENABLE_CAPTCHA"
CAPTCHA_SESSION_PREFIX = "captcha_"
CAPTCHA_LENGTH = 6
CAPTCHA_CHARACTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def is_captcha_enabled():
    setting, _ = SystemSetting.objects.get_or_create(
        key=CAPTCHA_SETTING_KEY,
        defaults={
            "value": "true",
            "description": "تفعيل/تعطيل رمز التحقق البصري (Captcha)",
        },
    )
    return setting.value.strip().lower() != "false"


def consume_captcha(request, purpose, submitted_value):
    expected = request.session.pop(f"{CAPTCHA_SESSION_PREFIX}{purpose}", None)
    if not is_captcha_enabled():
        return True
    submitted_value = str(submitted_value or "").strip().upper()
    return bool(expected and submitted_value and hmac.compare_digest(expected, submitted_value))


def captcha_image_view(request):
    purpose = request.GET.get("purpose", "")
    if purpose not in {"login", "register"}:
        return HttpResponse(status=400)

    image = Image.new("RGB", (280, 64), "#f1f5f9")
    draw = ImageDraw.Draw(image)
    code = "".join(secrets.choice(CAPTCHA_CHARACTERS) for _ in range(CAPTCHA_LENGTH))
    request.session[f"{CAPTCHA_SESSION_PREFIX}{purpose}"] = code

    for _ in range(12):
        start = (secrets.randbelow(280), secrets.randbelow(64))
        end = (secrets.randbelow(280), secrets.randbelow(64))
        draw.line((start, end), fill="#cbd5e1", width=1)

    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()

    for index, character in enumerate(code):
        draw.text(
            (18 + index * 41, 9 + secrets.randbelow(10)),
            character,
            fill=secrets.choice(("#312e81", "#4338ca", "#0f766e", "#9f1239")),
            font=font,
        )

    output = BytesIO()
    image.save(output, format="PNG")
    response = HttpResponse(output.getvalue(), content_type="image/png")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response
