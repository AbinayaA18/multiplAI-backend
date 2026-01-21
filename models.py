import uuid
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Integer,
    Text,
    TIMESTAMP,
    func
)
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class AgentPhoneScopeMap(Base):
    __tablename__ = "agent_phone_scope_map"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    tenant_id = Column(UUID(as_uuid=True), nullable=True)

    phone_e164 = Column(Text, nullable=False)

    agent_id = Column(ARRAY(Text), nullable=False, default=[])
    agent_name = Column(ARRAY(Text), default=[])

    # Authorization
    allowed_scopes = Column(ARRAY(Text), nullable=False, default=list)
    scope_meta = Column(JSONB, default=dict)

    # Auth references (non-secret)
    client_id = Column(Text)
    api_key_id = Column(Text)
    x509_cert_fingerprint = Column(Text)
    public_key_fingerprint = Column(Text)

    # Endpoint details
    endpoint_url = Column(Text)
    endpoint_protocol = Column(Text)
    endpoint_port = Column(Integer)
    endpoint_path = Column(Text)
    endpoint_tls_fingerprint = Column(Text)
    endpoint_meta = Column(JSONB, default=dict)

    reverse_learning_allowed = Column(Boolean, nullable=False, default=False)

    agent_status = Column(Text, nullable=False, default="active")

    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(TIMESTAMP(timezone=True))

    meta = Column("metadata", JSONB, default=dict)


    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now()
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

