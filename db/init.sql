CREATE TYPE standard_size AS ENUM ('A5', 'B5');
CREATE TYPE inbound_type AS ENUM ('NEW_STOCK', 'USED_PURCHASE', 'CUSTOMER_RETURN');
CREATE TYPE inbound_status AS ENUM ('RECEIVED', 'CHECKING', 'COMPLETED');
CREATE TYPE condition_grade AS ENUM ('MINT', 'EXCELLENT', 'NORMAL', 'REJECT');
CREATE TYPE book_category AS ENUM (
    'COMIC',
    'STUDY_GUIDE',
    'NOVEL',
    'HUMANITIES',
    'SOCIAL_SCIENCE',
    'BUSINESS_ECONOMICS',
    'SCIENCE_TECHNOLOGY',
    'CHILDREN',
    'LANGUAGE',
    'ART_LIFESTYLE'
);
CREATE TYPE order_type AS ENUM ('B2B_ORDER', 'AUTO_PO');
CREATE TYPE order_status AS ENUM ('PENDING', 'PICKING', 'SHIPPED', 'RETURN_REQUESTED');
CREATE TYPE orderproposalstatus AS ENUM (
    'PENDING',
    'APPROVED',
    'REJECTED',
    'NOT_REQUIRED'
);
CREATE TYPE return_job_status AS ENUM ('PENDING', 'PROCESSING', 'APPROVED', 'REJECTED', 'HITL_REQUIRED', 'RECHECK_REQUIRED', 'FAILED');
CREATE TYPE inspection_mode AS ENUM ('RETURN', 'USED_PURCHASE');
CREATE TYPE inventory_transaction_type AS ENUM ('INBOUND', 'OUTBOUND', 'RETURN_RESTOCK', 'DISCARD');
CREATE TYPE used_inventory_status AS ENUM ('AVAILABLE', 'RESERVED', 'SHIPPED');
CREATE TYPE rejected_item_status AS ENUM ('REJECT_HOLD', 'DISCARDED');
CREATE TYPE user_role AS ENUM ('MASTER', 'ADMIN', 'WORKER', 'GUEST', 'PENDING');
CREATE TYPE user_status AS ENUM ('ACTIVE', 'INACTIVE');
CREATE TYPE ticket_status AS ENUM ('TODO', 'IN_PROGRESS', 'RESOLVED');
CREATE TYPE post_category AS ENUM ('NOTICE', 'MANUAL', 'GENERAL');
CREATE TYPE notification_category AS ENUM ('FDS_ALERT', 'AGENT_ALERT', 'RESTOCK_ALERT');
CREATE TYPE notification_severity AS ENUM ('HIGH', 'MEDIUM', 'LOW');

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
    isbn VARCHAR UNIQUE,
    publisher VARCHAR,
    category book_category NOT NULL,
    standard_size standard_size,
    thickness_mm INTEGER,
    base_price DECIMAL(12, 2) NOT NULL DEFAULT 0,
    virtual_stock INTEGER NOT NULL DEFAULT 0,
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
    location_id UUID,
    quantity INTEGER NOT NULL,
    lpn_barcode VARCHAR UNIQUE,
    certificate_token VARCHAR UNIQUE,
    condition_grade condition_grade,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE locations (
    id UUID PRIMARY KEY,
    zone VARCHAR NOT NULL,
    rack VARCHAR NOT NULL,
    shelf VARCHAR NOT NULL,
    barcode VARCHAR NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_locations_zone CHECK (zone IN ('A', 'B', 'C')),
    CONSTRAINT ck_locations_rack_positive_integer
        CHECK (rack ~ '^[1-9][0-9]*$'),
    CONSTRAINT ck_locations_shelf_range
        CHECK (shelf ~ '^([1-9]|10)$'),
    CONSTRAINT ck_locations_barcode_components
        CHECK (barcode = zone || '-' || rack || '-' || shelf)
);

ALTER TABLE inbound_items
    ADD CONSTRAINT inbound_items_location_id_fkey
    FOREIGN KEY (location_id) REFERENCES locations(id);

