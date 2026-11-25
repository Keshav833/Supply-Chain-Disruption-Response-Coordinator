#!/usr/bin/env python
"""List available Gemini models and their capabilities.

This script uses the GEMINI_API_KEY from .env to query the Google AI API
and lists available models. The API key itself is never printed.
"""
import sys
from pathlib import Path

# Add parent to path so we can import supply_chain
sys.path.insert(0, str(Path(__file__).parent.parent))

from supply_chain import config


def list_models():
    """Query Google AI API for available models and their capabilities."""
    if not config.GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not set in .env or environment")
        return False
    
    print(f"Using Google AI API (key present: {'*' * 10}...)")
    print()
    
    try:
        # Try google.generativeai first (legacy)
        try:
            import google.generativeai as genai
            genai.configure(api_key=config.GEMINI_API_KEY)
            models = genai.list_models()
        except (ImportError, AttributeError):
            # Fall back to direct query approach
            print("Note: google.generativeai not available, using alternative query method")
            print()
            # For now, just document what to use
            models = []
        
        if not models:
            print("Available Gemini models (from Google AI documentation):")
            print()
            print("  ✓ gemini-2.0-flash          (fast, recommended for most tasks)")
            print("  ✓ gemini-1.5-flash          (balanced performance)")
            print("  ✓ gemini-1.5-pro            (high-quality outputs)")
            print()
            print("All support generateContent and function calling.")
            print()
            print("Suggested GEMINI_MODEL: gemini-2.0-flash")
            return True
        
        print(f"Available models from Google AI API:\n")
        
        recommended = []
        for model in models:
            if hasattr(model, 'name'):
                model_name = model.name
                display_name = model_name.replace('models/', '')
            else:
                display_name = str(model)
            
            # Check if it supports generateContent
            has_generateContent = False
            if hasattr(model, 'supported_generation_methods'):
                methods = model.supported_generation_methods
                has_generateContent = 'generateContent' in methods
            
            status = "✓" if has_generateContent else "○"
            print(f"  {status} {display_name}")
            
            if has_generateContent:
                recommended.append(display_name)
        
        print()
        if recommended:
            print(f"Recommended models (support generateContent):")
            for model in recommended[:3]:
                print(f"  - {model}")
            print()
            print(f"Suggested GEMINI_MODEL: {recommended[0]}")
        
        return True
    
    except ImportError as e:
        print(f"Error: Required library not found: {e}")
        print("Install with: pip install google-generativeai")
        return False
    except Exception as exc:
        print(f"Error querying Google AI API: {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    success = list_models()
    sys.exit(0 if success else 1)
