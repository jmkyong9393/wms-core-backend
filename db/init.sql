CREATE TYPE standard_size AS ENUM ('A5', 'B5');
CREATE TYPE inbound_type AS ENUM ('NEW_STOCK', 'USED_PURCHASE', 'CUSTOMER_RETURN');
CREATE TYPE inbound_status AS ENUM ('RECEIVED', 'CHECKING', 'COMPLETED');
CREATE TYPE condition_grade AS ENUM ('MINT', 'EXCELLENT', 'GOOD', 'NORMAL', 'REJECT');
CREATE TYPE order_type AS ENUM ('B2B_ORDER', 'AUTO_PO');
CREATE TYPE order_status AS ENUM ('PENDING', 'PICKING', 'SHIPPED', 'RETURN_REQUESTED');
CREATE TYPE return_job_status AS ENUM ('PENDING', 'PROCESSING', 'APPROVED', 'REJECTED', 'HITL_REQUIRED', 'FAILED');
CREATE TYPE inspection_mode AS ENUM ('RETURN', 'USED_PURCHASE');
CREATE TYPE inventory_transaction_type AS ENUM ('INBOUND', 'OUTBOUND', 'RETURN_RESTOCK', 'DISCARD');
CREATE TYPE user_role AS ENUM ('MASTER', 'WORKER', 'GUEST', 'PENDING');
CREATE TYPE user_status AS ENUM ('ACTIVE', 'INACTIVE');
CREATE TYPE ticket_status AS ENUM ('TODO', 'IN_PROGRESS', 'RESOLVED');
CREATE TYPE post_category AS ENUM ('NOTICE', 'MANUAL', 'GENERAL');

CREATE TABLE tenants (
    id UUID PRIMARY KEY,
    code VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX ix_tenants_code ON tenants(code);

CREATE TABLE fds_policies (
    policy_key VARCHAR(100) PRIMARY KEY,
    policy_value NUMERIC NOT NULL,
    description VARCHAR(500),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE books (
    id UUID PRIMARY KEY,
    title VARCHAR NOT NULL,
    isbn VARCHAR,
    standard_size standard_size,
    thickness_mm INTEGER,
    base_price INTEGER NOT NULL DEFAULT 0,
    virtual_stock INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inbound_jobs (
    id UUID PRIMARY KEY,
    inbound_type inbound_type NOT NULL,
    status inbound_status NOT NULL,
    supplier_name VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inbound_items (
    id UUID PRIMARY KEY,
    inbound_job_id UUID NOT NULL REFERENCES inbound_jobs(id),
    book_id UUID NOT NULL REFERENCES books(id),
    quantity INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE locations (
    id UUID PRIMARY KEY,
    zone VARCHAR NOT NULL,
    rack VARCHAR NOT NULL,
    shelf VARCHAR NOT NULL,
    barcode VARCHAR,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inventory (
    id UUID PRIMARY KEY,
    book_id UUID NOT NULL REFERENCES books(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    quantity INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_inventory_book_location UNIQUE (book_id, location_id)
);

CREATE TABLE inventory_used_items (
    id UUID PRIMARY KEY,
    book_id UUID NOT NULL REFERENCES books(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    lpn_barcode VARCHAR NOT NULL UNIQUE,
    ubci_score INTEGER,
    condition_grade condition_grade NOT NULL,
    certificate_url VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id UUID PRIMARY KEY,
    customer_name VARCHAR,
    type order_type NOT NULL,
    total_price INTEGER NOT NULL,
    status order_status NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_items (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    book_id UUID NOT NULL REFERENCES books(id),
    quantity INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    final_price INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE return_jobs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    order_id UUID REFERENCES orders(id),
    book_id UUID NOT NULL REFERENCES books(id),
    task_id VARCHAR,
    mode inspection_mode NOT NULL,
    status return_job_status NOT NULL,
    image_paths JSONB,
    ubci_score INTEGER,
    agent_logs JSONB,
    final_report VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_return_jobs_tenant_id ON return_jobs(tenant_id);

CREATE TABLE inventory_logs (
    id UUID PRIMARY KEY,
    transaction_type inventory_transaction_type NOT NULL,
    book_id UUID NOT NULL REFERENCES books(id),
    condition_grade condition_grade NOT NULL,
    quantity_change INTEGER NOT NULL,
    target_lpn VARCHAR,
    picked_location VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE users (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    employee_id VARCHAR NOT NULL,
    email VARCHAR,
    name VARCHAR NOT NULL,
    password_hash VARCHAR NOT NULL,
    role user_role NOT NULL,
    status user_status NOT NULL DEFAULT 'ACTIVE',
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    last_login TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_users_tenant_id ON users(tenant_id);
CREATE UNIQUE INDEX ix_users_employee_id ON users(employee_id);
CREATE UNIQUE INDEX ix_users_email ON users(email);

CREATE TABLE boards (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES return_jobs(id),
    ticket_status ticket_status NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE board_posts (
    id UUID PRIMARY KEY,
    author_id UUID NOT NULL REFERENCES users(id),
    category post_category NOT NULL,
    title VARCHAR NOT NULL,
    content TEXT NOT NULL,
    attachment_paths JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
