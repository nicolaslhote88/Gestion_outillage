SIGA Telegram Batch Processor v1.2

Modifications:
- Added Google Drive Trigger watching SIGA_TELEGRAM_BUFFER folder id 1tbyhFwLBWm5jh605vngOqpHN-Gpb-9yf
- Added Wait node set to 30 seconds after trigger
- Trigger is used only as wake-up signal; workflow continues by rescanning entire buffer
- Existing Manual Trigger kept for manual testing
- Core parsing/grouping/processed-marker logic unchanged from v1.1
