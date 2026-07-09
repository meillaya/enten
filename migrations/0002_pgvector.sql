-- 0002_pgvector.sql
-- Vector storage for code chunks. The embedding column is fixed at 1024
-- dimensions; callers that use 1536-dim OpenAI models must project down
-- (truncate/pad) before inserting.
CREATE SCHEMA IF NOT EXISTS vec;

CREATE TABLE IF NOT EXISTS vec.chunks (
    id text PRIMARY KEY,
    repo_id text NOT NULL,
    path text NOT NULL,
    lang text NOT NULL,
    start_line int NOT NULL,
    end_line int NOT NULL,
    text text NOT NULL,
    embedding vector(1024),
    symbols jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_hash text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS vec_chunks_repo_lang_idx ON vec.chunks (repo_id, lang);
CREATE INDEX IF NOT EXISTS vec_chunks_path_idx ON vec.chunks (path);
CREATE INDEX IF NOT EXISTS vec_chunks_embedding_idx
    ON vec.chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
