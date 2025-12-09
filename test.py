#!/usr/bin/env python3
"""
Test script to make an outbound call via your Twilio bot.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_dialout(to_number: str):
    """
    Test the dialout endpoint.
    
    Args:
        to_number: The phone number to call (format: +1234567890)
    """
    # Use local or ngrok URL
    base_url = os.getenv("LOCAL_SERVER_URL", "http://localhost:7860")
    if not base_url.endswith('/'):
        base_url += '/'
    
    dialout_url = f"{base_url}dialout"
    
    # Get Twilio phone number from env
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    
    if not from_number:
        print("❌ ERROR: TWILIO_PHONE_NUMBER not set in .env file")
        return
    
    print(f"📞 Making outbound call...")
    print(f"   From: {from_number}")
    print(f"   To: {to_number}")
    print(f"   URL: {dialout_url}")
    
    payload = {
        "to_number": to_number,
        "from_number": from_number
    }
    
    try:
        response = requests.post(dialout_url, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n✅ Call initiated successfully!")
            print(f"   Call SID: {data.get('call_sid')}")
            print(f"   Status: {data.get('status')}")
        else:
            print(f"\n❌ Error: {response.status_code}")
            print(f"   Response: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"\n❌ Request failed: {e}")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python test_dialout.py +1234567890")
        print("\nExample: python test_dialout.py +14155551234")
        sys.exit(1)
    
    to_number = sys.argv[1]
    
    # Validate phone number format
    if not to_number.startswith('+'):
        print("❌ Phone number must start with + and country code")
        print("   Example: +14155551234")
        sys.exit(1)
    
    test_dialout(to_number)