"""
Product database models.

Covers: products, product_versions, product_images, product_identifiers,
product_ingredients, nutrition_facts, product_allergens, saved_products
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Text,
    ForeignKey, Enum as SAEnum, JSON, Index, UniqueConstraint, text
)
from sqlalchemy.orm import relationship
from app.db.session import Base
import enum


class VerificationStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    USER_SUBMITTED = "user_submitted"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"


class DataQuality(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class DataSourceTier(str, enum.Enum):
    MANUFACTURER = "tier_1_manufacturer"
    VERIFIED_DB = "tier_2_verified_db"
    RETAIL_PROVIDER = "tier_3_retail_provider"
    USER_SUBMITTED = "tier_4_user_submitted"
    COMMUNITY_CORRECTION = "tier_5_community_correction"


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), nullable=False, index=True)
    brand = Column(String(255), nullable=True, index=True)
    category = Column(String(255), nullable=True, index=True)
    subcategory = Column(String(255), nullable=True)
    country = Column(String(100), default="IN")
    description = Column(Text, nullable=True)
    serving_size = Column(String(100), nullable=True)
    serving_unit = Column(String(50), nullable=True)
    image_url = Column(String(1000), nullable=True)

    # Data quality & verification
    verification_status = Column(SAEnum(VerificationStatus), default=VerificationStatus.UNVERIFIED)
    data_quality = Column(SAEnum(DataQuality), default=DataQuality.UNKNOWN)
    data_source_tier = Column(SAEnum(DataSourceTier), nullable=True)
    external_source = Column(String(255), nullable=True)  # e.g., "open_food_facts"
    external_id = Column(String(255), nullable=True)

    # Timestamps
    first_observed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    identifiers = relationship("ProductIdentifier", back_populates="product", cascade="all, delete-orphan")
    versions = relationship("ProductVersion", back_populates="product", cascade="all, delete-orphan")
    images = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan")
    ingredients = relationship("ProductIngredient", back_populates="product", cascade="all, delete-orphan")
    nutrition_facts = relationship("NutritionFact", back_populates="product", uselist=False, cascade="all, delete-orphan")
    allergens = relationship("ProductAllergen", back_populates="product", cascade="all, delete-orphan")
    claims = relationship("ProductClaim", back_populates="product", cascade="all, delete-orphan")
    community_reviews = relationship("CommunityReview", back_populates="product", cascade="all, delete-orphan")
    saved_by = relationship("SavedProduct", back_populates="product", cascade="all, delete-orphan")
    data_provenance = relationship("DataProvenance", back_populates="product", cascade="all, delete-orphan")

    # Declared so autogenerate does not propose dropping what the trigram
    # migration created. See a7c31f9d5b60 for why each one exists.
    __table_args__ = (
        Index("ix_products_brand_name", "brand", "name"),
        Index(
            "ix_products_search_trgm",
            text("(coalesce(brand, '') || ' ' || name) gin_trgm_ops"),
            postgresql_using="gin",
        ),
        Index(
            "ix_products_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        # An OFF product imported from a name search may carry no barcode, so
        # product_identifiers cannot deduplicate it. Partial because both
        # columns are null for user-submitted products.
        Index(
            "uq_products_external_ref",
            "external_source", "external_id",
            unique=True,
            postgresql_where=text("external_source IS NOT NULL AND external_id IS NOT NULL"),
        ),
    )


class ProductIdentifier(Base):
    __tablename__ = "product_identifiers"
    # The barcode lookup keys on the value alone, so a repeated value would make
    # it ambiguous and break every scan of that barcode.
    __table_args__ = (
        UniqueConstraint("identifier_value", name="uq_product_identifier_value"),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    identifier_type = Column(String(50), nullable=False)  # "barcode", "ean", "upc", "sku"
    identifier_value = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="identifiers")


class ProductVersion(Base):
    __tablename__ = "product_versions"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    is_current = Column(Boolean, default=True)
    ingredients_text = Column(Text, nullable=True)
    nutrition_snapshot = Column(JSON, nullable=True)
    allergens_snapshot = Column(JSON, nullable=True)
    changes_from_previous = Column(JSON, nullable=True)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    source = Column(String(255), nullable=True)

    product = relationship("Product", back_populates="versions")


class ProductImage(Base):
    __tablename__ = "product_images"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    image_type = Column(String(50), nullable=False)  # "front", "back", "ingredients", "nutrition"
    image_url = Column(String(1000), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="images")


class ProductIngredient(Base):
    __tablename__ = "product_ingredients"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=True)
    name = Column(String(255), nullable=False)
    position = Column(Integer, nullable=True)  # Order in ingredient list
    percentage = Column(Float, nullable=True)
    is_allergen = Column(Boolean, default=False)

    product = relationship("Product", back_populates="ingredients")
    ingredient = relationship("Ingredient", back_populates="product_ingredients")


class NutritionFact(Base):
    __tablename__ = "nutrition_facts"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), unique=True, nullable=False)
    serving_size = Column(String(100), nullable=True)
    serving_unit = Column(String(50), nullable=True)

    # Per 100g values. Open Food Facts is ingested from its *_100g fields and
    # the scoring engine's thresholds are calibrated for that basis, so this is
    # the unit everything downstream assumes. serving_size above is descriptive
    # only — these numbers are not scaled to it.
    energy_kcal = Column(Float, nullable=True)
    total_fat_g = Column(Float, nullable=True)
    saturated_fat_g = Column(Float, nullable=True)
    trans_fat_g = Column(Float, nullable=True)
    cholesterol_mg = Column(Float, nullable=True)
    sodium_mg = Column(Float, nullable=True)
    total_carbohydrates_g = Column(Float, nullable=True)
    dietary_fiber_g = Column(Float, nullable=True)
    total_sugars_g = Column(Float, nullable=True)
    added_sugars_g = Column(Float, nullable=True)
    protein_g = Column(Float, nullable=True)

    # Vitamins & Minerals (common ones)
    vitamin_a_mcg = Column(Float, nullable=True)
    vitamin_c_mg = Column(Float, nullable=True)
    vitamin_d_mcg = Column(Float, nullable=True)
    calcium_mg = Column(Float, nullable=True)
    iron_mg = Column(Float, nullable=True)
    potassium_mg = Column(Float, nullable=True)

    # Additional nutritional data as JSON for flexibility
    additional_nutrients = Column(JSON, nullable=True)

    # Data source
    source = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    product = relationship("Product", back_populates="nutrition_facts")


class ProductAllergen(Base):
    __tablename__ = "product_allergens"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    allergen = Column(String(100), nullable=False)
    certainty = Column(String(50), default="declared")  # "declared", "may_contain", "traces"
    source = Column(String(255), nullable=True)

    product = relationship("Product", back_populates="allergens")


class ProductClaim(Base):
    __tablename__ = "product_claims"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    claim = Column(String(500), nullable=False)  # e.g., "Sugar Free", "High Protein"
    claim_type = Column(String(100), nullable=True)  # "health", "nutrition", "organic"
    verified = Column(Boolean, default=False)

    product = relationship("Product", back_populates="claims")


class DataProvenance(Base):
    """Tracks where each piece of product data came from."""
    __tablename__ = "data_provenance"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String(100), nullable=False)
    field_value = Column(Text, nullable=True)
    source = Column(String(255), nullable=False)
    source_tier = Column(SAEnum(DataSourceTier), nullable=True)
    source_url = Column(String(1000), nullable=True)
    license_info = Column(String(500), nullable=True)
    acquired_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    verified = Column(Boolean, default=False)

    product = relationship("Product", back_populates="data_provenance")


class SavedProduct(Base):
    __tablename__ = "saved_products"
    # Saving is a toggle, not a log: a user has either saved a product or not,
    # so a (user, product) pair can appear at most once. The constraint is what
    # makes the save endpoint an ON CONFLICT upsert — the same race that put
    # duplicates in Recent Activity applies here, since a double-clicked save
    # button issues two requests that both see no row.
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_saved_product_user_product"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    saved_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="saved_products")
    product = relationship("Product", back_populates="saved_by")
