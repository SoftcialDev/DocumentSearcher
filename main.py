from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv
from docx import Document
from datetime import datetime, timedelta, timezone
import fitz
import requests
import os
import json
import psycopg
import msal
import threading
import time

app = Flask(__name__)
load_dotenv()

# Configuration
MANIFEST_FILE = "manifest.json"
EXTENSIONS = os.getenv("EXTENSIONS").split(",")
ONEDRIVE_EMAILS = os.getenv("ONEDRIVE_EMAILS")
WEBHOOK = os.getenv("WEBHOOK_URL")

# Sharepoint structure
SHAREPOINT_SITE_ID = os.getenv("SHAREPOINT_SITE_ID")
SHAREPOINT_SUBSCRIPTION_ID = os.getenv("SHAREPOINT_SUBSCRIPTION_ID")
SHAREPOINT_LIST_ID = os.getenv("SHAREPOINT_LIST_ID")

# Sharepoint authentication
SHAREPOINT_ENTRA_SECRET_VALUE = os.getenv("SHAREPOINT_ENTRA_SECRET_VALUE")
SHAREPOINT_ENTRA_SECRET_ID = os.getenv("SHAREPOINT_ENTRA_SECRET_ID")
SHAREPOINT_ENTRA_TENANT_ID = os.getenv("SHAREPOINT_ENTRA_TENANT_ID")
SHAREPOINT_ENTRA_CLIENT_ID = os.getenv("SHAREPOINT_ENTRA_CLIENT_ID")

# Onedrive authentication
ONEDRIVE_ENTRA_SECRET_VALUE = os.getenv("ONEDRIVE_ENTRA_SECRET_VALUE")
ONEDRIVE_ENTRA_SECRET_ID = os.getenv("ONEDRIVE_ENTRA_SECRET_ID")
ONEDRIVE_ENTRA_TENANT_ID = os.getenv("ONEDRIVE_ENTRA_TENANT_ID")
ONEDRIVE_ENTRA_CLIENT_ID = os.getenv("ONEDRIVE_ENTRA_CLIENT_ID")

# PostgreSQL connection
PGHOST = os.getenv("PGHOST")
PGUSER = os.getenv("PGUSER")
PGPORT = os.getenv("PGPORT")
PGDATABASE = os.getenv("PGDATABASE")
PGPASSWORD = os.getenv("PGPASSWORD")

# Graph Authentication
GRAPH_TOKEN_ENDPOINT = os.getenv("GRAPH_TOKEN_ENDPOINT")

###############################
# Sharepoint related endpoint #
###############################
def get_sharepoint_token():

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    body = {
        "client_id": SHAREPOINT_ENTRA_CLIENT_ID,
        "scope": "https://graph.microsoft.com/.default",
        "client_secret": SHAREPOINT_ENTRA_SECRET_VALUE,
        "grant_type": "client_credentials"
    }

    try:
        response = requests.post(GRAPH_TOKEN_ENDPOINT, headers=headers, data=body)
        response.raise_for_status()
        token = response.json()["access_token"]
        return token
    except requests.exceptions.RequestException as e:
        print("Token request failed:", e)
        return None
    
def subscribe_to_sharepoint():
    access_token = get_sharepoint_token()

    expiration_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    url = "https://graph.microsoft.com/v1.0/subscriptions"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "changeType": "updated",
        "notificationUrl": WEBHOOK,
        "resource": f"/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_LIST_ID}",
        "expirationDateTime": expiration_time,
        "clientState": "SecretClientState"
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 201:
        data = response.json()
        print("Subscription created successfully.")
        print("ID:", data.get("id"))
        print("Expires:", data.get("expirationDateTime"))
    else:
        print("Failed to create subscription.")
        print("Status:", response.status_code)
        print("Response:", response.text)

