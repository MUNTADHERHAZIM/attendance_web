import threading

_thread_locals = threading.local()

def get_current_user():
    return getattr(_thread_locals, "user", None)

def get_current_ip():
    return getattr(_thread_locals, "ip_address", None)

class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.user = getattr(request, "user", None)
        
        # Get IP Address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")
        _thread_locals.ip_address = ip

        response = self.get_response(request)
        
        # Clean up to prevent leaks
        if hasattr(_thread_locals, "user"):
            del _thread_locals.user
        if hasattr(_thread_locals, "ip_address"):
            del _thread_locals.ip_address
            
        return response
