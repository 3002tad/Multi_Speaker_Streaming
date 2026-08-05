# Baseline compatibility mapping

Compatibility wrapper là nơi duy nhất được phép biết shape cũ. Core ASR/speaker code không đổi contract trực tiếp trong bước extraction.

## Transcript final

| Baseline field | Event v1 |
|---|---|
| `meeting_id` | envelope `meeting_id`; demo room string phải được adapter thay bằng UUID runtime cung cấp |
| chưa có | envelope `runtime_session_id`, `event_id`, `sequence`, `schema_version=1` |
| `timestamp` | envelope `occurred_at` ISO-8601 UTC |
| `source_id` | `payload.source_identity` |
| `speaker_id` | `payload.speaker.user_id` sau khi map profile label sang external user UUID; không map được thì `null` |
| `speaker` | `payload.speaker.label` |
| `identity_method` | `payload.speaker.identity_method` |
| `speaker_confidence/margin/consensus` | các field tương ứng trong `payload.speaker` |
| `raw_text` | `payload.raw_text` |
| `text` | `payload.content_text` |
| Unix `start_time/end_time` | ISO `payload.started_at/ended_at` |
| `revision` | `payload.revision` |
| `global_turn_id` | `payload.global_turn_id` |
| signal/pipeline timing | `payload.quality` |
| phonetic/refinement/topic metadata | `payload.pipeline_meta` |

## Minutes

- Baseline `document.schema_version=1` được giữ.
- Baseline Unix `meeting.started_at` được đổi thành ISO-8601 UTC.
- Meeting Service gắn UUID `item_id` ổn định khi persist; AI output được phép chưa có `item_id`.
- Mọi summary/detail/proposal/decision/action phải có ít nhất một `source_segment_id` hợp lệ.
- `meeting_id`, revision, trạng thái review/approve và export metadata nằm ngoài `document` và do Meeting Service sở hữu.

## Quy tắc failure

- Không có UUID user chắc chắn: dùng `speaker.user_id=null`, giữ label và `mic_fallback/unknown`.
- Payload không chuyển được sang schema v1: phát `pipeline.warning`, không persist/broadcast một final event sai schema.
- Callback timeout: ghi bounded spool theo `event_id`; retry giữ nguyên event và sequence.
