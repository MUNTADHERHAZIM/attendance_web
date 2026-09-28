import logging
import requests
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from .models import SyncQueue, AttendanceSession

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_offline_records(self):
    """
    Periodic Celery task to synchronize unsynced local SyncQueue records
    with the central cloud server. Retries up to 3 times on network failure.
    """
    cloud_url = getattr(settings, "CENTRAL_CLOUD_SYNC_URL", None)
    if not cloud_url:
        logger.warning("CENTRAL_CLOUD_SYNC_URL غير مهيأ. تم تخطي المزامنة.")
        return "سحابة المزامنة غير مهيأة"

    # ✅ FIX: was `.order_type` (wrong), now correctly uses `.order_by()`
    unsynced_items = SyncQueue.objects.filter(is_synced=False).order_by("created_at")
    if not unsynced_items.exists():
        return "لا توجد سجلات بحاجة للمزامنة"

    api_key = getattr(settings, "CENTRAL_CLOUD_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    success_count = 0
    fail_count = 0

    for item in unsynced_items:
        try:
            response = requests.post(
                cloud_url, json=item.payload, headers=headers, timeout=10
            )

            if response.status_code in [200, 201]:
                item.is_synced = True
                item.synced_at = timezone.now()
                item.save(update_fields=["is_synced", "synced_at"])
                success_count += 1
            else:
                logger.error(
                    f"فشل مزامنة العنصر #{item.record_id}: "
                    f"الخادم أعاد الحالة {response.status_code}"
                )
                fail_count += 1

        except requests.RequestException as e:
            logger.error(
                f"خطأ في الشبكة أثناء مزامنة العنصر #{item.record_id}: {str(e)}"
            )
            fail_count += 1
            # Retry the entire task if network is down
            raise self.retry(exc=e)

    return f"نجاح: {success_count} | فشل: {fail_count}"


@shared_task
def close_expired_sessions():
    """
    Periodic task (runs every minute via Celery Beat) to automatically
    close attendance sessions whose end_time has passed.
    """
    now = timezone.now()
    expired = AttendanceSession.objects.filter(is_active=True, end_time__lte=now)
    count = expired.count()

    if count > 0:
        expired.update(is_active=False)
        logger.info(f"تم إغلاق {count} جلسة تحضير منتهية الصلاحية تلقائياً.")

    return f"تم إغلاق {count} جلسة منتهية"
