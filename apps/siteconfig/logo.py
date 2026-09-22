"""Prüfung des hochgeladenen Logos.

Bewusst ohne Bildbibliothek: Pillow wäre eine weitere Abhängigkeit, nur um
eine Datei zu prüfen, die einmal im Jahr hochgeladen wird. Geprüft wird
deshalb die Signatur am Dateianfang und die Größe.
"""

# Ein Logo ist ein kleines Bild. Die Grenze hält die Datenbankzeile klein und
# die Seite schnell; sie ist großzügig für ein PNG in doppelter Auflösung.
MAX_LOGO_BYTES = 512 * 1024

# Nur Rasterbilder. SVG fehlt absichtlich: eine SVG-Datei ist ein Dokument,
# das Skripte enthalten kann, und läge unter derselben Herkunft wie das Tool.
_SIGNATUREN = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

ERLAUBTE_FORMATE = "PNG, JPEG, GIF oder WebP"


def erkenne_bildtyp(data: bytes) -> str | None:
    """Gibt den Medientyp zurück, oder None, wenn es kein erlaubtes Bild ist."""
    for signatur, content_type in _SIGNATUREN:
        if data.startswith(signatur):
            return content_type
    # WebP steht in einem RIFF-Rahmen: "RIFF", vier Byte Länge, dann "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
