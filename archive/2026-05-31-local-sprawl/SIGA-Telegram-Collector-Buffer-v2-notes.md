SIGA Telegram Collector Buffer v2

Corrections applied after first test:
- JSON payload files are no longer generated with Convert to File "toBinary" from plain text.
  The JSON file is now built directly in a Code node as a true UTF-8 JSON binary file.
  This fixes unreadable / corrupted JSON uploads.
- Added dedicated subfolder under SIGA_TEMP:
  SIGA_TEMP / SIGA_TELEGRAM_BUFFER
- Collector now searches/creates SIGA_TELEGRAM_BUFFER under SIGA_TEMP before buffering files.
- Part JSON files and media files are uploaded to SIGA_TEMP/SIGA_TELEGRAM_BUFFER.
- Final collector output now exposes:
  telegram_buffer_folder_id
  telegram_buffer_folder_name
  telegram_buffer_folder_path
  part_json_file_id
  media_buffer_file_id

Design choice:
- Static subfolder SIGA_TELEGRAM_BUFFER avoids race conditions from creating one folder per album
  when Telegram emits several triggers in parallel for the same media group.
- Grouping still relies on deterministic file prefixes:
  tg_<chatId>_<mediaGroupId>__part_<messageId>.json
  tg_<chatId>_<mediaGroupId>__part_<messageId>__<filename>

Expected test:
- Files should appear inside SIGA_TEMP/SIGA_TELEGRAM_BUFFER
- JSON files should be readable as plain JSON in Drive preview/download
- Photos/documents should also appear in the same buffer folder
- No write should land at Drive root anymore
