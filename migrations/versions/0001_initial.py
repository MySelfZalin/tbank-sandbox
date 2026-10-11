import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_table(
        "instruments",
        sa.Column("uid", sa.Uuid(), nullable=False),
        sa.Column("ticker", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("lot", sa.Integer(), nullable=False),
        sa.Column("instrument_type", sa.String(length=30), nullable=False),
        sa.Column("buy_available", sa.Boolean(), nullable=False),
        sa.Column("sell_available", sa.Boolean(), nullable=False),
        sa.CheckConstraint("lot > 0", name="instrument_positive_lot"),
        sa.PrimaryKeyConstraint("uid"),
    )
    op.create_index(op.f("ix_instruments_ticker"), "instruments", ["ticker"], unique=False)
    op.create_table(
        "fundings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("balance", sa.Numeric(precision=30, scale=9), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_funding_account_time", "fundings", ["account_id", "created_at"], unique=False
    )
    op.create_table(
        "operations",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("instrument_uid", sa.Uuid(), nullable=True),
        sa.Column("operation_type", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=60), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity_units", sa.BigInteger(), nullable=False),
        sa.Column("quantity_done_units", sa.BigInteger(), nullable=False),
        sa.Column("payment", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("commission", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("parent_operation_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["instrument_uid"],
            ["instruments.uid"],
        ),
        sa.PrimaryKeyConstraint("account_id", "external_id"),
    )
    op.create_index("ix_operation_account_date", "operations", ["account_id", "date"], unique=False)
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_uid", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=False),
        sa.Column("quantity_lots", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("broker_request_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=True),
        sa.Column("processing_state", sa.String(length=30), nullable=False),
        sa.Column("broker_status", sa.String(length=60), nullable=True),
        sa.Column("lots_executed", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("direction IN ('buy', 'sell')", name="order_direction"),
        sa.CheckConstraint("quantity_lots > 0", name="order_positive_lots"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["instrument_uid"],
            ["instruments.uid"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_order_account_key"),
        sa.UniqueConstraint("broker_request_id"),
    )
    op.create_index("ix_order_processing", "orders", ["processing_state"], unique=False)
    op.create_index(
        "ix_order_account_created", "orders", ["account_id", "created_at"], unique=False
    )
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_value", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("currency_value", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("shares_value", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("bonds_value", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("funds_value", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("cash", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("blocked_cash", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_snapshot_account_time", "portfolio_snapshots", ["account_id", "synced_at"], unique=False
    )
    op.create_table(
        "portfolio_snapshot_positions",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("instrument_uid", sa.Uuid(), nullable=False),
        sa.Column("quantity_units", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("current_price", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("average_price", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("accrued_interest", sa.Numeric(precision=30, scale=9), nullable=False),
        sa.Column("value", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.ForeignKeyConstraint(
            ["instrument_uid"],
            ["instruments.uid"],
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["portfolio_snapshots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("snapshot_id", "instrument_uid"),
    )


def downgrade():
    op.drop_table("portfolio_snapshot_positions")
    op.drop_table("portfolio_snapshots")
    op.drop_table("orders")
    op.drop_table("operations")
    op.drop_table("fundings")
    op.drop_table("instruments")
    op.drop_table("accounts")
