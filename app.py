# app.py - FINAL VERSION WITH CORRECT RESPONSE FORMATTING
import os
import json
import psycopg2
from flask import Flask, request, jsonify, Response
from functools import wraps

app = Flask(__name__)

# --- DATABASE SETUP ---
DATABASE_URL = os.environ.get('DATABASE_URL')
ISSUER_ID = os.environ.get('ISSUER_ID')
ISSUER_SECRET = os.environ.get('ISSUER_SECRET')

def get_db_connection():
    """Creates a database connection."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def setup_database():
    """Creates the 'licenses' table if it doesn't already exist."""
    print("Checking and setting up database table...")
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # <-- CHANGE: Updated table definition to include all required fields for the future.
        # This won't affect your existing table, which you will update with the ALTER TABLE commands.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS licenses (
                id SERIAL PRIMARY KEY,
                license_key VARCHAR(255) UNIQUE NOT NULL,
                product_id VARCHAR(255) NOT NULL,
                status VARCHAR(50) DEFAULT 'available',
                entity_id VARCHAR(255),
                "numberOfSeats" INTEGER NOT NULL DEFAULT 1,
                "exp" TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                "editions" VARCHAR(100) NOT NULL DEFAULT '{"en": "Commercial"}',
                date_created TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                date_assigned TIMESTAMP WITH TIME ZONE
            );
        ''')
        conn.commit()
        cur.close()
        conn.close()
        print("Database table setup complete.")
    except Exception as e:
        print(f"FATAL: Error setting up database: {e}")

# --- AUTHENTICATION (Unchanged) ---
def check_auth(username, password):
    return username == ISSUER_ID and password == ISSUER_SECRET

def authenticate():
    return Response('Could not verify your access level...', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- ROUTES ---

@app.route("/")
def index():
    return "Cloud Zoo Issuer Callback Server is running."

# == Route for GET /get_license ==
@app.route("/get_license")
@requires_auth
def get_license():
    license_key = request.args.get("key")
    product_id_req = request.args.get("aud")

    if not license_key or not product_id_req:
        return jsonify({"description": "Missing license key or product ID."}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    # <-- CHANGE: Updated SELECT query to get the new fields from the database.
    cur.execute(
        'SELECT license_key, product_id, "numberOfSeats", "exp", "editions" FROM licenses WHERE license_key = %s AND product_id = %s;',
        (license_key, product_id_req)
    )
    license_data = cur.fetchone()
    cur.close()
    conn.close()

    if license_data:
        key, prod_id, number_of_seats, expiration, editions_str = license_data
        
        # <-- CHANGE: Building the new, correctly formatted response object.
        json_response = {
            "id": key,
            "key": key,
            "aud": prod_id,
            "iss": ISSUER_ID,
            "exp": int(expiration.timestamp()) if expiration else None,
            "numberOfSeats": number_of_seats,
            "editions": json.loads(editions_str) # Parse the JSON string from the DB
        }
        
        print(f"SUCCESS (get_license): Found license. Returning: {json.dumps(json_response)}")
        return jsonify(json_response), 200
    else:
        print(f"INFO (get_license): No valid license found for key {license_key}")
        return jsonify({"description": "License key not found for the specified product."}), 404

# == Route for POST /add_license ==
@app.route("/add_license", methods=["POST"])
@requires_auth
def add_license():
    try:
        data = request.get_json()
        if not data: return jsonify({"description": "Request body is missing"}), 400

        license_info = data.get('license', {})
        license_key, product_id, entity_id = license_info.get('key'), license_info.get('aud'), data.get('entityId')

        if not all([license_key, product_id, entity_id]):
            return jsonify({"description": "Request is missing key data or entity ID."}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        # <-- CHANGE: Fetch all data needed to build the response object.
        cur.execute(
            'SELECT status, "numberOfSeats", "exp", "editions" FROM licenses WHERE license_key = %s AND product_id = %s;',
            (license_key, product_id)
        )
        result = cur.fetchone()

        if not result:
            cur.close(), conn.close()
            return jsonify({"description": "The provided license key does not exist."}), 409

        current_status, number_of_seats, expiration, editions_str = result
        if current_status != 'available':
            cur.close(), conn.close()
            return jsonify({"description": "This license key is not available to be added."}), 409

        # Success case: Update the database
        cur.execute( "UPDATE licenses SET status = 'assigned', entity_id = %s, date_assigned = NOW() WHERE license_key = %s;", (entity_id, license_key))
        conn.commit()
        cur.close(), conn.close()

        # <-- CHANGE: Building the new, correctly formatted License Cluster response.
        license_cluster_response = {
            "licenses": [{
                "id": license_key,
                "key": license_key,
                "aud": product_id,
                "iss": ISSUER_ID,
                "exp": int(expiration.timestamp()) if expiration else None,
                "numberOfSeats": number_of_seats,
                "editions": json.loads(editions_str)
            }]
        }
        print(f"SUCCESS (add_license): Assigned {license_key} to entity {entity_id}")
        return jsonify(license_cluster_response), 200

    except Exception as e:
        print(f"FATAL ERROR in /add_license: {e}")
        return jsonify({"description": "An internal server error occurred."}), 500
        
@app.route("/remove_license", methods=["POST"])
@requires_auth
def remove_license():
    """
    Sets a license's status back to 'available' in the database.
    This is called when a user removes a license from their account.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"description": "Request body is missing"}), 400

        # Cloud Zoo sends a LicenseCluster object when removing
        license_cluster = data.get('licenseCluster', {})
        licenses_to_remove = license_cluster.get('licenses', [])

        if not licenses_to_remove:
            return jsonify({"description": "No licenses specified for removal."}), 400

        # Process each license in the cluster (often there's only one)
        conn = get_db_connection()
        cur = conn.cursor()

        for license_info in licenses_to_remove:
            license_key = license_info.get('key')
            if license_key:
                print(f"INFO: Received request to remove license {license_key}")
                # Set the license back to available and clear the entity id
                cur.execute(
                    """
                    UPDATE licenses 
                    SET status = 'available', entity_id = NULL, date_assigned = NULL 
                    WHERE license_key = %s;
                    """,
                    (license_key,)
                )
        
        conn.commit()
        cur.close()
        conn.close()

        print(f"SUCCESS: Processed removal for {len(licenses_to_remove)} license(s).")
        # A successful response has a 200 OK status code and an empty body.
        return "", 200

    except Exception as e:
        print(f"FATAL ERROR in /remove_license: {e}")
        return jsonify({"description": "An internal server error occurred."}), 500        
        
# --- Main execution point ---
setup_database()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
