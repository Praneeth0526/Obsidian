FROM golang:1.23-alpine AS builder

WORKDIR /build

# Install protoc and Go plugins for gRPC code generation
RUN apk add --no-cache protobuf protobuf-dev curl && \
    go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.34.2 && \
    go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1

# Copy module files first for better layer caching
COPY gateway/go.mod gateway/go.sum* ./


# Download dependencies
RUN go mod download

# Copy source and build the binary
COPY gateway/ ./

# Generate Go stubs from proto (run after COPY so we overwrite any checked-in files)
RUN protoc \
    --go_out=. \
    --go_opt=paths=source_relative \
    --go-grpc_out=. \
    --go-grpc_opt=paths=source_relative \
    proto/search.proto

RUN go build -o gateway-server .

# ── Runtime stage ─────────────────────────────────────────────────────
FROM alpine:3.19

WORKDIR /app
COPY --from=builder /build/gateway-server .

EXPOSE 8080

CMD ["./gateway-server"]
