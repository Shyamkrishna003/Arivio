import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.products.models import ProductAllergen
from app.ai.gateway import settings, _parse_ai_response
import traceback

async def analyze_product_allergens_ai(product_id: int, ingredients: list[str]):
    """Background task to analyze raw ingredients for hidden allergens using AI."""
    if not ingredients or not settings.AI_API_KEY:
        return
        
    prompt = f"""
    You are an expert food scientist and toxicologist.
    Analyze the following list of product ingredients for any HIDDEN ALLERGENS.
    Look for chemical names, E-numbers, scientific derivatives, or uncommon names that actually belong to these major allergen groups:
    ['milk', 'peanuts', 'tree_nuts', 'gluten', 'soy', 'eggs', 'fish', 'shellfish', 'sesame', 'mustard', 'celery', 'sulphites']
    
    Ingredients to analyze: {', '.join(ingredients)}
    
    Return a JSON object with a list of found allergens in this exact format:
    {{
      "found_allergens": [
        {{"allergen": "milk", "certainty": "confirmed", "reason": "Found casein which is a milk protein"}},
        {{"allergen": "gluten", "certainty": "possible", "reason": "Found maltodextrin which may contain gluten"}}
      ]
    }}
    IMPORTANT: For "certainty", use ONLY these values:
    - "confirmed" — ingredient is definitely from this allergen group
    - "possible" — ingredient may be derived from this allergen group
    If none are found, return {{"found_allergens": []}}.
    """
    
    try:
        from openai import AsyncOpenAI
        
        if settings.AI_PROVIDER.lower() in ("gemini", "google"):
            client = AsyncOpenAI(
                api_key=settings.AI_API_KEY,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
            )
            model = settings.AI_MODEL if settings.AI_MODEL.startswith("gemini") else "gemini-2.0-flash"
        elif settings.AI_PROVIDER.lower() == "groq":
            client = AsyncOpenAI(
                api_key=settings.AI_API_KEY,
                base_url="https://api.groq.com/openai/v1"
            )
            model = settings.AI_MODEL if "gpt" in settings.AI_MODEL or "compound" in settings.AI_MODEL or "qwen" in settings.AI_MODEL else "openai/gpt-oss-20b"
        else:
            client = AsyncOpenAI(api_key=settings.AI_API_KEY)
            model = settings.AI_MODEL
            
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You output JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        
        raw = response.choices[0].message.content or "{}"
        data = _parse_ai_response(raw)
        found_allergens = data.get("found_allergens", [])
        
        if found_allergens:
            async with AsyncSessionLocal() as db:
                # Load existing allergens to avoid duplicates
                existing_result = await db.execute(
                    select(ProductAllergen.allergen).where(ProductAllergen.product_id == product_id)
                )
                existing_allergens = {row.lower() for row in existing_result.scalars().all()}
                
                added_count = 0
                for item in found_allergens:
                    allergen_name = item.get("allergen")
                    if not allergen_name:
                        continue
                    
                    allergen_lower = allergen_name.lower()
                    
                    # Skip if this allergen already exists for this product
                    if allergen_lower in existing_allergens:
                        continue
                    
                    # Validate and normalize certainty
                    raw_certainty = item.get("certainty", "possible")
                    certainty = "confirmed" if raw_certainty in ("confirmed", "high") else "possible"
                    
                    # Add to product_allergens table
                    new_allergen = ProductAllergen(
                        product_id=product_id,
                        allergen=allergen_lower,
                        certainty=certainty,
                        source="ai_analysis"
                    )
                    db.add(new_allergen)
                    existing_allergens.add(allergen_lower)
                    added_count += 1
                    
                await db.commit()
                print(f"✅ AI successfully analyzed and added {added_count} hidden allergens for product {product_id}.")
                
    except Exception as e:
        print(f"⚠️ AI background task for allergen extraction failed for product {product_id}: {e}")
        traceback.print_exc()
