import os
import re
import sys
import time
import json
import uuid
import requests
import traceback
import humanize
import urllib.parse
import webbrowser

from typing import List, Dict, Any
from dotenv import load_dotenv
from datetime import datetime, time as dt_time

# For the OAuth2 flow callback
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

# allow reusing address to prevent bind errors
HTTPServer.allow_reuse_address = True

#==================================================================
# We need to monkey patch AuthResult due to a bug in the Todoist API SDK
# state should be optional, but the SDK doesn't allow it to be None
from dataclasses import dataclass
from typing import Optional
from dataclass_wizard import JSONWizard

@dataclass
class PatchedAuthResult(JSONWizard):
    access_token: str
    token_type: Optional[str] = None
    state: Optional[str] = None  # optional now

# Patch it early on
import todoist_api_python.models
todoist_api_python.models.AuthResult = PatchedAuthResult
#==================================================================

# The Todoist Python SDK
from todoist_api_python.api import TodoistAPI
from todoist_api_python.authentication import get_auth_token, get_authentication_url


# Our main class
class TrmnlTodoistHookup:
    def __init__(self):
        # TRMNL webhook stuff
        self.trmnl_api_key = None
        self.trmnl_plugin_id = None
        self.trmnl_plugin_webhook_url = None

        # Todoist API settings - https://developer.todoist.com/appconsole.html
        self.todoist_api_client_id = None
        self.todoist_api_client_secret = None
        self.todoist_oauth_timeout = 60

        # Resultant creds
        self.todoist_response_auth_code = None
        self.todoist_response_state = None
        self.todoist_oauth_callback_received = False
        self.todoist_api_token = None

        # Try to load token from file, as we may have cached it
        self.access_token_path = os.path.join(os.path.dirname(__file__), "access_token.json")
        self.load_access_token()

        # Kick off this auth flow on class initialisation
        if self.configure():
            if not self.todoist_api_token:
                self.authenticate()

    # If we need to clear our vars
    def reset_auth(self):
        self.todoist_response_auth_code = None
        self.todoist_response_state = None
        self.todoist_api_token = None
        self.todoist_oauth_callback_received = False
        self.shutdown_local_server()

    # Load up the environment
    def configure(self) -> bool:
        try:
            load_dotenv()
            self.todoist_api_client_id = os.getenv('TODOIST_CLIENT_ID')
            self.todoist_api_client_secret = os.getenv('TODOIST_CLIENT_SECRET')
            self.trmnl_api_key = os.getenv('TRMNL_API_KEY')
            self.trmnl_plugin_id = os.getenv('TRMNL_PLUGIN_ID')
            self.trmnl_plugin_webhook_url = (
                f"https://usetrmnl.com/api/custom_plugins/{self.trmnl_plugin_id}"
            )
            # You can test the data held via this API
            # https://usetrmnl.com/api/plugin_settings/<id-here>/data
            # Docs: https://docs.usetrmnl.com/go/private-api/fetch-plugin-content
            return True
        except Exception as error:
            print("[!] Error configuring TrmnlTodoistHookup() - check environment variables")
            print(error)
            return False

    def start_local_server(self):
        self.reset_auth()
        self_ref = self  # for closure

        class RequestHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                if handler_self.path.startswith('/callback'):
                    handler_self.send_response(200)
                    handler_self.end_headers()
                    handler_self.wfile.write(
                        b'Authorization successful! You can close this window.'
                    )

                    # Extract the code and state from the URL
                    parsed = urllib.parse.urlparse(handler_self.path)
                    params = urllib.parse.parse_qs(parsed.query)
                    code = params.get("code", [None])[0]
                    state = params.get("state", [None])[0]
                    print(f"[<] OAuth Callback received: code={code[:5]}..., state={state[:5]}...")

                    if code and state:
                        self_ref.todoist_response_auth_code = code
                        self_ref.todoist_response_state = state
                        self_ref.todoist_oauth_callback_received = True
                        print("[*] Auth code and state stored in self_ref")

                else:
                    handler_self.send_response(404)
                    handler_self.end_headers()

        server_address = ('', 8080)
        self.httpd = HTTPServer(server_address, RequestHandler)
        print("[+] Starting local server on http://localhost:8080")
        try:
            self.httpd.serve_forever()
        except Exception as e:
            print(f"\n[!] Local server stopped: {e}")

    def shutdown_local_server(self):
        if hasattr(self, "httpd") and self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
                print("[-] Local server shut down.")
            except Exception as e:
                print(f"[!] Error shutting down local server: {e}")

    def authenticate(self) -> bool:
        state = uuid.uuid4()
        url = get_authentication_url(
            client_id=self.todoist_api_client_id,
            scopes=["data:read"],
            state=state
        )
        print(f"[>] Visit URL to authorize application: {url}")

        try:
            webbrowser.open(url)
        except Exception:
            pass

        # Start the local web server in a separate thread
        server_thread = Thread(target=self.start_local_server)
        server_thread.daemon = True
        server_thread.start()
        
        # wait for server to initialize
        while not hasattr(self, "httpd"):
            time.sleep(0.2)

        try:
            # Wait for the authorization code
            start_time = time.time()
            while not self.todoist_oauth_callback_received:
                if time.time() - start_time > self.todoist_oauth_timeout:
                    print("[!] Timeout...")
                    self.reset_auth()
                    break
                time.sleep(0.1)

            self.shutdown_local_server()
            server_thread.join(timeout=2)

        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received. Shutting down local server and exiting.")
            self.reset_auth()
            self.shutdown_local_server()
            server_thread.join(timeout=2)
            sys.exit(1)

        # State mismatch
        if str(state) != str(self.todoist_response_state):
            print("[!] State mismatch. Possible CSRF attack. Auth Failed.")
            self.reset_auth()
            self.shutdown_local_server()
            server_thread.join(timeout=2)
            return False

        # Exchange code for access token (note the monkey patch impacts this line)
        auth_result = get_auth_token(
            client_id=self.todoist_api_client_id,
            client_secret=self.todoist_api_client_secret,
            code=self.todoist_response_auth_code
        )

        self.todoist_api_token = auth_result.access_token
        self.save_access_token()

        return True

    def to_timestamp(self, date_str):
        # Try parsing with time
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            # Fallback to date-only format
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        
        return int(dt.timestamp())

    def humanize_timestamp(self, timestamp: int, tiny: bool=False):
        now = datetime.now()
        dt = datetime.fromtimestamp(timestamp)

        # Check if it's exactly midnight today
        start_of_today = datetime.combine(now.date(), dt_time())
        if dt == start_of_today:
            return "today"
        
        ht = humanize.naturaltime(now - dt)
        if (tiny):
            # Start with "a minute" or "a day" ago fixes
            if ht.startswith("a "):
                ht = ht.replace("a ", "1 ") 
            ht = ht.replace(" year", "y")
            ht = ht.replace(" month", "M")
            ht = ht.replace(" week", "w")
            ht = ht.replace(" day", "d")
            ht = ht.replace(" hour", "h")
            ht = ht.replace(" minute", "m")
            ht = ht.replace(" second", "s")
            # Fixup plurals, but protect "plural seconds" first
            ht = ht.replace("ss", "XX")
            ht = ht.replace("s", "")
            ht = ht.replace("XX", "s")
        return ht

    # Get the Todoist data via direct REST calls as the SDK sucks ass
    def get_todoist_data(self):
        
        user_filter = "today | overdue"
        data = {
            "checked": int(datetime.now().timestamp()),
            "tasks": [],
            "filter": user_filter,
            "status": "Checking"
        }

        # Todoist API stuff
        api_root = "https://api.todoist.com/"
        headers = {
            "Authorization": f"Bearer {self.todoist_api_token}"
        }


        # Get the projects
        projects = {}
        u = api_root + "api/v1/projects"
        r = requests.get(u, headers=headers)
        if (r.status_code == 200):
            j = json.loads(r.text)
            if "results" in j:
                for proj in j["results"]:
                    projects[proj["id"]] = proj["name"]
        else:
            print(r.status_code)
            print(r.text)
            data["status"] = "Error fetching projects"
            return data
        
        # Get Today's tasks (and overdue tasks)
        u = api_root + "api/v1/tasks/filter?query=" + user_filter
        r = requests.get(u, headers=headers)
        if (r.status_code == 200):
            j = json.loads(r.text)
            if "results" in j:

                # Tidy up the structure for TRMNL
                tasks = j["results"]
                now = datetime.now()
                for item in tasks:
                    tm = self.to_timestamp(item["due"]["date"])
                    ht = self.humanize_timestamp(tm, tiny=True)
                    item["due_date"] = tm
                    item["due_date_str"] = ht
                    item["priority"] = 5 - item["priority"]
                    item["project_name"] = ("# " + projects[item["project_id"]]) if item["project_id"] in projects.keys() else "#NoProject"
                
                # Sort by due date first, then by priority
                tasks.sort(key=lambda x: x["due_date"], reverse=True)
                tasks.sort(key=lambda x: x["priority"], reverse=False)

                # Store the sorted tasks
                # Note we only get 2KB to play with, so we need to truncate
                for item in tasks:
                    data["tasks"].append({
                        "name": item["content"],
                        "prio": item["priority"],
                        #"due_date": item["due_date"],
                        "due": item["due_date_str"],
                        #"project_id": item["project_id"],
                        "proj": item["project_name"]
                    })
        else:
            print("Status Code: " + r.status_code)
            print(r.text)
            data["status"] = "Error fetching tasks"

        # This simply didn't work    
        # todoist = TodoistAPI(token=self.todoist_api_token)
        # tasks: List[Task] = todoist.filter_tasks(query="View All", limit=100)

        return data


    def truncate_data_to_limit(self, task_list: list, limit_bytes: int = 2048):
        new_list = []

        # Trim our pre-sorted task list to fit.
        for item in task_list:
            # Add it
            new_list.append(item)
            
            # Check the size of the serialized JSON
            size = len(json.dumps(new_list).encode('utf-8'))
            if size > limit_bytes:
                # Remove the last item if it pushes us over the limit
                new_list.pop()
                break
        return new_list


    def update_trmnl_via_webhook(self, data: list):
        # Send the data to TRMNL
        try:
            u = self.trmnl_plugin_webhook_url
            h = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.trmnl_api_key}'
            }
            d = {
                'merge_variables': {
                    'tasks': self.truncate_data_to_limit(data["tasks"], limit_bytes=2000),
                    'filter': data["filter"],
                    'refreshed': data["checked"]
                }
            }
            r = requests.post(u, json=d, headers=h)
            if (r.status_code == 200):
                j = json.loads(r.text)
                #print(json.dumps(j, indent=4))
                if "status" in j:
                    print(f"[>] Tasks sent to TRMNL at {datetime.now().isoformat()}")
                    print(f"[>] Status: {j['status']}")
                    print(f"[>] Message: {j['message']}")
            else:
                print(r.status_code)
                print(r.text)
                data["status"] = "Error saving data to TRMNL"

        except Exception as e:
            print(f"[!] Error sending data to TRMNL: {e}")
            data["status"] = "Exception saving data to TRMNL"
            traceback.print_exc()
            
        pass

    def save_access_token(self):
        try:
            with open(self.access_token_path, "w") as f:
                json.dump({"access_token": self.todoist_api_token}, f)
        except Exception as e:
            print(f"[!] Failed to save access token: {e}")

    def load_access_token(self):
        try:
            if os.path.exists(self.access_token_path):
                with open(self.access_token_path, "r") as f:
                    data = json.load(f)
                    self.todoist_api_token = data.get("access_token")
        except Exception as e:
            print(f"[!] Failed to load access token: {e}")

def main():
    print("Starting TRMNL Todoist script")
    try:
        todoist_trmnl = TrmnlTodoistHookup()

        # Always ensure authentication flow
        if not todoist_trmnl.todoist_api_token:
            print("[>] Reauthenticating to Todoist API...")
            todoist_trmnl.authenticate()
        else:
            print("[>] Already authenticated to Todoist API.")
            print("[>] Access Token: %s" % todoist_trmnl.todoist_api_token[0:5] + "...")
        

        print("[>] Fetching Todoist data...")
        data = todoist_trmnl.get_todoist_data()

        for item in data["tasks"]:
            print("* [P%d] %s // Due: %s // %s" % (
                item["prio"],
                item["name"],
                item["due"],
                item["proj"]
                ))

        # Send the data to TRMNL
        print(json.dumps(data, indent=4))
        
        todoist_trmnl.update_trmnl_via_webhook(data)

    except Exception as error:
        print(error)
        traceback.print_exc()

if __name__ == "__main__":
    main()
