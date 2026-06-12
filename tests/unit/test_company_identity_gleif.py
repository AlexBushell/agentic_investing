from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from research_platform.sources.gleif import GLEIFRecord
from research_platform.store.models import Base, Identifier
from research_platform.store.services.company_identity import CompanyIdentityService


def test_upsert_from_gleif_persists_lei_and_isins():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, future=True)()

    record = GLEIFRecord(
        lei="213800VCU9TBANZIN455",
        legal_name="THE GYM GROUP PLC",
        country="GB",
        jurisdiction="GB",
        registered_as="08528493",
        status="ACTIVE",
        registration_status="ISSUED",
        isins=["GB00BZBX0P70"],
    )

    result = CompanyIdentityService(session).upsert_from_gleif(record=record)
    session.commit()

    identifiers = session.query(Identifier).all()
    pairs = {(identifier.id_type, identifier.id_value) for identifier in identifiers}

    assert result.created_company is True
    assert ("LEI", "213800VCU9TBANZIN455") in pairs
    assert ("ISIN", "GB00BZBX0P70") in pairs
    assert ("REGISTERED_AS", "08528493") in pairs
