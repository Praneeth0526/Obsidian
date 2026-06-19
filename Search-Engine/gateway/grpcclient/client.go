// Package grpcclient provides a reusable gRPC client for PyWorker-2.
package grpcclient

import (
	"context"
	"time"

	pb "github.com/hpe/search-engine/gateway/proto"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const defaultTimeout = 10 * time.Second

// Client wraps the gRPC connection and the generated SearchWorker stub.
type Client struct {
	conn   *grpc.ClientConn
	worker pb.SearchWorkerClient
}

// New dials PyWorker-2 and returns a ready-to-use Client.
// The caller must call Close() when done.
func New(addr string) (*Client, error) {
	conn, err := grpc.NewClient(
		addr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		return nil, err
	}
	return &Client{
		conn:   conn,
		worker: pb.NewSearchWorkerClient(conn),
	}, nil
}

// Search sends a query to PyWorker-2 and returns the response.
func (c *Client) Search(query string, limit int32) (*pb.SearchQueryResponse, error) {
	ctx, cancel := context.WithTimeout(context.Background(), defaultTimeout)
	defer cancel()

	req := &pb.SearchQueryRequest{
		Query: query,
		Limit: limit,
	}
	return c.worker.ProcessQuery(ctx, req)
}

// Close releases the underlying gRPC connection.
func (c *Client) Close() error {
	return c.conn.Close()
}
