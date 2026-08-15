import requests
import json
import sys

def main():
    # 1. Get user inputs for the placeholders
    print("Enter the values for the URL placeholders:")
    param1 = input("First path parameter (replaces first {ask}): ").strip()
    param2 = input("Second path parameter (replaces second {ask}): ").strip()
    limit = input("Query parameter 'lmit' (replaces {ask} in ?lmit=...): ").strip()

    # 2. Build the URL
    url = f"http://localhost:3001/api/ohlc/{param1}/{param2}?lmit={limit}"
    print(f"\nRequest URL: {url}")

    # 3. Perform the GET request
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise an error for bad status codes
    except requests.exceptions.RequestException as e:
        print(f"Error during request: {e}")
        sys.exit(1)

    # 4. Parse and display the JSON response
    try:
        data = response.json()
    except json.JSONDecodeError:
        print("Response is not valid JSON. Raw response:")
        print(response.text)
        sys.exit(1)

    print("\nJSON Response:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    # 5. Optionally save the JSON to a file
    save = input("\nSave the JSON to a file? (y/n): ").strip().lower()
    if save == 'y':
        filename = input("Enter filename (e.g., output.json): ").strip()
        if not filename:
            filename = "ohlc_response.json"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"JSON saved to {filename}")
        except IOError as e:
            print(f"Error writing file: {e}")

if __name__ == "__main__":
    main()