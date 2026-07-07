
## Role-Based Buckets (added for RBAC feature)

In addition to the original `uploads` bucket, three role-scoped buckets exist:

| Bucket | Intended uploader | Intended visibility |
|---|---|---|
| `admin-uploads` | Admin | Admin only (sees everything across all 3) |
| `manager-uploads` | Manager | Manager + User |
| `user-uploads` | User | User only |

All three:
- Have versioning enabled (no silent overwrite on same-filename re-upload)
- Fire `s3:ObjectCreated` events to the same `file-upload-events` Kafka topic as the original `uploads` bucket
- Are created automatically by the `minio-init` container on stack startup

The bucket name is available in every Kafka event payload at
`Records[0].s3.bucket.name` — downstream consumers (PyWorker-1, OpenSearch
indexing, Go Gateway query filtering) should use this field to apply
role-based access logic.

**Open decision for Role 2 / Role 3:** how to handle multiple versions of
the same filename being indexed (see team discussion — options A/B/C for
version-aware indexing).
