# SIGA Ingestion Atelier V1 v6.4.1 telegram-buffer-compatible

Patch minimal du workflow principal existant pour améliorer la compatibilité avec le batch processor Telegram, sans toucher à la zone stable OCR / Drive / finalisation.

## Modifications
- `Normalize Input` accepte proprement `source = telegram_buffered`
- support conservé pour `payload.images[]` legacy JSON avec `content_base64`
- `sender.display_name` enrichi depuis `first_name` / `username`
- `message.text` accepte aussi `caption_text`
- conservation de `context.telegram`
- ajout non bloquant de `documents[]` / `document_count`

## Ce que le patch ne change pas
- branche multipart webhook existante
- boucle compression avant OCR
- OCR
- Drive final
- consolidation finale
