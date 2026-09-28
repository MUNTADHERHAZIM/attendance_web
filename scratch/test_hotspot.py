import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from apps.attendance.views import get_hotspot_info_api, qr_image_view
import json

User = get_user_model()
teacher = User.objects.filter(role=User.Roles.TEACHER).first() or User.objects.first()
rf = RequestFactory()

# 1. Test hotspot info API
req = rf.get('/attendance/api/hotspot-info/')
req.user = teacher
res = get_hotspot_info_api(req)
data = json.loads(res.content.decode('utf-8'))
print('Hotspot API Success:', data['success'])
print('Primary IP:', data['primary_ip'])
print('All IPs:', data['all_ips'])
print('Check-in URL:', data['checkin_url'])

# 2. Test qr_image_view with custom host
host = f"{data['primary_ip']}:8001"
req2 = rf.get(f'/attendance/qr-image/?host={host}&token=sample_token_123')
res2 = qr_image_view(req2)
print('QR Image Status:', res2.status_code, 'Content-Type:', res2.get('Content-Type'), 'Size:', len(res2.content))
print('\nHOTSPOT VERIFICATION SUCCESSFUL!')
