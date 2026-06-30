/**
 * GET /api/download?bucket=<bucket>&key=<object_key>
 *
 * Server-side streaming proxy for MinIO.
 *
 * Flow:
 *   1. Build a presigned GET URL using the INTERNAL MinIO endpoint
 *      (e.g. http://minio:9000 inside k8s/Docker, or http://192.168.49.2:30900 locally)
 *   2. Fetch the file from MinIO on the server — no browser redirect
 *   3. Stream the bytes back to the browser
 *
 * This means the browser only ever calls /api/download on Next.js;
 * it never tries to resolve the internal "minio" hostname.
 *
 * Required env vars:
 *   MINIO_ENDPOINT    e.g. http://minio:9000         (inside Docker/k8s)
 *                          http://192.168.49.2:30900 (local minikube NodePort)
 *   MINIO_ACCESS_KEY  e.g. minioadmin
 *   MINIO_SECRET_KEY  e.g. minioadmin123
 *   MINIO_REGION      e.g. us-east-1  (default)
 */

import { createHmac, createHash } from "crypto";

// ── AWS Sig V4 helpers ────────────────────────────────────────────────────────

function hmacSHA256(key, data) {
  return createHmac("sha256", key).update(data).digest();
}

function sha256hex(data) {
  return createHash("sha256").update(data).digest("hex");
}

/**
 * Build a presigned S3-compatible GET URL (AWS Signature V4).
 * Returns a full URL string pointing to MINIO_ENDPOINT.
 */
function presignS3GetUrl({ endpoint, accessKey, secretKey, region, bucket, key, expiresIn = 300 }) {
  // Encode each path segment of the key separately so slashes are preserved
  const encodedKey = key.split("/").map(encodeURIComponent).join("/");
  const url = new URL(`${endpoint}/${bucket}/${encodedKey}`);
  const host = url.host;
  const path = url.pathname;

  const now = new Date();
  // YYYYMMDD
  const datestamp = now.toISOString().replace(/[:\-]|\.\d{3}/g, "").slice(0, 8);
  // YYYYMMDDTHHmmssZ
  const amzDate = now.toISOString().replace(/[:\-]|\.\d{3}/g, "").slice(0, 15) + "Z";

  const credentialScope = `${datestamp}/${region}/s3/aws4_request`;
  const credential      = `${accessKey}/${credentialScope}`;

  // Build and sort canonical query parameters
  const rawParams = {
    "X-Amz-Algorithm":     "AWS4-HMAC-SHA256",
    "X-Amz-Credential":    credential,
    "X-Amz-Date":          amzDate,
    "X-Amz-Expires":       String(expiresIn),
    "X-Amz-SignedHeaders": "host",
  };

  const sortedQuery = Object.keys(rawParams)
    .sort()
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(rawParams[k])}`)
    .join("&");

  // Canonical request
  const canonicalRequest = [
    "GET",
    path,
    sortedQuery,
    `host:${host}\n`,   // canonical headers (trailing \n required by spec)
    "host",             // signed headers list
    "UNSIGNED-PAYLOAD",
  ].join("\n");

  // String to sign
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    sha256hex(canonicalRequest),
  ].join("\n");

  // Derive signing key
  const kDate    = hmacSHA256(`AWS4${secretKey}`, datestamp);
  const kRegion  = hmacSHA256(kDate, region);
  const kService = hmacSHA256(kRegion, "s3");
  const kSigning = hmacSHA256(kService, "aws4_request");

  const signature = createHmac("sha256", kSigning).update(stringToSign).digest("hex");

  return `${url.origin}${path}?${sortedQuery}&X-Amz-Signature=${signature}`;
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const bucket = (searchParams.get("bucket") || "").trim();
  const key    = (searchParams.get("key")    || "").trim();
  const forceDownload = searchParams.get("dl") === "1";

  if (!bucket || !key) {
    return new Response(
      JSON.stringify({ error: "Missing bucket or key parameter" }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );
  }

  const endpoint  = (process.env.MINIO_ENDPOINT  || "http://localhost:9000").replace(/\/$/, "");
  const accessKey = process.env.MINIO_ACCESS_KEY  || "minioadmin";
  const secretKey = process.env.MINIO_SECRET_KEY  || "minioadmin123";
  const region    = process.env.MINIO_REGION      || "us-east-1";

  try {
    // Step 1: build presigned URL (server-side internal endpoint)
    const presignedUrl = presignS3GetUrl({ endpoint, accessKey, secretKey, region, bucket, key });

    // Step 2: fetch the object FROM MinIO on the server
    //         (browser never sees the internal hostname)
    const minioRes = await fetch(presignedUrl, { cache: "no-store" });

    if (!minioRes.ok) {
      const errText = await minioRes.text().catch(() => "MinIO error");
      return new Response(
        JSON.stringify({ error: `MinIO fetch failed (${minioRes.status})`, detail: errText }),
        { status: 502, headers: { "Content-Type": "application/json" } }
      );
    }

    // Step 3: stream bytes back to the browser
    const contentType = minioRes.headers.get("content-type") || "application/octet-stream";
    const fileName = key.split("/").pop() || key;

    // "inline" lets the browser render the file (used by preview img/iframe)
    // "attachment" forces a Save As dialog (used by the download button)
    const disposition = forceDownload
      ? `attachment; filename="${encodeURIComponent(fileName)}"`
      : `inline; filename="${encodeURIComponent(fileName)}"`;

    const responseHeaders = {
      "Content-Type": contentType,
      "Content-Disposition": disposition,
      // Cache for 5 minutes in the browser (reduces repeated fetches for preview)
      "Cache-Control": "private, max-age=300",
    };

    // Forward content-length when available (enables browser download progress)
    const contentLength = minioRes.headers.get("content-length");
    if (contentLength) responseHeaders["Content-Length"] = contentLength;

    return new Response(minioRes.body, { status: 200, headers: responseHeaders });

  } catch (err) {
    return new Response(
      JSON.stringify({ error: "Download proxy failed", detail: err?.message || String(err) }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}
