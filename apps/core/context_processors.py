from apps.academics.models import Institution

def system_context(request):
    """
    Global context processor to supply system institution name and logo
    across all templates in the entire platform.
    """
    institution = Institution.objects.first()
    return {
        "system_institution": institution,
        "system_institution_name": institution.name if institution else "جامعة بغداد",
        "system_institution_logo": institution.logo.url if (institution and institution.logo) else None,
    }