def subscription_renewal_loop():
    
    interval_hours = 24 # Sleep this amount of hours
    while True:
        token = get_sharepoint_token()
        url = f"https://graph.microsoft.com/v1.0/subscriptions/{SHAREPOINT_SUBSCRIPTION_ID}"

        new_expiration = (datetime.now(timezone.utc) + timedelta(minutes=4200)).isoformat().replace("+00:00", "Z")

        payload = {
            "expirationDateTime": new_expiration
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        response = requests.patch(url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"Subscription {SHAREPOINT_SUBSCRIPTION_ID} renewed successfully.")
        else:
            print(f"Failed to renew subscription {SHAREPOINT_SUBSCRIPTION_ID}: {response.text}")
        time.sleep(interval_hours * 3600)

def delete_sharepoint_subscription():
    """
        DO NOT USE
    """
    token = get_sharepoint_token()
    url = f"https://graph.microsoft.com/v1.0/subscriptions/{SHAREPOINT_SUBSCRIPTION_ID}"
    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = requests.delete(url, headers=headers)

    if response.status_code == 204:
        print(f"Subscription {SHAREPOINT_SUBSCRIPTION_ID} deleted successfully.")
    else:
        print(f"Failed to delete subscription {SHAREPOINT_SUBSCRIPTION_ID}")
        print("Status:", response.status_code)
        print("Response:", response.text)


def get_sharepoint_content(token):
    url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{SHAREPOINT_LIST_ID}/items?expand=fields,driveItem"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    try:
        items = []
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        for item in data.get("value", []):
            fields = item.get("fields", {})
            drive_item = item.get("driveItem", {})

            # Ignore items without valid extensions
            if not any(drive_item.get("name", "").endswith(ext) for ext in EXTENSIONS):
                continue

            # Merge both fields and driveItem info
            combined = {
                **fields,
                "driveId": drive_item.get("parentReference", {}).get("driveId", ""),
                "driveItemId": drive_item.get("id", ""),
                "webUrl": drive_item.get("webUrl", ""),
                "size": drive_item.get("size", ""),
                "lastModifiedDateTime": drive_item.get("lastModifiedDateTime", ""),
                "fileName": drive_item.get("name", "")
            }

            items.append(combined)

        return items
    except requests.exceptions.RequestException as e:
        print("Request failed:", e)
        if e.response is not None:
            print("Status:", e.response.status_code)
            print("Response:", e.response.text)
            return None
        
def download_sharepoint_file(token, manifest, items):
    files = []

    manifest_keys = {
        f"{entry.get('driveId')}::{entry.get('driveItemId')}" for entry in manifest
    }

    for item in items:
        drive_id = item.get("driveId")
        item_id = item.get("driveItemId")
        download_dir = ""
        file_name = item.get("fileName")

        manifest_key = f"{drive_id}::{item_id}"

        # Only download .docx and .pdf
        if not (file_name.lower().endswith(".docx") or file_name.lower().endswith(".pdf")):
            continue

        # Skip items already on manifest
        if manifest_key in manifest_keys:
            continue
        
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
        headers = {
            "Authorization": f"Bearer {token}"
        }

        response = requests.get(url, headers=headers, stream=True)

        if response.status_code == 200:
            file_path = os.path.join(download_dir, file_name)
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"Downloaded: {file_name} -> {file_path}")
            files.append({
                "path" : file_path,
                "itemId" : item_id
            })
        else:
            print(f"Failed to download {file_name}")
    return files
        
def update_sharepoint_manifest(manifest):
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)

def load_sharepoint_manifest():
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, "r") as f:
            return json.load(f)
    return {}

#############################
# Onedrive related endpoint #
#############################
def onedrive_read_loop():
    
    interval_hours = 24 # Sleep this amount of hours
    while True:
        print("Starting Onedrive read process")
        start_onedrive_sequence()
        print("Onedrive read process finished")
        time.sleep(interval_hours * 3600)

def get_onedrive_token():

    AUTHORITY = f"https://login.microsoftonline.com/{ONEDRIVE_ENTRA_TENANT_ID}"
    SCOPE = ["https://graph.microsoft.com/.default"]  # Application permission scope

    app = msal.ConfidentialClientApplication(
        ONEDRIVE_ENTRA_CLIENT_ID,
        authority=AUTHORITY,
        client_credential=ONEDRIVE_ENTRA_SECRET_VALUE
    )

    result = app.acquire_token_for_client(scopes=SCOPE)

    if "access_token" in result:
        token = result["access_token"]
        return token
    else:
        print("Failed to get token:", result.get("error_description"))
        return None
    
def get_onedrive_users(token):
    allowed_users = ONEDRIVE_EMAILS.split(",")
    allowed_ids = []

    access_token = get_onedrive_token()
    url = "https://graph.microsoft.com/v1.0/users"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    users = response.json().get("value", [])
    for user in users:
        if user["userPrincipalName"] in allowed_users:
            allowed_ids.append(user["id"])
    
    return allowed_ids


