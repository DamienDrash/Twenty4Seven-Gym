import datetime
import time
from nuki_integration.nuki_client import NukiClient
from nuki_integration.config import Settings
from nuki_integration.db import Database
from nuki_integration.services import get_effective_nuki_config

def test():
    settings = Settings()
    db = Database(settings.database_url)
    db.open()
    try:
        nuki_cfg = get_effective_nuki_config(db, settings)
        full_cfg = settings.model_copy(update=nuki_cfg)
        client = NukiClient(full_cfg)

        now = datetime.datetime.now(datetime.UTC)
        start = now.isoformat().replace('+00:00', 'Z')
        end = (now + datetime.timedelta(hours=1)).isoformat().replace('+00:00', 'Z')

        print(f"Testing code creation for Smartlock: {full_cfg.nuki_smartlock_id}")
        
        # Nuki API: PUT /smartlock/auth
        payload = {
            "name": "TEST-GEMINI",
            "type": 13, # 13 is keypad code
            "code": 826491, # random int
            "smartlockIds": [int(full_cfg.nuki_smartlock_id)],
            "allowedFromDate": start,
            "allowedUntilDate": end,
        }
        
        print(f"Payload: {payload}")
        data = client._request("PUT", "/smartlock/auth", json_body=payload)
        print(f"Raw Response: {data}")
        
        print("Waiting 5 seconds for sync...")
        time.sleep(5)
        
        print("Listing all authorizations for the lock...")
        auths = client._request("GET", f"/smartlock/{full_cfg.nuki_smartlock_id}/auth")
        found = False
        if auths:
            for a in auths:
                if a.get("name") == "TEST-GEMINI":
                    print(f"SUCCESS: Found authorization: {a}")
                    # Cleanup
                    client._request("DELETE", f"/smartlock/{full_cfg.nuki_smartlock_id}/auth/{a['id']}")
                    print("Cleanup: Deleted test code.")
                    found = True
                    break
        
        if not found:
            print("FAILED: TEST-GEMINI not found in authorizations list.")
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    test()
