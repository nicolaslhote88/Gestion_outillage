# SIGA Telegram Batch Processor v1.3

Patch ciblé sur l’aval du batch processor, sans modifier le collector.

## Correctifs
- `Select Oldest Ready Group`
  - conserve maintenant `chat_id`, `chat_type`, `sender`, `message_text` par coalescence depuis les `group_parts`
  - trie les parts par `message_id`
- `Build Media Download Plan`
  - ajoute `media_kind` dérivé depuis `mime_type`
  - garde un `media_order`
- `Build Legacy Payload Rows`
  - lit le binaire via `this.helpers.getBinaryDataBuffer(i, 'data')`
  - construit `image_row` pour les `mime_type` commençant par `image/`
  - construit `document_row` sinon
- `Aggregate Canonical Payload`
  - construit séparément `images[]` et `documents[]`
  - n’utilise plus `part_kind` pour classer les médias
  - propage `chat_id` / `chat_type` dans `context.telegram`

## Effet attendu
Pour un album Telegram de JPG :
- `chat_id` et `chat_type` ne doivent plus être `null`
- les JPG doivent apparaître dans `payload.images[]`
- `payload.documents[]` doit rester vide pour ce cas
