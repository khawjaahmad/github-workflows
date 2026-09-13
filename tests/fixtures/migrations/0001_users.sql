-- A migration squawk is happy with: no locks taken on existing data.
CREATE TABLE IF NOT EXISTS users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
