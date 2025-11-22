import requests
import json
from datetime import datetime

topic = "bnb"
url = f"https://ntfy.sh/{topic}"

def test_simple_message():
    print(f"Sending text message to {url}...")
    try:
        resp = requests.post(url, data="🔔 This is a test message from your Python script!".encode('utf-8'))
        if resp.status_code == 200:
            print("✅ Text message sent successfully!")
        else:
            print(f"❌ Failed to send text message. Status: {resp.status_code}, Response: {resp.text}")
    except Exception as e:
        print(f"❌ Error sending text message: {e}")

def test_file_attachment():
    print(f"\nSending file attachment to {url}...")
    
    # Create a dummy JSON data
    data = {
        "test_id": 123,
        "timestamp": datetime.now().isoformat(),
        "content": "This is a test JSON file content."
    }
    json_str = json.dumps(data, indent=2)
    filename = f"test_data_{datetime.now().strftime('%H%M%S')}.json"
    
    headers = {
        "Filename": filename,
    }
    
    try:
        # ntfy supports PUT for file uploads
        resp = requests.put(url, data=json_str.encode('utf-8'), headers=headers)
        if resp.status_code == 200:
            print(f"✅ File '{filename}' sent successfully!")
        else:
            print(f"❌ Failed to send file. Status: {resp.status_code}, Response: {resp.text}")
    except Exception as e:
        print(f"❌ Error sending file: {e}")

if __name__ == "__main__":
    test_simple_message()
    test_file_attachment()