CREATE TABLE inventory (
    id UUID PRIMARY KEY,
    book_id UUID NOT NULL REFERENCES books(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    reserved_quantity INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_inventory_book_location UNIQUE (book_id, location_id),
    CONSTRAINT ck_inventory_reserved_quantity_non_negative
        CHECK (reserved_quantity >= 0),
    CONSTRAINT ck_inventory_reserved_quantity_not_exceed_quantity
        CHECK (reserved_quantity <= quantity)
);

CREATE TABLE inventory_used_items (
    id UUID PRIMARY KEY,
    book_id UUID NOT NULL REFERENCES books(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    return_job_id UUID UNIQUE,
    lpn_barcode VARCHAR NOT NULL UNIQUE,
    ubci_score NUMERIC(5, 2),
    condition_grade condition_grade NOT NULL,
    status used_inventory_status NOT NULL DEFAULT 'AVAILABLE',
    certificate_url VARCHAR,
    stocked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_inventory_used_items_sellable_grade
        CHECK (condition_grade IN ('MINT', 'EXCELLENT', 'NORMAL'))
);

CREATE TABLE orders (
    id UUID PRIMARY KEY,
    customer_id UUID,
    customer_name VARCHAR,
    type order_type NOT NULL,
    total_price DECIMAL(12, 2) NOT NULL,
    status order_status NOT NULL,
    logistics_center VARCHAR,
    waybill_number VARCHAR(100),
    shipping_carrier VARCHAR(50),
    shipped_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX ix_orders_waybill_number
    ON orders(waybill_number);

CREATE TABLE order_items (
    id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES orders(id),
    book_id UUID NOT NULL REFERENCES books(id),
    location_id UUID REFERENCES locations(id),
    condition_grade condition_grade,
    quantity INTEGER NOT NULL,
    unit_price DECIMAL(12, 2) NOT NULL,
    final_price DECIMAL(12, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE order_item_inventory_allocations (
    id UUID PRIMARY KEY,
    order_item_id UUID NOT NULL REFERENCES order_items(id),
    inventory_id UUID NOT NULL REFERENCES inventory(id),
    quantity INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_order_item_inventory_allocation
        UNIQUE (order_item_id, inventory_id),
    CONSTRAINT ck_order_item_inventory_allocation_quantity_positive
        CHECK (quantity > 0)
);

CREATE INDEX ix_order_item_inventory_allocations_order_item_id
    ON order_item_inventory_allocations(order_item_id);

CREATE INDEX ix_order_item_inventory_allocations_inventory_id
    ON order_item_inventory_allocations(inventory_id);

CREATE TABLE order_item_lpn_allocations (
    id UUID PRIMARY KEY,
    order_item_id UUID NOT NULL REFERENCES order_items(id),
    inventory_used_item_id UUID NOT NULL REFERENCES inventory_used_items(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_order_item_lpn_allocation
        UNIQUE (order_item_id, inventory_used_item_id)
);

CREATE INDEX ix_order_item_lpn_allocations_order_item_id
    ON order_item_lpn_allocations(order_item_id);

CREATE TABLE return_jobs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    order_id UUID REFERENCES orders(id),
    book_id UUID NOT NULL REFERENCES books(id),
    inbound_item_id UUID REFERENCES inbound_items(id),
    task_id VARCHAR,
    mode inspection_mode NOT NULL,
    status return_job_status NOT NULL,
    image_paths JSONB,
    ubci_score NUMERIC(5, 2),
    condition_grade condition_grade,
    agent_logs JSONB,
    final_report VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_return_jobs_tenant_id ON return_jobs(tenant_id);

ALTER TABLE inventory_used_items
    ADD CONSTRAINT fk_inventory_used_items_return_job
    FOREIGN KEY (return_job_id) REFERENCES return_jobs(id);

CREATE TABLE rejected_items (
    id UUID PRIMARY KEY,
    inbound_item_id UUID NOT NULL UNIQUE REFERENCES inbound_items(id),
    return_job_id UUID UNIQUE REFERENCES return_jobs(id),
    book_id UUID NOT NULL REFERENCES books(id),
    location_id UUID NOT NULL REFERENCES locations(id),
    lpn_barcode VARCHAR NOT NULL UNIQUE,
    ubci_score NUMERIC(5, 2),
    rejection_reason JSONB,
    status rejected_item_status NOT NULL DEFAULT 'REJECT_HOLD',
    rejected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    discarded_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_rejected_items_status_location
    ON rejected_items(status, location_id);

CREATE TABLE inventory_logs (
    id UUID PRIMARY KEY,
    transaction_type inventory_transaction_type NOT NULL,
    book_id UUID NOT NULL REFERENCES books(id),
    condition_grade condition_grade,
    quantity_change INTEGER NOT NULL,
    target_lpn VARCHAR,
    picked_location VARCHAR,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE fds_reports (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    customer_id UUID NOT NULL,
    fraud_score INTEGER NOT NULL,
    fraud_reason VARCHAR,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_fds_reports_tenant_id ON fds_reports(tenant_id);

CREATE TABLE weekly_insights (
    id UUID PRIMARY KEY,
    report_week VARCHAR NOT NULL UNIQUE,
    saved_labor_cost_krw INTEGER NOT NULL DEFAULT 0,
    top_defective_publishers JSONB,
    location_hotspots JSONB,
    logistics_hotspots JSONB,
    predicted_returns INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE order_proposals (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    book_id UUID NOT NULL REFERENCES books(id),
    return_job_id UUID NOT NULL REFERENCES return_jobs(id),
    recent_sales_quantity INTEGER NOT NULL,
    current_stock INTEGER NOT NULL,
    pending_auto_po_quantity INTEGER NOT NULL DEFAULT 0,
    rejected_quantity INTEGER NOT NULL,
    rejection_reason_code VARCHAR NOT NULL,
    recommended_order_quantity INTEGER NOT NULL,
    reason_summary VARCHAR NOT NULL,
    evidence JSONB NOT NULL,
    risk_level VARCHAR NOT NULL,
    status orderproposalstatus NOT NULL,
    auto_po_order_id UUID REFERENCES orders(id),
    reviewer_id UUID REFERENCES users(id),
    reviewed_at TIMESTAMP,
    review_comment VARCHAR(1000),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_order_proposals_return_job UNIQUE (return_job_id),
    CONSTRAINT ck_order_proposals_pending_auto_po_nonnegative
        CHECK (pending_auto_po_quantity >= 0)
);

CREATE INDEX ix_order_proposals_tenant_id
    ON order_proposals(tenant_id);

CREATE INDEX ix_order_proposals_book_id
    ON order_proposals(book_id);

CREATE INDEX ix_order_proposals_return_job_id
    ON order_proposals(return_job_id);

CREATE INDEX ix_order_proposals_status
    ON order_proposals(status);

CREATE INDEX ix_order_proposals_auto_po_order_id
    ON order_proposals(auto_po_order_id);

CREATE INDEX ix_order_proposals_reviewer_id
    ON order_proposals(reviewer_id);

CREATE TABLE notifications (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    category notification_category NOT NULL,
    severity notification_severity NOT NULL,
    title VARCHAR(200) NOT NULL,
    message VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX ix_notifications_tenant_id ON notifications(tenant_id);

CREATE TABLE notification_recipients (
    id UUID PRIMARY KEY,
    notification_id UUID NOT NULL REFERENCES notifications(id),
    user_id UUID NOT NULL REFERENCES users(id),
    read_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_notification_recipient UNIQUE (notification_id, user_id)
);

CREATE INDEX ix_notification_recipients_notification_id
    ON notification_recipients(notification_id);
CREATE INDEX ix_notification_recipients_user_id
    ON notification_recipients(user_id);
CREATE INDEX ix_notification_recipients_read_at
    ON notification_recipients(read_at);

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
    content VARCHAR NOT NULL,
    attachment_paths JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
