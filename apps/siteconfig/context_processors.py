from .models import FooterLink, SiteSettings


def branding(request):
    """Stellt Kopf- und Fußzeile die gepflegten Werte bereit (Issue 77).

    Zwei kleine Abfragen je Seite, bewusst ohne Zwischenspeicher: ein
    Zwischenspeicher lebt je Arbeitsprozess, und eine gerade geänderte
    Fußzeile wäre dann je nach Prozess mal alt und mal neu. Der Gewinn wäre
    zwei Abfragen auf eine Seite, die ohnehin Zeiteinträge lädt.
    """
    site = SiteSettings.current()
    return {
        "site": site,
        "footer_links": list(FooterLink.objects.all()),
    }
