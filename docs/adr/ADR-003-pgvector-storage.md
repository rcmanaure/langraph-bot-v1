# ADR-003: PostgreSQL + pgvector for Vector Storage

**Status:** Accepted  
**Date:** 2025-01

## Context

RAG retrieval requires approximate nearest-neighbor (ANN) search over document embeddings. The system already uses PostgreSQL (via SQLAlchemy + `asyncpg`) for tenant metadata and LangGraph checkpoints.

## Decision

Use the **`pgvector` extension** on the existing PostgreSQL instance for vector storage and similarity search. HNSW index with `ef_search=160` and `iterative_scan=relaxed_order`.

## Alternatives Considered

| Option | Why Rejected |
|---|---|
| Pinecone | External service adds latency, cost, and a second infrastructure dependency |
| Qdrant | Separate Docker service; requires ops runbook for backup, scaling, and failover |
| Weaviate | Same objection as Qdrant; schema management is heavier than pgvector |
| ChromaDB (embedded) | Embedded mode doesn't survive process restarts without a persistent volume; not suitable for multi-tenant |

## Consequences

**Positive:**
- Single database for all data: fewer moving parts, unified backup and restore
- ACID transactions: document indexing and metadata updates are atomic
- `asyncpg` driver already in use; no new driver or connection pool needed
- `iterative_scan=relaxed_order` enables high-recall search even under HNSW approximation

**Negative:**
- pgvector's HNSW index degrades beyond ~10M vectors on commodity hardware; plan a migration to a dedicated vector DB at that scale
- `embedding_dim=1536` (text-embedding-3-small) is fixed at table creation; changing models requires re-indexing all documents
- PostgreSQL is not optimized for write-heavy vector upserts; bulk indexing should use batched inserts with `copy_from` at scale
