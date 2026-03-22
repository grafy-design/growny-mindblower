import requests
import json
import datetime

MATON_API_KEY = "6_uOrU5mwMx-uvZz59I8edXVXKuc25TSQpx1Z3w67U8UFiCyFJqARsPy0vCI268hC4fpmrJ220gfWf_JkVHhz0hDHEMGdeSiJH4"

def test_api():
    url = f"https://gateway.maton.ai/google-calendar/calendar/v3/users/me/calendarList"
    headers = {"Authorization": f"Bearer {MATON_API_KEY}"}
    
    response = requests.get(url, headers=headers)
    data = response.json()
    
    print(f"Total calendars: {len(data.get('items', []))}")
    for cal in data.get('items', []):
        if cal.get('selected', False):
            print(f"- Selected: {cal.get('summary')} | Color: {cal.get('backgroundColor')}")

test_api()