def traverse_onedrive(token, user_id, manifest, item_id="root", path=""):
    headers = {"Authorization": f"Bearer {token}"}

    url = f"https://graph.microsoft.com/v1.0/users/{user_id}/drive/items/{item_id}/children"
    resp = requests.get(url, headers=headers)

    if not resp.ok:
        print(f"Failed to list {item_id} for {user_id}: {resp.status_code}")
        return

    items = resp.json().get("value", [])
    for item in items:
        name = item["name"]
        current_path = f"{path}/{name}".strip("/")

        if "folder" in item:
            # It's a folder, recurse into it
            print(f"[Folder] {current_path}")
            traverse_onedrive(token, user_id, manifest, item["id"], current_path)
        else:
            # Ignore items without valid extensions
            if not any(name.endswith(ext) for ext in EXTENSIONS):
                continue
            manifest.append({
                "user_id": user_id,
                "item_id": item["id"],
                "name": name,
                "path": current_path,
                "size": item.get("size", 0),
                "lastModifiedDateTime": item.get("lastModifiedDateTime")
            })
    return manifest

def download_onedrive_file(manifest, save_path="."):

    files = []
    for file in manifest:
        user_id = file["user_id"]
        item_id = file["item_id"]
        filename = file["name"]

        access_token = get_onedrive_token()
        headers = {"Authorization": f"Bearer {access_token}"}

        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/drive/items/{item_id}/content"
        response = requests.get(url, headers=headers, stream=True)

        if response.status_code == 200:
            os.makedirs(save_path, exist_ok=True)
            full_path = os.path.join(save_path, filename)

            with open(full_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            print(f"Downloaded: {filename}")
            files.append({
                "path" : full_path,
                "itemId" : item_id
            })
        else:
            print(f"Failed to download {filename}: {response.status_code} - {response.text}")

    return files

###############################
# Embeddings related endpoint #
###############################
def docx_to_chunks(filepath, max_words=200, overlap=0.2):

    print(f"Parsing DOCX: {filepath}")
    doc = Document(filepath)
    text = "\n".join([p.text for p in doc.paragraphs])
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    print(f"Extracted {len(text)} characters from DOCX.")
    
    chunks = []
    current_chunk = []
    word_count = 0
    max_total = max_words
    step_words = int(max_words * (1 - overlap))

    print(f"Parsing DOCX: {filepath}")
    for paragraph in paragraphs:
        words = paragraph.split()
        if word_count + len(words) > max_total:
            chunks.append(" ".join(current_chunk))
            current_chunk = words[-step_words:]  # overlap with last part
            word_count = len(current_chunk)
        else:
            current_chunk.extend(words)
            word_count += len(words)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    print(f"Split into {len(chunks)} chunks.")
    return chunks


def pdf_to_chunks(filepath, max_words=200, overlap=0.2):
    print(f"Parsing PDF: {filepath}")
    doc = fitz.open(filepath)
    text = ""

    for page in doc:
        text += page.get_text()

    print(f"Extracted {len(text)} characters from PDF.")
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    chunks = []
    current_chunk = []
    word_count = 0
    max_total = max_words
    step_words = int(max_words * (1 - overlap))

    for paragraph in paragraphs:
        words = paragraph.split()
        if word_count + len(words) > max_total:
            chunks.append(" ".join(current_chunk))
            current_chunk = words[-step_words:]  # overlap
            word_count = len(current_chunk)
        else:
            current_chunk.extend(words)
            word_count += len(words)

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    print(f"Split into {len(chunks)} chunks.")
    return chunks

def chunks_to_embeddings(chunks, model):
    embeddings = model.encode(chunks, normalize_embeddings=True)
    print(f"Generated {len(embeddings)} embeddings.")
    return list(zip(chunks, embeddings))

def upload_embeddings(embedding):

    conn = psycopg.connect(
        host=PGHOST,
        user=PGUSER,
        password=PGPASSWORD,
        dbname=PGDATABASE,
        port=int(PGPORT),
        sslmode="require",
        autocommit=True
    )

    with conn.cursor() as cur:
        for chunk_id, (chunk, emb) in enumerate(embedding["embedding"]):
            cur.execute("""
                INSERT INTO documents (id_doc, chunk_id, content, vector)
                VALUES (%s, %s, %s, %s)
            """, (
                embedding["itemId"],
                chunk_id,
                chunk,
                emb.tolist()
            ))

    print(f"Uploaded {len(embedding['embedding'])} records for doc {embedding['itemId']}.")

######################
# Maintenance points #
#####################3
def clean_files(file_paths):
    for file in file_paths:
        try:
            if os.path.exists(file['path']):
                os.remove(file['path'])
                print(f"Deleted: {file['path']}")
            else:
                print(f"File not found: {file['path']}")
        except Exception as e:
            print(f"Failed to delete {file['path']}: {e}")


#######################
# Main entrance point #
#######################

def start_sharepoint_sequence():
    sharepoint_token = get_sharepoint_token()

    print("Loading local manifest...")
    manifest = load_sharepoint_manifest()
    print("Manifest loaded")

    print("Reading remote manifest...")
    list_items = get_sharepoint_content(sharepoint_token)
    print("Remote manifest loaded")

    print("Downloading differences")
    downloaded_files = download_sharepoint_file(sharepoint_token, manifest, list_items)
    print(f"Downloaded {len(downloaded_files)} files")

    if not downloaded_files:
        print("No changes detected, aborting...")
        return
    
    print("Vectorizing new files...")
    if downloaded_files:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-mpnet-base-v2")
        embeddings = []
        for file in downloaded_files:
            print(f"Vectorizing file: {file['path']}")
            chunks = None
            if file["path"].endswith(".docx"):
                chunks = docx_to_chunks(file["path"])
            elif file["path"].endswith(".pdf"):
                chunks = pdf_to_chunks(file["path"])

            if chunks is not None:
                embedding = chunks_to_embeddings(chunks, model)
                embeddings.append({
                    "itemId" : file["itemId"],
                    "embedding" : embedding
                })
    print("All files vectorized")

    print("Uploading new vectors")
    counter = 1
    for embedding in embeddings:
        print(f"Uploading embedding {counter} of {len(embeddings)}")
        upload_embeddings(embedding)
        counter = counter + 1
    print("Embeddings uploaded")

    print("Registering local changes...")
    update_sharepoint_manifest(list_items)
    print("Local manifest updated")

    print("Cleaning local information...")
    clean_files(downloaded_files)
    print("Local server cleaned")

def start_onedrive_sequence():
    onedrive_token = get_onedrive_token()

    print("Getting users IDS")
    allowed_ids = get_onedrive_users(onedrive_token)
    print(f"Obtained {len(allowed_ids)} ids")

    print("Getting users drives content")
    manifest = []
    for id in allowed_ids:
        print(f"Reading files of {id}")
        manifest = traverse_onedrive(onedrive_token, id, manifest)
    
    print(f"Fetched {len(manifest)} items metadata")

    print("Downloading items...")
    downloaded_files = download_onedrive_file(manifest)
    print(f"Downloaded {len(downloaded_files)} files")

    if not downloaded_files:
        print("No changes detected, aborting...")
        return
    
    print("Vectorizing new files...")
    if downloaded_files:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-mpnet-base-v2")
        embeddings = []
        for file in downloaded_files:
            print(f"Vectorizing file: {file['path']}")
            chunks = None
            if file["path"].endswith(".docx"):
                chunks = docx_to_chunks(file["path"])
            elif file["path"].endswith(".pdf"):
                chunks = pdf_to_chunks(file["path"])

            if chunks is not None:
                embedding = chunks_to_embeddings(chunks, model)
                embeddings.append({
                    "itemId" : file["itemId"],
                    "embedding" : embedding
                })
    print("All files vectorized")

    print("Uploading new vectors")
    counter = 1
    for embedding in embeddings:
        print(f"Uploading embedding {counter} of {len(embeddings)}")
        upload_embeddings(embedding)
        counter = counter + 1
    print("Embeddings uploaded")


@app.route("/sharepoint-hook", methods=["GET", "POST"])
def sharepoint_hook():
    if "validationToken" in request.args:
        token = request.args["validationToken"]
        return Response(token, status=200, mimetype="text/plain")

    # Handle notifications
    try:
        data = request.get_json(force=True)
        print("Incoming notification from Microsoft Graph:")
        print(data)
        start_sharepoint_sequence()
    except Exception as e:
        print("Failed to parse request body:", e)
        return jsonify({"error": "Invalid request"}), 400

    return jsonify({"status": "received"}), 202

########
# Test #
########
def test_point():
    token = get_sharepoint_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    url = f"https://graph.microsoft.com/v1.0/subscriptions"

    response = requests.get(url, headers=headers)
    print(response.json())


"""
if not hasattr(app, 'subscription_thread_started'):
    app.subscription_thread_started = True
    thread = threading.Thread(target=subscription_renewal_loop, daemon=True)
    thread.start()

if not hasattr(app, 'onedrive_thread_started'):
    app.onedrive_thread_started = True
    thread = threading.Thread(target=onedrive_read_loop, daemon=True)
    thread.start()
"""

if __name__ == "__main__":
    start_onedrive_sequence()