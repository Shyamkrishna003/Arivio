#!/usr/bin/env python3
"""Quick scoring test to verify the fixes."""
import sys
sys.path.insert(0, '.')

from app.personalization.engine import calculate_suitability

# Test 1: Protein bar for muscle gain
result = calculate_suitability(
    product_nutrition={
        'energy_kcal': 250, 'protein_g': 20, 'total_fat_g': 8,
        'saturated_fat_g': 3, 'total_carbohydrates_g': 25,
        'total_sugars_g': 12, 'fiber_g': 3, 'sodium_mg': 200
    },
    product_allergens=[],
    product_ingredients=[{'name': f'ingredient_{i}', 'position': i} for i in range(12)],
    user_goals=[{'goal_type': 'muscle_gain', 'priority': 1}],
    user_allergies=[],
    user_preferences=[],
)
print(f"Test 1 - Protein Bar (Muscle Gain):")
print(f"  Score: {result.overall_score}/100")
print(f"  Goal alignment: {result.breakdown['goal_alignment']}")
print(f"  Nutritional quality: {result.breakdown['nutritional_quality']}")
print(f"  Ingredient profile: {result.breakdown['ingredient_profile']}")
print(f"  Verdict: {result.verdict}")
print()

# Test 2: Oats for weight loss
result2 = calculate_suitability(
    product_nutrition={
        'energy_kcal': 150, 'protein_g': 12, 'total_fat_g': 3,
        'saturated_fat_g': 0.5, 'total_carbohydrates_g': 25,
        'total_sugars_g': 1, 'fiber_g': 8, 'sodium_mg': 5
    },
    product_allergens=[],
    product_ingredients=[{'name': 'whole grain oats', 'position': 1}],
    user_goals=[{'goal_type': 'weight_loss', 'priority': 1}],
    user_allergies=[],
    user_preferences=[],
)
print(f"Test 2 - Oats (Weight Loss):")
print(f"  Score: {result2.overall_score}/100")
print(f"  Goal alignment: {result2.breakdown['goal_alignment']}")
print(f"  Nutritional quality: {result2.breakdown['nutritional_quality']}")
print(f"  Ingredient profile: {result2.breakdown['ingredient_profile']}")
print(f"  Verdict: {result2.verdict}")
print()

# Test 3: Allergen conflict
result3 = calculate_suitability(
    product_nutrition={'energy_kcal': 200, 'protein_g': 15},
    product_allergens=[{'allergen': 'milk', 'certainty': 'confirmed'}],
    product_ingredients=[{'name': 'milk powder', 'position': 1}],
    user_goals=[{'goal_type': 'general_health', 'priority': 1}],
    user_allergies=[{'allergen': 'milk', 'allergy_type': 'allergy', 'severity': 'severe'}],
    user_preferences=[],
)
print(f"Test 3 - Allergen Conflict:")
print(f"  Score: {result3.overall_score}/100")
print(f"  Allergen safe: {result3.allergen_safe}")
print(f"  Verdict: {result3.verdict}")

# Test imports
from app.ai.gateway import generate_report, _parse_ai_response
print("\nAll imports OK ✓")
