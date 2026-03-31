from nuki_integration.nuki_client import NukiClient
from nuki_integration.config import Settings
from nuki_integration.db import Database
from nuki_integration.services import get_effective_nuki_config

def cleanup():
    settings = Settings()
    db = Database(settings.database_url)
    db.open()
    try:
        nuki_cfg = get_effective_nuki_config(db, settings)
        full_cfg = settings.model_copy(update=nuki_cfg)
        client = NukiClient(full_cfg)

        print(f"Listing all authorizations for lock {full_cfg.nuki_smartlock_id} to clean up TEST-GEMINI codes...")
        auths = client._request("GET", f"/smartlock/{full_cfg.nuki_smartlock_id}/auth")
        
        if not auths:
            print("No authorizations found.")
            return

        deleted_count = 0
        for a in auths:
            if a.get("name") == "TEST-GEMINI":
                auth_id_to_delete = a.get("id")
                print(f"Deleting authorization: {a.get('name')} (ID: {auth_id_to_delete})...")
                client._request("DELETE", f"/smartlock/{full_cfg.nuki_smartlock_id}/auth/{auth_id_to_delete}")
                deleted_count += 1
        
        print(f"Cleanup finished. Deleted {deleted_count} 'TEST-GEMINI' codes.")
    except Exception as e:
        print(f"ERROR during cleanup: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    cleanup()
